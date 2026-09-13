#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${REPO_ROOT:?}"
base="$(mktemp -d)"
trap 'rm -rf "$base"' EXIT

test_codex_hooks_list_error() {
  cat >"$base/bin/codex" <<'PY'
#!/usr/bin/env python3
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    if message.get("id") == 2:
        print(json.dumps({"id": 2, "error": {"message": "fixture error"}}), flush=True)
        break
PY
  chmod +x "$base/bin/codex"

  local rc=0
  PATH="$base/bin:$PATH" python3 "$REPO_ROOT/tests/runtime-smoke/lib/codex-hooks-list.py" \
    --cwd "$base/cwd" >"$base/hooks-list.stdout" 2>"$base/hooks-list.stderr" || rc=$?
  if [ "$rc" -ne 1 ] || ! grep -Fq "hooks/list returned an error" "$base/hooks-list.stderr"; then
    echo "codex-hooks-list did not report the hooks/list error (exit $rc)" >&2
    cat "$base/hooks-list.stderr" >&2
    exit 1
  fi
}

test_codex_hooks_list_filter() {
  cat >"$base/bin/codex" <<'PY'
#!/usr/bin/env python3
import json
import sys

for line in sys.stdin:
    try:
        message = json.loads(line)
    except Exception:
        continue
    if message.get("id") == 2:
        print("non-json line from app-server", flush=True)
        print(json.dumps({"method": "window/logMessage", "params": {"type": 3, "message": "initializing"}}), flush=True)
        print(json.dumps({"id": 1, "result": {"serverInfo": {"name": "dummy"}}}), flush=True)
        result = {
            "data": [
                {
                    "pluginId": "dummy-plugin@ai-plugins",
                    "hooks": [
                        {"type": "command", "command": "echo characterization"}
                    ]
                }
            ]
        }
        print(json.dumps({"id": 2, "result": result}), flush=True)
        break
PY
  chmod +x "$base/bin/codex"

  local rc=0
  PATH="$base/bin:$PATH" python3 "$REPO_ROOT/tests/runtime-smoke/lib/codex-hooks-list.py" \
    --cwd "$base/cwd" >"$base/hooks-list-branch.stdout" 2>"$base/hooks-list-branch.stderr" || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "codex-hooks-list failed on non-JSON and other id lines (exit $rc)" >&2
    cat "$base/hooks-list-branch.stderr" >&2
    exit 1
  fi

  if ! python3 -c "
import json, sys
actual = json.load(open('$base/hooks-list-branch.stdout'))
expected = {
    'data': [
        {
            'pluginId': 'dummy-plugin@ai-plugins',
            'hooks': [
                {'type': 'command', 'command': 'echo characterization'}
            ]
        }
    ]
}
if actual != expected:
    sys.stderr.write(f'unexpected hooks/list result: {actual}\n')
    sys.exit(1)
"; then
    echo "codex-hooks-list output did not match expected result" >&2
    exit 1
  fi
}

test_discover_targets_empty_boundary() {
  # discover_targets の境界: hooks 定義を持つプラグインが 0 件のマーケットプレイスでは
  # 空出力（0 行）を返す。呼び出し元はこの空出力を 0 件として検出する（後段の no-hooks の
  # 経路が確かめる）。ここでは関数そのものを本物の assert-hook-definitions.sh から取り出して
  # 実行し、実装と結合したまま境界値を固定する。
  local assert_dir="$REPO_ROOT/tests/runtime-smoke/assertions/assert-hook-definitions.sh"
  local no_hooks_fixture="$REPO_ROOT/tests/runtime-smoke/fixtures/hooks-positive-control/no-hooks"
  local discover_fn
  discover_fn="$(sed -n '/^discover_targets() {$/,/^}$/p' "$assert_dir")"
  if [ -z "$discover_fn" ]; then
    echo "could not extract discover_targets from $assert_dir" >&2
    exit 1
  fi
  eval "$discover_fn"
  local rt targets_out
  for rt in claude codex; do
    targets_out="$(discover_targets "$no_hooks_fixture" "$rt")"
    if [ -n "$targets_out" ]; then
      echo "discover_targets ($rt) returned output for a marketplace without hooks:" >&2
      printf '%s\n' "$targets_out" >&2
      exit 1
    fi
  done
  unset -f discover_targets
}

test_reject_marketplace_without_hooks() {
  cat >"$base/bin/codex" <<'SH'
#!/usr/bin/env bash
touch "${RUNTIME_CALLED:?}"
exit 99
SH
  chmod +x "$base/bin/codex"

  local rc=0
  RUNTIME_CALLED="$base/runtime-called" \
  REPO_ROOT="$REPO_ROOT/tests/runtime-smoke/fixtures/hooks-positive-control/no-hooks" \
  ARTIFACT_DIR="$base/artifacts" HOME="$base/home" PATH="$base/bin:$PATH" \
    "$REPO_ROOT/tests/runtime-smoke/assertions/assert-hook-definitions.sh" codex \
    >"$base/no-hooks.stdout" 2>"$base/no-hooks.stderr" || rc=$?
  if [ "$rc" -ne 1 ] || ! grep -Fq "no plugin with hooks definitions found" "$base/no-hooks.stderr"; then
    echo "assert-hook-definitions did not reject a marketplace without hooks (exit $rc)" >&2
    cat "$base/no-hooks.stderr" >&2
    exit 1
  fi
  if [ -e "$base/runtime-called" ]; then
    echo "assert-hook-definitions started codex before rejecting a marketplace without hooks" >&2
    exit 1
  fi
}

test_claude_missing_hooks() {
  cat >"$base/bin/claude" <<'PY'
#!/usr/bin/env python3
import sys

if "--version" in sys.argv:
    print("1.0.0")
    sys.exit(0)

debug_file = None
for i, arg in enumerate(sys.argv):
    if arg == "--debug-file" and i + 1 < len(sys.argv):
        debug_file = sys.argv[i + 1]
        break

if debug_file:
    with open(debug_file, "w", encoding="utf-8") as f:
        if any("broken-hooks" in arg or "positive-control" in arg for arg in sys.argv):
            f.write("[WARN] Plugin broken-hooks: hooks.json: fixture warning\n")
            f.write("Read hooks.json for plugin broken-hooks (0.0.0)\n")
        else:
            f.write("[INFO] plugin list called without reading hooks\n")

sys.exit(0)
PY
  chmod +x "$base/bin/claude"

  local rc=0
  ARTIFACT_DIR="$base/artifacts-claude" HOME="$base/home" PATH="$base/bin:$PATH" \
    "$REPO_ROOT/tests/runtime-smoke/assertions/assert-hook-definitions.sh" claude \
    >"$base/claude-missing.stdout" 2>"$base/claude-missing.stderr" || rc=$?
  if [ "$rc" -ne 1 ] || ! grep -Fq "claude did not load hooks for:" "$base/claude-missing.stderr"; then
    echo "assert-hook-definitions did not report missing hooks for claude (exit $rc)" >&2
    cat "$base/claude-missing.stderr" >&2
    exit 1
  fi
}

setup_claude_diagnostic_stub() {
  # 公開 CLI の判定分岐を固定する。内部の読み込み関数ではなく、外部ランタイムだけを
  # スタブへ置き換えて終了コードと診断の要点を観測する。
  cat >"$base/bin/claude" <<'PY'
#!/usr/bin/env python3
import os
import sys

if "--version" in sys.argv:
    print("1.0.0")
    sys.exit(0)

debug_file = sys.argv[sys.argv.index("--debug-file") + 1]
is_control = any("hooks-positive-control" in arg for arg in sys.argv)
scenario = os.environ["CLAUDE_CHARACTERIZATION_SCENARIO"]
with open(debug_file, "w", encoding="utf-8") as f:
    if is_control and scenario != "control-silent":
        f.write("[WARN] Plugin broken-hooks: hooks.json: fixture warning\n")
        f.write("Read hooks.json for plugin broken-hooks (0.0.0)\n")
    elif not is_control:
        for name in ("ndf", "mcp-playwright", "mcp-serena"):
            f.write(f"Read hooks.json for plugin {name} (0.0.0)\n")
        if scenario == "plugins-report":
            f.write("[WARN] Plugin ndf: hooks.json: characterization warning\n")
PY
  chmod +x "$base/bin/claude"
}

test_claude_control_silent() {
  setup_claude_diagnostic_stub

  local rc=0
  CLAUDE_CHARACTERIZATION_SCENARIO=control-silent \
  ARTIFACT_DIR="$base/artifacts-control-silent" HOME="$base/home" PATH="$base/bin:$PATH" \
    "$REPO_ROOT/tests/runtime-smoke/assertions/assert-hook-definitions.sh" claude \
    >"$base/control-silent.stdout" 2>"$base/control-silent.stderr" || rc=$?
  if [ "$rc" -ne 1 ] || ! grep -Fq "did not report the broken hooks fixture" "$base/control-silent.stderr"; then
    echo "assert-hook-definitions did not reject a silent positive control (exit $rc)" >&2
    cat "$base/control-silent.stderr" >&2
    exit 1
  fi
}

test_claude_plugins_report() {
  setup_claude_diagnostic_stub

  local rc=0
  CLAUDE_CHARACTERIZATION_SCENARIO=plugins-report \
  ARTIFACT_DIR="$base/artifacts-plugins-report" HOME="$base/home" PATH="$base/bin:$PATH" \
    "$REPO_ROOT/tests/runtime-smoke/assertions/assert-hook-definitions.sh" claude \
    >"$base/plugins-report.stdout" 2>"$base/plugins-report.stderr" || rc=$?
  if [ "$rc" -ne 1 ] || ! grep -Fq "claude reported hooks definitions:" "$base/plugins-report.stderr" \
    || ! grep -Fq "characterization warning" "$base/plugins-report.stderr"; then
    echo "assert-hook-definitions did not report real plugin hooks diagnostics (exit $rc)" >&2
    cat "$base/plugins-report.stderr" >&2
    exit 1
  fi
}

test_codex_missing_hooks() {
  cat >"$base/bin/codex" <<'PY'
#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

if "--version" in sys.argv:
    print("codex-cli 1.0.0")
    sys.exit(0)
if len(sys.argv) >= 5 and sys.argv[1:4] == ["plugin", "marketplace", "add"]:
    Path(os.environ["CODEX_HOME"], "fixture-root").write_text(sys.argv[4], encoding="utf-8")
    sys.exit(0)
if len(sys.argv) >= 3 and sys.argv[1:3] == ["plugin", "add"]:
    sys.exit(0)
if sys.argv[1:] == ["app-server"]:
    root = Path(os.environ["CODEX_HOME"], "fixture-root").read_text(encoding="utf-8")
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("id") != 2:
            continue
        if "hooks-positive-control" in root:
            data = [{
                "hooks": [{
                    "pluginId": "broken-hooks@hooks-positive-control",
                    "type": "command",
                    "command": "true",
                }],
                "warnings": ["characterization fixture warning"],
                "errors": [],
            }]
        else:
            data = []
        print(json.dumps({"id": 2, "result": {"data": data}}), flush=True)
        break
    sys.exit(0)
sys.exit(2)
PY
  chmod +x "$base/bin/codex"

  local rc=0
  ARTIFACT_DIR="$base/artifacts-codex-missing" HOME="$base/home" PATH="$base/bin:$PATH" \
    "$REPO_ROOT/tests/runtime-smoke/assertions/assert-hook-definitions.sh" codex \
    >"$base/codex-missing.stdout" 2>"$base/codex-missing.stderr" || rc=$?
  if [ "$rc" -ne 1 ] || ! grep -Fq "codex did not load hooks for:" "$base/codex-missing.stderr"; then
    echo "assert-hook-definitions did not report missing hooks for codex (exit $rc)" >&2
    cat "$base/codex-missing.stderr" >&2
    exit 1
  fi
}

mkdir -p "$base/bin" "$base/cwd"

test_codex_hooks_list_error
test_codex_hooks_list_filter
test_discover_targets_empty_boundary
test_reject_marketplace_without_hooks
test_claude_missing_hooks
test_claude_control_silent
test_claude_plugins_report
test_codex_missing_hooks

echo "hooks characterization tests: pass"
