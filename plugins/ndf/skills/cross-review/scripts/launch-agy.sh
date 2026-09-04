#!/usr/bin/env bash
# cross-review agy launcher（互換のための薄い委譲）。
#
# Usage: launch-agy.sh <STATE_PR> <ROUND>
#
# **実体は `launch-reviewer.sh` にある。** 母集合が 4 者になったため、起動の手順は
# ランタイム名を引数に取る 1 本へ寄せた。この名前は既存の呼び出し側（手順書・監視の
# 設定）が使い続けられるように残す。
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec "$SCRIPT_DIR/launch-reviewer.sh" agy "$@"
