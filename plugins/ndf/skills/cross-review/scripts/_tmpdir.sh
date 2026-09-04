# shellcheck shell=bash
# cross-review 共通: tmp ディレクトリ決定ヘルパ。
#
# 実体は [../../../scripts/lib/_tmpdir.sh](../../../scripts/lib/_tmpdir.sh) の
# `resolve_tmpdir` にある（プラグインルート直下の共通層）。
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

# **`cd` で登らない。** Kiro CLI の symlink の手前へ字句で戻るため、文字列のまま渡して
# カーネルに解決させる（バッチ 06 の契約）。
# shellcheck source=../../../scripts/lib/_tmpdir.sh
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../../../scripts/lib/_tmpdir.sh"

tmpdir() {
  resolve_tmpdir CROSS_REVIEW_TMP_DIR .cross_review
}
