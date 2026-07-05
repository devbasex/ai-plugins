#!/usr/bin/env bash
# Wait for codex / gemini review processes — monitor.py の薄いラッパ。
#
# Usage: wait-review.sh <PR> [codex|gemini|both] [--timeout SEC] [--stall-timeout SEC]
#
# 既定値:
#   timeout       1800s (= 30 min)   env MONITOR_TIMEOUT で上書き
#   stall-timeout 600s  (= 10 min)   env MONITOR_STALL で上書き
#   poll          15s                env MONITOR_POLL で上書き
#
# Exit codes は monitor.py に準拠:
#   0  OK
#   1  USAGE / IO error
#   2  TIMEOUT
#   3  NO_RESULT (プロセス終了したが result.json 未生成)
#   4  EARLY_ERROR (err.log に致命的パターン)
#   5  STALLED (err.log 進捗なし)
#   6  PIDFILE_BAD (pidfile 不正 / プロセス未起動)
#
# 旧 wait_codex / wait_gemini (sentinel + pidfile のみ) は信頼性が低かったため
# Python 側に多軸監視を集約した。本ラッパは既存呼び出し互換のために残す。

set -euo pipefail

PR=${1:?PR required}
TARGET=${2:-both}
shift 2 || shift $#

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec "$SCRIPT_DIR/monitor.py" "$PR" "$TARGET" "$@"
