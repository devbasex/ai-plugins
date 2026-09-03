# shellcheck shell=bash
# cross-review 共通: tmp ディレクトリ決定ヘルパ。
#
# 実体は [lib/_tmpdir.sh](lib/_tmpdir.sh) の `resolve_tmpdir` にある。
# ここは cross-review 固有の環境変数名とディレクトリ名を束ねるだけの薄い層で、
# 既存の呼び出し (`. "$(dirname "$0")/_tmpdir.sh"` → `tmpdir()`) を維持する。
#
# Usage:
#   . "$(dirname "$0")/_tmpdir.sh"
#   TMP_DIR=$(tmpdir)
#
# 優先順位:
#   1. 環境変数 CROSS_REVIEW_TMP_DIR (明示)
#   2. <worktree-root>/.cross_review/ (worktree 内。作業領域を 1 つに保つため)

# shellcheck source=lib/_tmpdir.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/_tmpdir.sh"

tmpdir() {
  resolve_tmpdir CROSS_REVIEW_TMP_DIR .cross_review
}
