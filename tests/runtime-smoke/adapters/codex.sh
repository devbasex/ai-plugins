#!/usr/bin/env bash
set -euo pipefail

source /workspace/ai-plugins/tests/runtime-smoke/lib/common.sh

cd "$PROJECT_DIR"
record_version codex --version
run_step "codex marketplace add" codex plugin marketplace add "$REPO_ROOT"
run_step "codex marketplace list" codex plugin marketplace list
run_step "codex install ndf" codex plugin add ndf@ai-plugins
run_step "codex install playwright-kit" codex plugin add playwright-kit@ai-plugins
run_step "codex install mcp-bigquery" codex plugin add mcp-bigquery@ai-plugins
run_step "codex plugin list" codex plugin list

"$REPO_ROOT/tests/runtime-smoke/assertions/assert-plugin-files.sh" codex
"$REPO_ROOT/tests/runtime-smoke/assertions/assert-mcp-config.sh" codex "$REPO_ROOT/plugins/mcp/codex/mcp-bigquery/.mcp.json"
"$REPO_ROOT/tests/runtime-smoke/assertions/assert-hook-fixtures.sh" codex
"$REPO_ROOT/tests/runtime-smoke/assertions/assert-authenticated-smoke.sh" codex
"$REPO_ROOT/tests/runtime-smoke/assertions/assert-no-host-contamination.sh" codex
write_junit codex
