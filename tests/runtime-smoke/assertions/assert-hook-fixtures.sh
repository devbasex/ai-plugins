#!/usr/bin/env bash
set -euo pipefail

runtime="${1:?runtime is required}"
: "${REPO_ROOT:=/workspace/ai-plugins}"
: "${PROJECT_DIR:=/tmp/runtime-project}"
: "${HOME:=/tmp/runtime-home}"

case "$runtime" in
  claude)
    PLUGIN_ROOT="$REPO_ROOT/plugins/ndf-claude" CLAUDE_PLUGIN_ROOT="$REPO_ROOT/plugins/ndf-claude" \
      bash "$REPO_ROOT/plugins/ndf-claude/scripts/ensure-retention.sh" < "$REPO_ROOT/tests/runtime-smoke/fixtures/hook-session-start.json"
    PLUGIN_ROOT="$REPO_ROOT/plugins/ndf-claude" CLAUDE_PLUGIN_ROOT="$REPO_ROOT/plugins/ndf-claude" \
      node "$REPO_ROOT/plugins/ndf-claude/scripts/slack-notify.js" session_end < "$REPO_ROOT/tests/runtime-smoke/fixtures/hook-stop.json" >/dev/null
    ;;
  codex)
    PLUGIN_ROOT="$REPO_ROOT/plugins/ndf-codex" \
      node "$REPO_ROOT/plugins/ndf-codex/scripts/codex-slack-notify.js" < "$REPO_ROOT/tests/runtime-smoke/fixtures/hook-stop.json" >/dev/null
    ;;
  kiro)
    test -f "$PROJECT_DIR/.kiro/agents/default.json"
    jq -e '.hooks.agentSpawn[0].command' "$PROJECT_DIR/.kiro/agents/default.json" >/dev/null
    stop_command="$(jq -r '.hooks.stop[0].command // empty' "$PROJECT_DIR/.kiro/agents/default.json")"
    test -n "$stop_command"
    stop_script="$(python3 - "$stop_command" <<'PY'
import shlex
import sys

parts = shlex.split(sys.argv[1])
if len(parts) < 2 or parts[0] != "node":
    raise SystemExit(1)
print(parts[1])
PY
)"
    test -f "$stop_script"
    PLUGIN_ROOT="$REPO_ROOT/plugins/ndf-kiro" \
      node "$stop_script" session_end < "$REPO_ROOT/tests/runtime-smoke/fixtures/hook-stop.json" >/dev/null
    ;;
  *) echo "unknown runtime: $runtime" >&2; exit 2 ;;
esac
