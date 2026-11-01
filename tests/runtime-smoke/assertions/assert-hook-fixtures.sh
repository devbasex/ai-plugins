#!/usr/bin/env bash
set -euo pipefail

runtime="${1:?runtime is required}"
: "${REPO_ROOT:=/workspace/ai-plugins}"
: "${PROJECT_DIR:=/tmp/runtime-project}"
: "${HOME:=/tmp/runtime-home}"

case "$runtime" in
  claude)
    PLUGIN_ROOT="$REPO_ROOT/plugins/ndf" CLAUDE_PLUGIN_ROOT="$REPO_ROOT/plugins/ndf" \
      bash "$REPO_ROOT/plugins/ndf/scripts/ensure-retention.sh" < "$REPO_ROOT/tests/runtime-smoke/fixtures/hook-session-start.json"
    PLUGIN_ROOT="$REPO_ROOT/plugins/ndf" CLAUDE_PLUGIN_ROOT="$REPO_ROOT/plugins/ndf" \
      python3 "$REPO_ROOT/plugins/ndf/scripts/wait-notify.py" --runtime claude < "$REPO_ROOT/tests/runtime-smoke/fixtures/hook-stop.json" >/dev/null
    ;;
  codex)
    PLUGIN_ROOT="$REPO_ROOT/plugins/ndf" \
      python3 "$REPO_ROOT/plugins/ndf/scripts/wait-notify.py" --runtime codex < "$REPO_ROOT/tests/runtime-smoke/fixtures/hook-stop.json" >/dev/null
    ;;
  *) echo "unknown runtime: $runtime" >&2; exit 2 ;;
esac
