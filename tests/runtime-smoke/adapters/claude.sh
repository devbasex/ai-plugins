#!/usr/bin/env bash
set -euo pipefail

source /workspace/ai-plugins/tests/runtime-smoke/lib/common.sh

cd "$PROJECT_DIR"
record_version claude --version
run_step "claude plugin validate ndf" claude plugin validate "$REPO_ROOT/plugins/ndf-claude"
run_step "claude plugin validate playwright-kit" claude plugin validate "$REPO_ROOT/plugins/playwright-kit"
run_step "claude marketplace validate" claude plugin validate "$REPO_ROOT/.claude-plugin/marketplace.json"
run_step "claude marketplace add" claude plugin marketplace add "$REPO_ROOT" --scope local
run_step "claude marketplace list" claude plugin marketplace list
run_step "claude install ndf" claude plugin install ndf@ai-plugins
run_step "claude install playwright-kit" claude plugin install playwright-kit@ai-plugins
run_step "claude install mcp-bigquery" claude plugin install mcp-bigquery@ai-plugins
run_step "claude plugin list" claude plugin list

"$REPO_ROOT/tests/runtime-smoke/assertions/assert-plugin-files.sh" claude
"$REPO_ROOT/tests/runtime-smoke/assertions/assert-mcp-config.sh" claude "$REPO_ROOT/plugins/mcp/claude/mcp-bigquery/.mcp.json"
"$REPO_ROOT/tests/runtime-smoke/assertions/assert-hook-fixtures.sh" claude
"$REPO_ROOT/tests/runtime-smoke/assertions/assert-authenticated-smoke.sh" claude
"$REPO_ROOT/tests/runtime-smoke/assertions/assert-no-host-contamination.sh" claude
write_junit claude
