#!/usr/bin/env bash
# NDF plugin: 作業ツリー運用をこのリポジトリへ導入する。
#
#   init [--force]   宣言ファイル (.ndf/worktree.json) を作る
#   status           導入の状態を出す
#   check            宣言の状態を終了コードで返す（読み取り専用）
#   create <ブランチ> [--from <起点>]
#                    `.worktrees/<ブランチ>` に作業ツリーを作る。起点の既定は宣言の
#                    base_branch。課題の作業ツリーはミッションのブランチ（mission/<名前>）を
#                    --from に渡して切る。ミッションのブランチそのものは --from を省いて切る
#
# 作業ツリー運用の仕組みは、リポジトリ側に宣言ファイルがあるときだけ動く。
# 無ければ hook もコマンドも何も出力せず終了コード 0 で終わる。**このスクリプトは
# その入口を作る。** 他のスクリプトと違い、宣言が無い状態で意味を持つ唯一のもの。
#
# 終了コードは 0 を「処理が完了した」、1 を「処理できなかった」に割り当てる。
# check だけは状態を 0（あり）/ 2（なし）/ 3（読めない）へ分け、1 は「判定できない」を指す。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/worktree-common.sh
. "$SCRIPT_DIR/lib/worktree-common.sh" 2>/dev/null || {
  printf '%s\n' "共通ライブラリを読み込めません" >&2
  exit 1
}

SUBCOMMAND="${1:-init}"
FORCE=0
CREATE_BRANCH=
FROM_BRANCH=
shift 2>/dev/null || true
while [ "$#" -gt 0 ]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --from)
      [ "$#" -ge 2 ] && [ -n "$2" ] || { printf '%s\n' "--from に起点のブランチを渡してください" >&2; exit 1; }
      FROM_BRANCH="$2"; shift 2 ;;
    --) shift ;;
    -*) printf '知らない引数です: %s\n' "$1" >&2; exit 1 ;;
    *)
      if [ "$SUBCOMMAND" = create ] && [ -z "$CREATE_BRANCH" ]; then
        CREATE_BRANCH="$1"; shift
      else
        printf '知らない引数です: %s\n' "$1" >&2; exit 1
      fi
      ;;
  esac
done
if [ -n "$FROM_BRANCH" ] && [ "$SUBCOMMAND" != create ]; then
  printf '%s\n' "--from は create だけが受け取ります" >&2
  exit 1
fi

command -v git >/dev/null 2>&1 || { printf '%s\n' "git が要ります" >&2; exit 1; }
# 宣言の読み取りは jq を使う。無いと、書いた後の確認も status の判定もできない。
command -v jq >/dev/null 2>&1 || { printf '%s\n' "jq が要ります" >&2; exit 1; }

MAIN_DIR=$(wt_main_dir) || { printf '%s\n' "git のリポジトリの中で実行してください" >&2; exit 1; }
DECLARATION_FILE="$MAIN_DIR/$WT_DECLARATION_FILE"
# 個人の宣言を追跡から外す登録。宣言と同じディレクトリで完結させ、根の .gitignore の
# 既存の内容と並びに触れない（#495 の決定 15）。
NDF_GITIGNORE_FILE="$MAIN_DIR/.ndf/.gitignore"

SCHEMA_URL="https://raw.githubusercontent.com/devbasex/ai-plugins/main/plugins/ndf/skills/worktree/schemas/worktree.schema.json"

# check の各行に付く注記。意味（宣言由来・既定ブランチへの退避・退避先不明）を
# 名前で表す。文言を変えるときはここだけを直す。
readonly NOTE_DECLARED="（宣言）"
readonly NOTE_DEFAULT_BRANCH="（未宣言。既定ブランチ）"
readonly NOTE_UNKNOWN_FALLBACK="不明（origin/HEAD が未設定）"

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
  "version": $WT_DECLARATION_VERSION
}
JSON
  mv "$tmp" "$DECLARATION_FILE" 2>/dev/null || { rm -f "$tmp"; return 1; }
}

# 個人の宣言を追跡から外す `.ndf/.gitignore` を作る。**既にあれば触らない。**
# 書き加えた内容を消さないためで、symlink もたどらずにそのまま残す。
# 書き方は宣言と同じく、同じディレクトリの一時ファイルへ書いてから名前を付け替える。
write_local_gitignore() {
  local dir tmp
  # 壊れた symlink は `[ -e ]` が偽になる。`mv` は symlink をたどらずに置き換えるが、
  # 利用者が置いたものを消さないため `[ -L ]` でも触らない。
  if [ -e "$NDF_GITIGNORE_FILE" ] || [ -L "$NDF_GITIGNORE_FILE" ]; then
    return 0
  fi
  dir=$(dirname "$NDF_GITIGNORE_FILE")
  tmp=$(mktemp "$dir/.gitignore.XXXXXX" 2>/dev/null) || return 1
  cat >"$tmp" <<EOS
# 個人の宣言。各自の機械の値を書き、コミットしない（worktree Skill の references/declaration.md）
${WT_DECLARATION_LOCAL_FILE##*/}
EOS
  mv "$tmp" "$NDF_GITIGNORE_FILE" 2>/dev/null || { rm -f "$tmp"; return 1; }
}

do_init() {
  # --force が無ければ、書く前に宣言の状態を読む。状態は status / check と同じ関数で
  # 決める（基準を書き写すと、同じ宣言を別の状態として報告する経路が再び生まれる）。
  # refuse_symlink より前に置くのは、読むだけの枝ではリポジトリの外を書き換えないため。
  # 後に置くと、読める宣言を指す symlink で「symlink です」と断ってしまう。
  if [ "$FORCE" = 0 ]; then
    case "$(wt_declaration_state "$MAIN_DIR")" in
      present)
        # **上書きしない。** 書き加えた内容を消さないため。
        printf '宣言ファイルは既にあります: %s\n' "${DECLARATION_FILE#"$MAIN_DIR"/}"
        return 0
        ;;
      unreadable)
        # 読めない宣言は「既にある」ではない。運用は宣言を読めないと何もしないため、
        # 用意できなかったとして 1 で終わる。直すか消すかは利用者が決める。--force は
        # 形によって結果が分かれる（ディレクトリは #628）ため勧めない。
        print_declaration_line unreadable >&2
        printf '中身を直すか、書き加えた内容が要らなければ %s を消してから、もう一度 init を実行してください\n' \
          "${DECLARATION_FILE#"$MAIN_DIR"/}" >&2
        return 1
        ;;
    esac
  fi

  refuse_symlink || return 1

  write_declaration || { printf '%s\n' "宣言ファイルを書けませんでした" >&2; return 1; }

  # 書いた内容が読めることを確かめる。読めない宣言は無いものとして扱われる。
  wt_declaration "$MAIN_DIR" >/dev/null || {
    printf '%s\n' "書いた宣言ファイルを読み取れません" >&2
    return 1
  }

  # 追跡から外す登録が書けなくても、宣言そのものは作れている。init の目的は果たした
  # ため終了コードは変えず、気づけるように理由だけを標準エラーへ出す。
  write_local_gitignore || printf '%s\n' \
    "${NDF_GITIGNORE_FILE#"$MAIN_DIR"/} を書けませんでした。個人の宣言を使うなら手で登録してください" >&2

  cat <<EOS
宣言ファイルを作りました: ${DECLARATION_FILE#"$MAIN_DIR"/}

これで、主ディレクトリの編集時の案内と、セッション開始時の逸脱検知が動きます。
案内を出さないパスは組み込みの既定（issues/ docs/ 各ランタイムの設定 .serena/ .ndf/
.gitignore）を使います。主ディレクトリのブランチは既定では動かしません。稼働中の
作業ツリーへ追従させるなら follow_branch: true を足します。

**このファイルはコミットしてください。** リポジトリの設定であり、他の開発者にも
同じ運用が要ります。

ローカル環境での動作検証やテスト実行の分離を使うときは、localenv / testenv を
足します。書き方は worktree Skill の references/declaration.md にあります。

機械ごとに違う値（ポートの帯・持ち込み物・追従の有無）は ${WT_DECLARATION_LOCAL_FILE}
へ書きます。**このファイルはコミットしません。** ${NDF_GITIGNORE_FILE#"$MAIN_DIR"/} で
追跡から外してあります。
EOS
}

# --- status -----------------------------------------------------------------

# 「宣言ファイル:」の行を出す。**status と check は同じ行を出す。** 同じ状態を別の
# 言葉で書くと、どちらかの出力だけを見た利用者が別の状態と読みうる。
print_declaration_line() {
  case "$1" in
    present) printf '宣言ファイル: あり（%s）\n' "${DECLARATION_FILE#"$MAIN_DIR"/}" ;;
    unreadable) printf '宣言ファイル: 読めません（版が未対応か、JSON として壊れています）\n' ;;
    *) printf '宣言ファイル: なし。`worktree-setup.sh init` で作れます\n' ;;
  esac
}

# 個人の宣言の行を出す。**ファイルが無いときは何も出さない**（#495 の決定 16）。
# 個人の宣言を使わない利用者の出力を変えないためで、既存の出力に依存する手順と
# テストがこの変更で壊れない。**status と check は同じ行を出す。**
print_local_declaration_lines() {
  local state list= name
  state=$(wt_declaration_local_state "$MAIN_DIR") || return 0
  case "$state" in
    absent) return 0 ;;
    present) printf '個人の宣言: あり（%s）\n' "$WT_DECLARATION_LOCAL_FILE" ;;
    unreadable)
      printf '個人の宣言: 読めません（版が未対応か、JSON として壊れています。共有の宣言だけで動きます）\n'
      return 0
      ;;
    *)
      printf '個人の宣言: 使っていません（共有の宣言ファイルが無いか、読めません）\n'
      return 0
      ;;
  esac

  # 反映しなかった項目を 1 行へ並べる。無ければ行を足さない。
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    if [ -n "$list" ]; then
      list="$list, $name"
    else
      list="$name"
    fi
  done < <(wt_declaration_local_ignored "$MAIN_DIR")
  [ -n "$list" ] && printf '個人の宣言で反映しない項目: %s\n' "$list"
  return 0
}

do_status() {
  printf '主ディレクトリ: %s\n' "$MAIN_DIR"

  print_declaration_line "$(wt_declaration_state "$MAIN_DIR")"
  print_local_declaration_lines

  # 追跡されたままの個人の宣言は、各自の値が差分に載る。**status だけが出す。**
  # check は分岐に使われるため、終了コードに現れない行を増やさない。
  if [ -e "$MAIN_DIR/$WT_DECLARATION_LOCAL_FILE" ] &&
     ! git -C "$MAIN_DIR" check-ignore -q "$WT_DECLARATION_LOCAL_FILE" 2>/dev/null; then
    printf '個人の宣言の登録: なし。%s へ %s を足してください\n' \
      "${NDF_GITIGNORE_FILE#"$MAIN_DIR"/}" "${WT_DECLARATION_LOCAL_FILE##*/}"
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

# --- check ------------------------------------------------------------------

# 宣言のキーを行へ出す。**名前の実在は確かめない。** 確かめる wt_branch_exists は
# origin へ問い合わせることがあり、起動のたびに通信が走る。実在の確認は、その名前を
# 使う時点の wt_base_branch / wt_production_branch が持つ。
print_branch_line() {
  local label="$1" key="$2" decl="$3" name= fallback
  if [ -n "$decl" ]; then
    name=$(_wt_declaration_string "$decl" "$key")
  fi
  if [ -n "$name" ]; then
    printf '%s: %s%s\n' "$label" "$name" "$NOTE_DECLARED"
  elif fallback=$(wt_default_branch "$MAIN_DIR"); then
    printf '%s: %s%s\n' "$label" "$fallback" "$NOTE_DEFAULT_BRANCH"
  else
    printf '%s: %s\n' "$label" "$NOTE_UNKNOWN_FALLBACK"
  fi
}

do_check() {
  local state decl=
  state=$(wt_declaration_state "$MAIN_DIR") || return 1
  [ "$state" = present ] && decl=$(wt_declaration "$MAIN_DIR")

  print_declaration_line "$state"
  print_local_declaration_lines
  print_branch_line "開発の起点" base_branch "$decl"
  print_branch_line "本番のチャネル" production_branch "$decl"

  case "$state" in
    present) return 0 ;;
    absent) return 2 ;;
    *) return 3 ;;
  esac
}

# --- create -----------------------------------------------------------------

# 作業ツリーを `.worktrees/<ブランチ>` に作り、そのパスを出力する。
#
# 起点は --from があればそれ、無ければ宣言の base_branch（無ければ既定ブランチ）で
# ある。**宣言の base_branch は書き換えない。** ミッションのブランチは develop から
# 切り、課題の作業ツリーは --from にミッションのブランチを渡して切る。
# 起点の名前が origin にもローカルにも無いときは作らずに 1 で終わる。
do_create() {
  local branch="$CREATE_BRANCH" base target start
  if [ -z "$branch" ]; then
    printf '%s\n' "使い方: worktree-setup.sh create <ブランチ> [--from <起点>]" >&2
    return 1
  fi
  git check-ref-format --branch "$branch" >/dev/null 2>&1 || {
    printf 'ブランチ名として使えません: %s\n' "$branch" >&2
    return 1
  }
  if ! git -C "$MAIN_DIR" check-ignore -q "$WT_WORKTREE_DIR/" 2>/dev/null; then
    printf '%s/ が .gitignore に登録されていません。作る前に登録してください\n' "$WT_WORKTREE_DIR" >&2
    return 1
  fi
  target="$MAIN_DIR/$WT_WORKTREE_DIR/$branch"
  if [ -e "$target" ]; then
    printf '作業ツリーの置き場所が既にあります: %s\n' "$target" >&2
    return 1
  fi

  # 取得に失敗しても止めない。origin の無いリポジトリでもローカルのブランチから切れる。
  GIT_TERMINAL_PROMPT=0 git -C "$MAIN_DIR" fetch -q origin >/dev/null 2>&1 || true

  if [ -n "$FROM_BRANCH" ]; then
    base="$FROM_BRANCH"
    wt_branch_exists "$MAIN_DIR" "$base" || {
      printf '起点のブランチがありません: %s\n' "$base" >&2
      return 1
    }
  else
    base=$(wt_base_branch "$MAIN_DIR") || return 1
  fi
  start="origin/$base"
  git -C "$MAIN_DIR" show-ref --verify --quiet "refs/remotes/origin/$base" || start="$base"

  if git -C "$MAIN_DIR" show-ref --verify --quiet "refs/heads/$branch"; then
    git -C "$MAIN_DIR" worktree add -q "$target" "$branch" >&2 || return 1
  else
    git -C "$MAIN_DIR" worktree add -q --no-track -b "$branch" "$target" "$start" >&2 || return 1
  fi
  printf '作業ツリー: %s\n' "$target"
  printf '起点: %s\n' "$base"
}

case "$SUBCOMMAND" in
  init) do_init ;;
  status) do_status ;;
  check) do_check ;;
  create) do_create ;;
  *)
    printf '使い方: worktree-setup.sh <init|status|check> [--force] | create <ブランチ> [--from <起点>]\n' >&2
    exit 1
    ;;
esac
