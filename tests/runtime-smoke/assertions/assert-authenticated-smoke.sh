#!/usr/bin/env bash
set -euo pipefail

runtime="${1:?runtime is required}"
: "${WITH_SECRETS:=off}"
: "${ARTIFACT_DIR:=/tmp/runtime-artifacts}"
: "${PROJECT_DIR:=/tmp/runtime-project}"

log="$ARTIFACT_DIR/authenticated-smoke.log"

if [ "$WITH_SECRETS" = off ]; then
  echo "authenticated smoke skipped: --with-secrets=off" > "$log"
  exit 0
fi

auth_ran=false

run_bigquery_secret_check() {
  [ -n "${BIGQUERY_PROJECT:-}" ] || return 1
  [ -n "${BIGQUERY_LOCATION:-}" ] || return 1
  [ -n "${BIGQUERY_DATASET:-}" ] || return 1
  [ -n "${BIGQUERY_KEY_FILE:-}" ] || return 1
  case "$BIGQUERY_KEY_FILE" in
    /tmp/runtime-secrets/*) ;;
    *) echo "BIGQUERY_KEY_FILE is not inside /tmp/runtime-secrets" >&2; return 1 ;;
  esac
  test -f "$BIGQUERY_KEY_FILE"
  python3 - "$BIGQUERY_KEY_FILE" >> "$log" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
json.loads(path.read_text(encoding="utf-8"))
print("bigquery credential json parsed")
PY
  auth_ran=true
}

case "$runtime" in
  claude)
    if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
      timeout 60s claude -p "runtime smoke: reply OK only" >> "$log" 2>&1
      auth_ran=true
    fi
    run_bigquery_secret_check || true
    ;;
  codex)
    if [ -n "${OPENAI_API_KEY:-}" ]; then
      timeout 60s codex exec --dangerously-bypass-approvals-and-sandbox "Print OK only for runtime smoke." >> "$log" 2>&1
      auth_ran=true
    fi
    run_bigquery_secret_check || true
    ;;
  kiro)
    if command -v kiro-cli >/dev/null 2>&1 && [ -n "${ANTHROPIC_API_KEY:-}${OPENAI_API_KEY:-}" ]; then
      timeout 30s kiro-cli doctor >> "$log" 2>&1 || true
      auth_ran=true
    fi
    run_bigquery_secret_check || true
    ;;
  *) echo "unknown runtime: $runtime" >&2; exit 2 ;;
esac

if [ "$auth_ran" = false ]; then
  if [ "$WITH_SECRETS" = required ]; then
    echo "no runtime-usable authenticated smoke secret was available for $runtime" >&2
    exit 1
  fi
  echo "authenticated smoke skipped: no runtime-usable secret for $runtime" > "$log"
fi
