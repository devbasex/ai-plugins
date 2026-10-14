#!/usr/bin/env bash
set -euo pipefail

source /workspace/ai-plugins/tests/runtime-smoke/lib/common.sh

cd "$PROJECT_DIR"
record_version agy --version

run_step "agy validate ndf" agy plugin validate "$REPO_ROOT/plugins/ndf/dev.agy"
run_step "agy install ndf" agy plugin install "$REPO_ROOT/plugins/ndf/dev.agy"
# 2 回目で壊れないことを見る。agy には入れ替えの操作が無く、利用者は install を繰り返す。
run_step "agy install ndf idempotent" agy plugin install "$REPO_ROOT/plugins/ndf/dev.agy"
run_step "agy plugin list" agy plugin list

"$REPO_ROOT/tests/runtime-smoke/assertions/assert-plugin-files.sh" agy
"$REPO_ROOT/tests/runtime-smoke/assertions/assert-authenticated-smoke.sh" agy
"$REPO_ROOT/tests/runtime-smoke/assertions/assert-no-host-contamination.sh" agy
write_junit agy
