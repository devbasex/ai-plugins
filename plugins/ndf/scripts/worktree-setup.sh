#!/usr/bin/env bash
# NDF plugin: 作業ツリー運用をこのリポジトリへ導入する。
#
#   init [--force]   宣言ファイル (.ndf/worktree.json) を作る
#   status           導入の状態を出す
#
# 作業ツリー運用の仕組みは、リポジトリ側に宣言ファイルがあるときだけ動く。
# 無ければ hook もコマンドも何も出力せず終了コード 0 で終わる。**このスクリプトは
# その入口を作る。** 他のスクリプトと違い、宣言が無い状態で意味を持つ唯一のもの。
#
# 終了コードは 0 を「処理が完了した」、1 を「処理できなかった」に割り当てる。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/worktree-common.sh
. "$SCRIPT_DIR/lib/worktree-common.sh" 2>/dev/null || {
  printf '%s\n' "共通ライブラリを読み込めません" >&2
  exit 1
}

SUBCOMMAND="${1:-init}"
FORCE=0
shift 2>/dev/null || true
while [ "$#" -gt 0 ]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --) shift ;;
    *) printf '知らない引数です: %s\n' "$1" >&2; exit 1 ;;
  esac
done

command -v git >/dev/null 2>&1 || { printf '%s\n' "git が要ります" >&2; exit 1; }
# 宣言の読み取りは jq を使う。無いと、書いた後の確認も status の判定もできない。
command -v jq >/dev/null 2>&1 || { printf '%s\n' "jq が要ります" >&2; exit 1; }

MAIN_DIR=$(wt_main_dir) || { printf '%s\n' "git のリポジトリの中で実行してください" >&2; exit 1; }
DECLARATION_FILE="$MAIN_DIR/.ndf/worktree.json"

SCHEMA_URL="https://raw.githubusercontent.com/devbasex/ai-plugins/main/plugins/ndf/skills/worktree/schemas/worktree.schema.json"

# --- init -------------------------------------------------------------------

# 書き先が symlink なら断る。たどると、リポジトリの外を指した状態で --force を
# 実行したときに外のファイルを書き換えてしまう。
refuse_symlink() {
  local ndf_dir="$MAIN_DIR/.ndf"
  if [ -L "$ndf_dir" ]; then
    printf '%s\n' ".ndf が symlink です。たどらずに終わります" >&2
    return 1
  fi
  if [ -L "$DECLARATION_FILE" ]; then
    printf '%s\n' "宣言ファイルが symlink です。たどらずに終わります" >&2
    return 1
  fi
  return 0
}

# 最小の宣言を書く。案内を出さないパスは組み込みの既定を使うため、ここでは
# 書かない。差し替えるときだけ guard.allow_paths を足す（declaration.md）。
#
# 同じディレクトリの一時ファイルへ書いてから名前を付け替える。書いている途中で
# 落ちても、中途半端な宣言が残らない。
write_declaration() {
  local dir tmp
  dir=$(dirname "$DECLARATION_FILE")
  mkdir -p "$dir" 2>/dev/null || return 1
  tmp=$(mktemp "$dir/.worktree.json.XXXXXX" 2>/dev/null) || return 1
  cat >"$tmp" <<JSON
{
  "\$schema": "$SCHEMA_URL",
  "version": 1
}
JSON
  mv "$tmp" "$DECLARATION_FILE" 2>/dev/null || { rm -f "$tmp"; return 1; }
}

do_init() {
  refuse_symlink || return 1

  if [ -e "$DECLARATION_FILE" ] && [ "$FORCE" = 0 ]; then
    # **上書きしない。** 書き加えた内容を消さないため。
    printf '宣言ファイルは既にあります: %s\n' "${DECLARATION_FILE#"$MAIN_DIR"/}"
    return 0
  fi

  write_declaration || { printf '%s\n' "宣言ファイルを書けませんでした" >&2; return 1; }

  # 書いた内容が読めることを確かめる。読めない宣言は無いものとして扱われる。
  wt_declaration "$MAIN_DIR" >/dev/null || {
    printf '%s\n' "書いた宣言ファイルを読み取れません" >&2
    return 1
  }

  cat <<EOS
宣言ファイルを作りました: ${DECLARATION_FILE#"$MAIN_DIR"/}

これで、主ディレクトリの編集時の案内と、セッション開始時の逸脱検知・ブランチ追従が
動きます。案内を出さないパスは組み込みの既定（issues/ docs/ 各ランタイムの設定
.serena/ .ndf/ .gitignore）を使います。

**このファイルはコミットしてください。** リポジトリの設定であり、他の開発者にも
同じ運用が要ります。

ローカル環境での動作検証やテスト実行の分離を使うときは、localenv / testenv を
足します。書き方は worktree Skill の references/declaration.md にあります。
EOS
}

# --- status -----------------------------------------------------------------

do_status() {
  printf '主ディレクトリ: %s\n' "$MAIN_DIR"

  if wt_declaration "$MAIN_DIR" >/dev/null; then
    printf '宣言ファイル: あり（%s）\n' "${DECLARATION_FILE#"$MAIN_DIR"/}"
  elif [ -e "$DECLARATION_FILE" ]; then
    printf '宣言ファイル: 読めません（版が未対応か、JSON として壊れています）\n'
  else
    printf '宣言ファイル: なし。`worktree-setup.sh init` で作れます\n'
  fi

  if git -C "$MAIN_DIR" check-ignore -q "$WT_WORKTREE_DIR/" 2>/dev/null; then
    printf '%s/ の登録: あり\n' "$WT_WORKTREE_DIR"
  else
    printf '%s/ の登録: なし。作業ツリーを作る前に .gitignore へ登録します\n' "$WT_WORKTREE_DIR"
  fi

  # `grep -c` は該当なしで 0 を出しつつ終了コード 1 を返す。退避を足すと
  # 数が二重に出る。空でないときだけ数える。
  local listing count=0
  listing=$(wt_dev_worktrees "$MAIN_DIR")
  if [ -n "$listing" ]; then
    count=$(printf '%s\n' "$listing" | grep -c '^')
  fi
  printf '開発用の作業ツリー: %s 個\n' "$count"
}

case "$SUBCOMMAND" in
  init) do_init ;;
  status) do_status ;;
  *)
    printf '使い方: worktree-setup.sh <init|status> [--force]\n' >&2
    exit 1
    ;;
esac
