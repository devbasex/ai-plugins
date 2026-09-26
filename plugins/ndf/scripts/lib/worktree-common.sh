#!/usr/bin/env bash
# NDF plugin: 作業ツリー運用の判定を集めた共通ライブラリ。
#
# 入口のスクリプト (worktree-guard.sh / worktree-session.sh など) は入力の受け取りと
# 出力の整形だけを行い、判定はすべてこのファイルの関数が持つ。同じ判定を 3 ランタイム
# 分の入口へ書くと片方だけが古くなるため、テストもこの層に対して書く。
#
# このファイルは source して使う。単体で実行しても何も起きない。
# 依存は bash と git、宣言ファイルを読むときだけ jq。無い場合は各関数が 1 を返す。

# 主ディレクトリで編集しても案内を出さないパスの既定。
# 宣言ファイルの guard.allow_paths が指定されていればそちらが優先する。
# 末尾が `/` の項目は前方一致、それ以外は完全一致とその配下を許可する。
WT_DEFAULT_ALLOW_PATHS=(
  "issues/"
  "docs/"
  ".claude/"
  ".codex/"
  ".kiro/"
  ".agents/"
  ".serena/"
  ".ndf/"
  ".gitignore"
)

# 読み取れる宣言ファイルの版。知らない版は読まずに終わる。
WT_DECLARATION_VERSION=1

# 宣言ファイルの主ディレクトリからの相対パス。
WT_DECLARATION_FILE=".ndf/worktree.json"

# 個人の宣言ファイルの主ディレクトリからの相対パス。共有の宣言の隣に置き、追跡しない
# （#495 の決定 6）。反映する項目は `_wt_local_overrides` の許可一覧が決める。
WT_DECLARATION_LOCAL_FILE=".ndf/worktree.local.json"

# 開発用の作業ツリーを置くディレクトリ (主ディレクトリからの相対)。
WT_WORKTREE_DIR=".worktrees"

# 逸脱検知でパスを並べる上限。超えた分は件数へ丸める。
WT_DIRTY_LIST_MAX=20

# 誘導の対象になる tool 名。ランタイムごとに名乗りが違うため、ここで 1 箇所に
# まとめる。hook の matcher もこの一覧から作る（両方に書くと片方が古くなる）。
#   編集系 — Claude Code は Edit / Write、Kiro CLI は fs_write、agy は write_to_file と
#            replace_file_content。ほかのランタイムが名乗る write_file / replace なども
#            同じ一覧へ並べる。**agy の `write_file` は権限の名前であって tool の名前では
#            ない。** hook が受け取る `toolCall.name` は `write_to_file` である（実測）
#   パッチ系 — Codex CLI はパッチ本文で編集先を渡す
#   シェル系 — 書き込みを伴うコマンドの形から編集先を推定する
WT_EDIT_TOOLS="Edit|MultiEdit|Write|NotebookEdit|fs_write|edit_file|write_file|str_replace_editor|replace|write_to_file|replace_file_content"
WT_PATCH_TOOLS="apply_patch"
WT_SHELL_TOOLS="Bash|shell|execute_bash|local_shell|run_command|run_shell_command"

# hook の matcher に書く正規表現を出力する。
wt_tool_matcher() {
  printf '%s|%s|%s\n' "$WT_EDIT_TOOLS" "$WT_PATCH_TOOLS" "$WT_SHELL_TOOLS"
}

# --- 補助 -------------------------------------------------------------------

# 標準入力を 1 行 1 要素で配列 WT_LINES へ読み込む。
# `mapfile` / `readarray` は bash 4 以降にしかない。macOS が標準で持つ bash は
# 3.2 で、そこで呼ぶと 127 を返して読み込みが空になる。hook は失敗しても黙って
# 終わるため、案内が出ない形で壊れる。
#
# 使い方: _wt_read_lines < <(コマンド); arr=("${WT_LINES[@]+"${WT_LINES[@]}"}")
_wt_read_lines() {
  WT_LINES=()
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    WT_LINES+=("$line")
  done
}

# --- 位置の解決 -------------------------------------------------------------

# `base` を起点に相対パスを実体の絶対パスへ直す。存在しなければ 1 を返す。
# `git -C <dir> rev-parse` が返すパスは <dir> からの相対になるため、現在地を
# 起点にすると解決できない。
_wt_abs_in() {
  local base="${1:-}" path="${2:-}"
  [ -n "$path" ] || return 1
  (cd "$base" 2>/dev/null && cd "$path" 2>/dev/null && pwd -P) || return 1
}

# 相対パスを実体の絶対パスへ直す。存在しなければ 1 を返す。
_wt_abs() {
  _wt_abs_in "$PWD" "${1:-}"
}

# git への問い合わせを、同じディレクトリについて 1 回に留めるためのキャッシュ。
# 呼び出しは `git rev-parse --git-dir --git-common-dir` と
# `git rev-parse --show-superproject-working-tree` の 2 回まで。
# 引数を省くと現在地を解決する。キャッシュは解決したディレクトリを鍵にする。
_wt_resolve() {
  local dir="${1:-$PWD}"
  [ "${_WT_RESOLVED_DIR:-}" = "$dir" ] && return "${_WT_RESOLVE_RC:-0}"
  _WT_RESOLVED_DIR="$dir"
  _WT_RESOLVE_RC=1
  _WT_MAIN_DIR=""
  _WT_IN_WORKTREE=1

  local dirs git_dir git_common super
  dirs=$(git -C "$dir" rev-parse --git-dir --git-common-dir 2>/dev/null) || return 1
  git_dir=$(printf '%s\n' "$dirs" | sed -n '1p')
  git_common=$(printf '%s\n' "$dirs" | sed -n '2p')
  git_dir=$(_wt_abs_in "$dir" "$git_dir") || return 1
  git_common=$(_wt_abs_in "$dir" "$git_common") || return 1

  # サブモジュールの中でも 2 つの git ディレクトリは異なる。作業ツリーと
  # 取り違えないよう、上位リポジトリを持つ場合は通常のリポジトリとして扱う。
  super=$(git -C "$dir" rev-parse --show-superproject-working-tree 2>/dev/null)
  if [ -n "$super" ]; then
    _WT_MAIN_DIR=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || return 1
    _WT_MAIN_DIR=$(_wt_abs "$_WT_MAIN_DIR") || return 1
    _WT_IN_WORKTREE=1
    _WT_RESOLVE_RC=0
    return 0
  fi

  if [ "$git_dir" != "$git_common" ]; then
    _WT_IN_WORKTREE=0
  fi
  _WT_MAIN_DIR=$(dirname "$git_common")
  _WT_RESOLVE_RC=0
  return 0
}

# 主ディレクトリの絶対パスを出力する。リポジトリの外では 1 を返す。
# 引数を省くと現在地から解決する。**対象を引数で受けるコマンドは対象を渡す。**
# 現在地から解決すると、別のリポジトリから実行したときに、対象とは違う
# リポジトリの宣言ファイル・台帳・ポートの帯で動く。
wt_main_dir() {
  _wt_resolve "${1:-$PWD}" || return 1
  [ -n "$_WT_MAIN_DIR" ] || return 1
  printf '%s\n' "$_WT_MAIN_DIR"
}

# 作業ツリーの中なら 0、主ディレクトリとサブモジュールの中なら 1 を返す。
# 引数を省くと現在地から解決する。
wt_in_worktree() {
  _wt_resolve "${1:-$PWD}" || return 1
  return "$_WT_IN_WORKTREE"
}

# 案内を出さないパスを 1 行 1 件で出力する。
# 引数は wt_declaration の出力。空や未指定なら既定を返す。
wt_allow_paths() {
  local decl="${1:-}"
  # 空の配列は「何も許可しない」という指定である。出力が空であることと
  # 項目が無いことを区別するため、既定へ戻すかは配列の有無で決める。
  if [ -n "$decl" ] && command -v jq >/dev/null 2>&1 &&
     printf '%s' "$decl" | jq -e '(.guard.allow_paths | type) == "array"' >/dev/null 2>&1; then
    printf '%s' "$decl" | jq -r '.guard.allow_paths | .[]' 2>/dev/null
    return 0
  fi
  printf '%s\n' "${WT_DEFAULT_ALLOW_PATHS[@]}"
}

# --- パスの判定 -------------------------------------------------------------

# 主ディレクトリからの相対パスが許可一覧に該当すれば 0 を返す。
# 使い方: wt_is_allowed_path <相対パス> <許可項目>...
wt_is_allowed_path() {
  local rel="${1:-}" entry
  [ -n "$rel" ] || return 1
  shift || true
  for entry in "$@"; do
    [ -n "$entry" ] || continue
    case "$entry" in
      */)
        # ディレクトリそのものを指す形も許可する。`cp x docs/` の書き込み先は
        # 正規化の途中で末尾のスラッシュが落ち、`docs` として渡ってくる。
        [ "$rel" = "${entry%/}" ] && return 0
        case "$rel" in "$entry"*) return 0 ;; esac
        ;;
      *)
        [ "$rel" = "$entry" ] && return 0
        case "$rel" in "$entry"/*) return 0 ;; esac
        ;;
    esac
  done
  return 1
}

# 宣言に書かれた相対パスが、主ディレクトリと作業ツリーの中に収まるかを見る。
# 宣言の誤りで外側を読み書きしないよう、絶対パスと上位への移動を弾く。
wt_is_safe_relative() {
  case "${1:-}" in
    "" | /*) return 1 ;;
    "." | "..") return 1 ;;
    ../* | */.. | */../*) return 1 ;;
    "~"*) return 1 ;;
  esac
  return 0
}

# Compose のプロジェクト名を作る。実行系は名前を小文字へ揃え、`a-z0-9_-` 以外を
# 落としてから使う。基のディレクトリ名をそのまま渡すと、大文字や記号を含むとき
# 稼働中のコンテナを見つけられない。
wt_compose_project() {
  local name="${1:-}"
  [ -n "$name" ] || return 1
  name=$(printf '%s' "$name" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -e 's/[^a-z0-9_-]//g' -e 's/^[-_]*//')
  [ -n "$name" ] || return 1
  printf '%s\n' "$name"
}

# 絶対パスを主ディレクトリからの相対パスへ直す。外を指すなら 1 を返す。
wt_relative_to_main() {
  local path="${1:-}" main_dir="${2:-}"
  [ -n "$path" ] && [ -n "$main_dir" ] || return 1
  case "$path" in
    "$main_dir") printf '.\n'; return 0 ;;
    "$main_dir"/*) printf '%s\n' "${path#"$main_dir"/}"; return 0 ;;
    *) return 1 ;;
  esac
}

# --- パスの正規化 -----------------------------------------------------------

# tool から渡されたパスを絶対パスへ直す。まだ存在しないパスでも、実在する
# 最も近い上位ディレクトリまでを実体解決してから残りを継ぎ足す。
# `.` と `..` を字面で畳む。実体解決は上位ディレクトリの存在を要するため、
# 存在しないパスでは `..` が残ってしまう。残ると、前方一致での「配下か」の
# 判定をすり抜ける（`<対象>/a/../../外` が `<対象>/` で始まって見える）。
_wt_lexical_normalize() {
  local path="$1" part out="" glob_was_off=0
  # 分割のための展開でパス名展開が走らないようにする。`*` や `?` を含む
  # パスが、実在するファイルの名前へ化けてしまう。
  case "$-" in *f*) glob_was_off=1 ;; esac
  set -f
  local IFS=/
  # shellcheck disable=SC2086
  set -- $path
  [ "$glob_was_off" = 1 ] || set +f
  for part in "$@"; do
    case "$part" in
      ""|.) continue ;;
      ..) out=${out%/*} ;;
      *) out="$out/$part" ;;
    esac
  done
  printf '%s\n' "${out:-/}"
}

wt_normalize_path() {
  local path="${1:-}" cwd="${2:-$PWD}" suffix="" dir abs
  [ -n "$path" ] || return 1
  case "$path" in /*) ;; *) path="$cwd/$path" ;; esac
  path=$(_wt_lexical_normalize "$path")
  dir="$path"
  while [ -n "$dir" ] && [ "$dir" != "/" ]; do
    if abs=$(_wt_abs "$dir"); then
      if [ -n "$suffix" ]; then
        printf '%s/%s\n' "$abs" "$suffix"
      else
        printf '%s\n' "$abs"
      fi
      return 0
    fi
    if [ -n "$suffix" ]; then
      suffix="$(basename "$dir")/$suffix"
    else
      suffix="$(basename "$dir")"
    fi
    dir=$(dirname "$dir")
  done
  printf '%s\n' "$path"
}

# --- 分けたファイルの読み込み ------------------------------------------------
#
# 残りの判定は同じディレクトリの 7 本が持つ。自分の位置からの相対で指し、`cd` で戻ってから
# `pwd` を取る形は採らない（`lock-common.sh` を指すときと同じ理由。Kiro CLI の配置で
# プラグインルートを外す）。関数どうしの呼び出しは実行時に解決されるため、読む順序に
# 定義の制約はない。**1 本でも読めなければ 1 を返す。** 呼び出し側は `|| exit 0` で抜ける。
_wt_lib_dir=$(dirname "${BASH_SOURCE[0]}")
# shellcheck source=worktree-declaration.sh
. "$_wt_lib_dir/worktree-declaration.sh" || { unset _wt_lib_dir; return 1; }
# shellcheck source=worktree-branch.sh
. "$_wt_lib_dir/worktree-branch.sh" || { unset _wt_lib_dir; return 1; }
# shellcheck source=worktree-shell-lex.sh
. "$_wt_lib_dir/worktree-shell-lex.sh" || { unset _wt_lib_dir; return 1; }
# shellcheck source=worktree-write-target.sh
. "$_wt_lib_dir/worktree-write-target.sh" || { unset _wt_lib_dir; return 1; }
# shellcheck source=worktree-write-target-scan.sh
. "$_wt_lib_dir/worktree-write-target-scan.sh" || { unset _wt_lib_dir; return 1; }
# shellcheck source=worktree-write-target-track.sh
. "$_wt_lib_dir/worktree-write-target-track.sh" || { unset _wt_lib_dir; return 1; }
# shellcheck source=worktree-registry.sh
. "$_wt_lib_dir/worktree-registry.sh" || { unset _wt_lib_dir; return 1; }
unset _wt_lib_dir
