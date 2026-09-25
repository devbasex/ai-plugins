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
  kiro)
    test -f "$PROJECT_DIR/.kiro/agents/ndf.json"
    jq -e '.hooks.agentSpawn[0].command' "$PROJECT_DIR/.kiro/agents/ndf.json" >/dev/null
    stop_command="$(jq -r '.hooks.stop[0].command // empty' "$PROJECT_DIR/.kiro/agents/ndf.json")"
    test -n "$stop_command"
    stop_script="$(python3 - "$stop_command" <<'PY'
import shlex
import sys

parts = shlex.split(sys.argv[1])
if len(parts) < 4 or parts[0] != "python3" or parts[2:4] != ["--runtime", "kiro"]:
    raise SystemExit(1)
print(parts[1])
PY
)"
    test -f "$stop_script"
    PLUGIN_ROOT="$REPO_ROOT/plugins/ndf" \
      python3 "$stop_script" --runtime kiro < "$REPO_ROOT/tests/runtime-smoke/fixtures/hook-stop.json" >/dev/null
    ;;
  *) echo "unknown runtime: $runtime" >&2; exit 2 ;;
esac
