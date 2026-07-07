#!/usr/bin/env bash
set -euo pipefail

: "${REPO_ROOT:=/workspace/ai-plugins}"
: "${PROJECT_DIR:=/tmp/runtime-project}"
: "${ARTIFACT_DIR:=/tmp/runtime-artifacts}"
: "${HOME:=/tmp/runtime-home}"

export PATH="$HOME/.local/bin:/root/.local/bin:$PATH"

mkdir -p "$ARTIFACT_DIR"
cd "$REPO_ROOT"

if [ -f /tmp/runtime-secrets/raw-env ]; then
  # shellcheck disable=SC1091
  source /tmp/runtime-secrets/raw-env
fi

run_step() {
  local name="$1"
  shift
  echo "==> $name"
  "$@"
}

record_version() {
  echo "==> $*" | tee -a "$ARTIFACT_DIR/version.log"
  "$@" 2>&1 | tee -a "$ARTIFACT_DIR/version.log"
}

write_junit() {
  local runtime="$1"
  cat > "$ARTIFACT_DIR/junit.xml" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="runtime-smoke-$runtime" tests="1" failures="0" skipped="0">
  <testcase classname="runtime-smoke" name="$runtime"/>
</testsuite>
XML
}
