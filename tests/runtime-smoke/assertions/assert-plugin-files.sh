#!/usr/bin/env bash
set -euo pipefail

runtime="${1:?runtime is required}"
: "${REPO_ROOT:=/workspace/ai-plugins}"
: "${PROJECT_DIR:=/tmp/runtime-project}"
: "${HOME:=/tmp/runtime-home}"

case "$runtime" in
  claude)
    find "$HOME" -path '*/.claude-plugin/plugin.json' -print | grep -q .
    find "$HOME" -path '*/skills/*/SKILL.md' -print | grep -q .
    find "$HOME" -path '*/agents/*.md' -print | grep -q .
    find "$HOME" -path '*/hooks/hooks.json' -print | grep -q .
    ;;
  codex)
    find "$HOME" -path '*/.codex-plugin/plugin.json' -print | grep -q .
    find "$HOME" -path '*/skills/*/SKILL.md' -print | grep -q .
    find "$HOME" -path '*/hooks/hooks.json' -print | grep -q .
    ;;
  kiro)
    test -f "$PROJECT_DIR/.kiro/agents/default.json"
    find -L "$PROJECT_DIR/.kiro/skills" -path '*/SKILL.md' -print | grep -q .
    test -f "$PROJECT_DIR/.kiro/prompts/pr.md"
    test -L "$PROJECT_DIR/.kiro/mcp_runtime/mcp-bigquery"
    ;;
  *) echo "unknown runtime: $runtime" >&2; exit 2 ;;
esac
