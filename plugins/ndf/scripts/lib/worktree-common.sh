#!/usr/bin/env bash
# NDF plugin: 作業ツリー運用の判定を集めた共通ライブラリ。
#
# 入口のスクリプト (worktree-session.sh / worktree-setup.sh など) は入力の受け取りと
# 出力の整形だけを行い、判定はすべてこのファイルの関数が持つ。同じ判定を 3 ランタイム
# 分の入口へ書くと片方だけが古くなるため、テストもこの層に対して書く。
#
# 編集時の guard（書き込み先の推定・許可パス・Tool の名前の一覧）は hook の 1 本のエントリポイント
# （hook.py。本体は hook_lib/worktree.py と hook_lib/write_target.py）が持つ（#1142 の決定 20）。
#
# このファイルは source して使う。単体で実行しても何も起きない。
# 依存は bash と git、宣言ファイルを読むときだけ jq。無い場合は各関数が 1 を返す。

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

# --- パスの判定 -------------------------------------------------------------

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
# 残りの判定は同じディレクトリの 3 本が持つ。自分の位置からの相対で指し、`cd` で戻ってから
# `pwd` を取る形は採らない（`lock-common.sh` を指すときと同じ理由。Kiro CLI の配置で
# プラグインルートを外す）。関数どうしの呼び出しは実行時に解決されるため、読む順序に
# 定義の制約はない。**1 本でも読めなければ 1 を返す。** 呼び出し側は `|| exit 0` で抜ける。
_wt_lib_dir=$(dirname "${BASH_SOURCE[0]}")
# shellcheck source=worktree-declaration.sh
. "$_wt_lib_dir/worktree-declaration.sh" || { unset _wt_lib_dir; return 1; }
# shellcheck source=worktree-branch.sh
. "$_wt_lib_dir/worktree-branch.sh" || { unset _wt_lib_dir; return 1; }
# shellcheck source=worktree-registry.sh
. "$_wt_lib_dir/worktree-registry.sh" || { unset _wt_lib_dir; return 1; }
unset _wt_lib_dir
