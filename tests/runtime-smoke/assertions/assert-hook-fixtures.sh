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
    ;;
  *) echo "unknown runtime: $runtime" >&2; exit 2 ;;
esac
