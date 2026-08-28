# shellcheck shell=bash
# 収束ループ共通: 一時ディレクトリ決定ヘルパ。
#
# Usage:
#   . "$LIB/_tmpdir.sh"
#   TMP_DIR=$(resolve_tmpdir CROSS_REFACTORING_TMP_DIR .cross_refactoring)
#
# 優先順位:
#   1. 第 1 引数で渡した環境変数（明示指定）
#   2. <worktree root>/<第 2 引数>（作業ディレクトリ内。gemini の作業領域制約を根本回避）
#
# 作業ディレクトリの外に一時ファイルを置くと gemini が書き込みを拒否されるため、
# 既定は必ず作業ディレクトリの中にする。

resolve_tmpdir() {
  local env_name=${1:?env name required}
  local dir_name=${2:?dir name required}
  local explicit=${!env_name:-}

  if [ -n "$explicit" ]; then
    mkdir -p "$explicit"
    echo "$explicit"
    return
  fi
  # サブディレクトリから呼ばれた場合でも作業ディレクトリの root を正しく特定する
  local root
  root="$(git rev-parse --show-toplevel 2>/dev/null)" || root="$PWD"
  mkdir -p "$root/$dir_name"
  echo "$root/$dir_name"
}
