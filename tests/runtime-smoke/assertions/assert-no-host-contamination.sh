#!/usr/bin/env bash
set -euo pipefail

runtime="${1:?runtime is required}"
: "${REPO_ROOT:=/workspace/ai-plugins}"
: "${PROJECT_DIR:=/tmp/runtime-project}"
: "${HOME:=/tmp/runtime-home}"

case "$HOME" in
  /tmp/runtime-home*) ;;
  *) echo "$runtime: HOME is not isolated: $HOME" >&2; exit 1 ;;
esac
test "$PROJECT_DIR" = /tmp/runtime-project

for path in "$REPO_ROOT/.claude" "$REPO_ROOT/.codex" "$REPO_ROOT/.kiro" "$REPO_ROOT/.mcp.json"; do
  if [ -e "$path" ]; then
    echo "$runtime: runtime config contaminated repo root: $path" >&2
    exit 1
  fi
done
for forbidden in /root/.claude /root/.codex /root/.kiro /root/.gemini /root/.config /root/.aws /root/.ssh; do
  if [ -e "$forbidden" ]; then
    echo "$runtime: runtime touched forbidden host-like path: $forbidden" >&2
    exit 1
  fi
done
leaked="$(find /tmp/runtime-artifacts -path '/tmp/runtime-artifacts/*runtime-secrets*' -print -quit)"
if [ -n "$leaked" ]; then
  echo "$runtime: secret path included in artifacts" >&2
  exit 1
fi
