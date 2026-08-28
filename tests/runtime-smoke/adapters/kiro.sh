#!/usr/bin/env bash
set -euo pipefail

source /workspace/ai-plugins/tests/runtime-smoke/lib/common.sh

cd "$PROJECT_DIR"
if command -v kiro-cli >/dev/null 2>&1; then
  record_version kiro-cli --help
else
  echo "kiro-cli is not available; using installer fallback" | tee "$ARTIFACT_DIR/version.log"
fi

run_step "kiro install ndf" bash "$REPO_ROOT/plugins/ndf/dev.kiro/install.sh" --project "$PROJECT_DIR" --with-slack
run_step "kiro install ndf idempotent" bash "$REPO_ROOT/plugins/ndf/dev.kiro/install.sh" --project "$PROJECT_DIR" --with-slack
run_step "kiro install playwright-kit" bash "$REPO_ROOT/plugins/playwright-kit/dev.kiro/install.sh" --project "$PROJECT_DIR"
run_step "kiro install playwright-kit idempotent" bash "$REPO_ROOT/plugins/playwright-kit/dev.kiro/install.sh" --project "$PROJECT_DIR"
run_step "kiro install mcp-bigquery" bash "$REPO_ROOT/plugins/mcp/kiro/mcp-bigquery/install.sh" --project "$PROJECT_DIR"
run_step "kiro install mcp-bigquery idempotent" bash "$REPO_ROOT/plugins/mcp/kiro/mcp-bigquery/install.sh" --project "$PROJECT_DIR"

"$REPO_ROOT/tests/runtime-smoke/assertions/assert-plugin-files.sh" kiro
"$REPO_ROOT/tests/runtime-smoke/assertions/assert-mcp-config.sh" kiro "$PROJECT_DIR/.mcp.json"
"$REPO_ROOT/tests/runtime-smoke/assertions/assert-hook-fixtures.sh" kiro
"$REPO_ROOT/tests/runtime-smoke/assertions/assert-kiro-agent.sh" workspace

run_step "kiro install ndf global" bash "$REPO_ROOT/plugins/ndf/dev.kiro/install.sh" --scope global --with-slack
"$REPO_ROOT/tests/runtime-smoke/assertions/assert-kiro-agent.sh" global
"$REPO_ROOT/tests/runtime-smoke/assertions/assert-authenticated-smoke.sh" kiro
"$REPO_ROOT/tests/runtime-smoke/assertions/assert-no-host-contamination.sh" kiro
write_junit kiro
