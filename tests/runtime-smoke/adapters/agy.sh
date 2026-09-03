#!/usr/bin/env bash
set -euo pipefail

source /workspace/ai-plugins/tests/runtime-smoke/lib/common.sh

cd "$PROJECT_DIR"
if ! command -v agy >/dev/null 2>&1; then
  # CLI を取得できない環境では、導入の手順そのものが実行できない。読み飛ばした事実を
  # 証跡へ残す（Kiro CLI の installer フォールバックと同じ考え方）。
  echo "agy is not available; skipped the install steps" | tee "$ARTIFACT_DIR/version.log"
  write_junit agy
  exit 0
fi

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
