#!/usr/bin/env bash
# 11 の hook の入口（hooks/<ランタイム>.json に登録したコマンドそのもの）を、同じ fixture で打って 0 で終わることを確かめる。
# 10 系では保存期間の保持（ensure-retention.sh）と待ちの通知（wait-notify.py）を直に打っていた。11.0.0 はこの 2 つを
# 持たず（11.x で移す。#3791）、同じ事象（SessionStart・Stop）の入口が fixture を受けて落ちないことだけを見る。
set -euo pipefail

runtime="${1:?runtime is required}"
: "${REPO_ROOT:=/workspace/ai-plugins}"
: "${PROJECT_DIR:=/tmp/runtime-project}"
: "${HOME:=/tmp/runtime-home}"

plugin="$REPO_ROOT/plugins/ndf"
fixtures="$REPO_ROOT/tests/runtime-smoke/fixtures"

run_hook() {
  local event="$1" fixture="$2" cmd
  cmd=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["hooks"][sys.argv[2]][0]["hooks"][0]["command"])' \
    "$plugin/hooks/$runtime.json" "$event")
  PLUGIN_ROOT="$plugin" CLAUDE_PLUGIN_ROOT="$plugin" sh -c "$cmd" < "$fixtures/$fixture" >/dev/null
}

case "$runtime" in
  claude)
    run_hook SessionStart hook-session-start.json
    run_hook Stop hook-stop.json
    ;;
  codex)
    run_hook Stop hook-stop.json
    ;;
  *) echo "unknown runtime: $runtime" >&2; exit 2 ;;
esac
