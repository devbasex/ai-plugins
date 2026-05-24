# shellcheck shell=bash
# cross-review 共通: tmp ディレクトリ決定ヘルパ。
#
# Usage:
#   . "$(dirname "$0")/_tmpdir.sh"
#   TMP_DIR=$(tmpdir)
#
# 優先順位:
#   1. 環境変数 CROSS_REVIEW_TMP_DIR (明示)
#   2. $PWD/.cross_review/ (worktree 内。gemini の workspace 制約を根本回避)
#   3. /tmp/ (フォールバック)

tmpdir() {
  if [ -n "${CROSS_REVIEW_TMP_DIR:-}" ]; then
    mkdir -p "$CROSS_REVIEW_TMP_DIR"
    echo "$CROSS_REVIEW_TMP_DIR"
    return
  fi
  # サブディレクトリから呼ばれた場合でも worktree root を正しく特定する
  local root
  root="$(git rev-parse --show-toplevel 2>/dev/null)" || root="$PWD"
  mkdir -p "$root/.cross_review"
  echo "$root/.cross_review"
}
