# shellcheck shell=bash
# cross-review 共通: tmp ディレクトリ決定ヘルパ。
#
# Usage:
#   . "$(dirname "$0")/_tmpdir.sh"
#   TMP_DIR=$(tmpdir)
#
# 優先順位:
#   1. 環境変数 CROSS_REVIEW_TMP_DIR (明示)
#   2. ~/.gemini/tmp/<cwd-basename>/ (gemini の workspace 制約を回避する公式 path)
#   3. /tmp/ (フォールバック)

tmpdir() {
  if [ -n "${CROSS_REVIEW_TMP_DIR:-}" ]; then
    mkdir -p "$CROSS_REVIEW_TMP_DIR"
    echo "$CROSS_REVIEW_TMP_DIR"
    return
  fi
  local base
  base=$(basename "$PWD")
  local gemini_root="$HOME/.gemini/tmp"
  if [ -d "$gemini_root" ] && [ -n "$base" ]; then
    mkdir -p "$gemini_root/$base"
    echo "$gemini_root/$base"
    return
  fi
  echo "/tmp"
}
