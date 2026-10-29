#!/usr/bin/env bash
# assert-hook-definitions.sh と lib/codex-hooks-list.py の判定分岐を、claude / codex をスタブへ
# 置き換えて固定する単体テスト。実機のスモーク（adapters/）からは呼ばない。
# 呼ぶのは pytest の tests/runtime-smoke/test_hook_definitions_characterization.py の 1 か所だけ。
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
        print(json.dumps(["json but not an object"]), flush=True)
        print("42", flush=True)
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

setup_codex_app_server_stub() {
  # codex-hooks-list.py の main の分岐を固定する。app-server だけをスタブへ置き換え、
  # 受け取った要求と作業ディレクトリを CODEX_STUB_RECORD へ書き、シナリオごとに応答を変える。
  cat >"$base/bin/codex" <<'PY'
#!/usr/bin/env python3
import json
import os
import sys
import time

scenario = os.environ["CODEX_STUB_SCENARIO"]
record = os.environ.get("CODEX_STUB_RECORD")
received = []

if scenario == "stderr-tail":
    sys.stderr.write("stub stderr line\n")
    sys.stderr.flush()

for line in sys.stdin:
    message = json.loads(line)
    received.append(message)
    if message.get("id") != 2:
        continue
    if record:
        with open(record, "w", encoding="utf-8") as f:
            json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), "received": received}, f)
    if scenario == "exit-early":
        print("partial line before exit", flush=True)
        sys.exit(0)
    if scenario == "no-data":
        print(json.dumps({"id": 2, "result": {"other": 1}}), flush=True)
    elif scenario == "result-not-dict":
        print(json.dumps({"id": 2, "result": ["data"]}), flush=True)
    elif scenario == "stderr-tail":
        time.sleep(0.5)
        print(json.dumps({"id": 2, "error": {"message": "fixture error"}}), flush=True)
    else:
        result = {"data": [{"pluginId": "日本語@ai-plugins", "hooks": []}], "extra": None}
        print(json.dumps({"id": 2, "result": result}), flush=True)
    if scenario == "hang-after-answer":
        time.sleep(60)
    break
PY
  chmod +x "$base/bin/codex"
}

run_codex_hooks_list() {
  # $1: シナリオ、$2: 出力の接頭辞。残りは codex-hooks-list.py への引数。終了コードを hooks_list_rc へ残す。
  local scenario="$1" prefix="$2"
  shift 2
  hooks_list_rc=0
  CODEX_STUB_SCENARIO="$scenario" CODEX_STUB_RECORD="$base/$prefix.record" PATH="$base/bin:$PATH" \
    python3 "$REPO_ROOT/tests/runtime-smoke/lib/codex-hooks-list.py" "$@" \
    >"$base/$prefix.stdout" 2>"$base/$prefix.stderr" || hooks_list_rc=$?
}

expect_hooks_list_failure() {
  # $1: 接頭辞、$2: 標準エラーに含まれるべき文言
  if [ "$hooks_list_rc" -ne 1 ] || ! grep -Fq "$2" "$base/$1.stderr" || [ -s "$base/$1.stdout" ]; then
    echo "codex-hooks-list did not fail with '$2' (exit $hooks_list_rc)" >&2
    cat "$base/$1.stderr" >&2
    exit 1
  fi
}

test_codex_hooks_list_no_data() {
  setup_codex_app_server_stub
  run_codex_hooks_list no-data no-data --cwd "$base/cwd"
  expect_hooks_list_failure no-data "hooks/list response has no result.data:"
  grep -Fq '"other": 1' "$base/no-data.stderr" || { echo "no-data failure did not include the response body" >&2; exit 1; }

  run_codex_hooks_list result-not-dict result-not-dict --cwd "$base/cwd"
  expect_hooks_list_failure result-not-dict "hooks/list response has no result.data:"
}

test_codex_hooks_list_exit_early() {
  setup_codex_app_server_stub
  run_codex_hooks_list exit-early exit-early --cwd "$base/cwd"
  expect_hooks_list_failure exit-early "codex app-server exited before answering hooks/list:"
  grep -Fq "partial line before exit" "$base/exit-early.stderr" \
    || { echo "exit-early failure did not include the received lines" >&2; exit 1; }
}

test_codex_hooks_list_stderr_tail() {
  setup_codex_app_server_stub
  run_codex_hooks_list stderr-tail stderr-tail --cwd "$base/cwd"
  expect_hooks_list_failure stderr-tail "hooks/list returned an error:"
  if ! grep -Fq "app-server stderr (tail):" "$base/stderr-tail.stderr" \
    || ! grep -Fq "stub stderr line" "$base/stderr-tail.stderr"; then
    echo "codex-hooks-list did not append the app-server stderr tail" >&2
    cat "$base/stderr-tail.stderr" >&2
    exit 1
  fi
}

test_codex_hooks_list_requests_and_output() {
  # 送る要求の並び・作業ディレクトリ・出力の書式（indent=2、非 ASCII をそのまま、末尾の改行）を固定する。
  setup_codex_app_server_stub
  run_codex_hooks_list success success --cwd "$base/cwd"
  if [ "$hooks_list_rc" -ne 0 ]; then
    echo "codex-hooks-list failed on a plain result (exit $hooks_list_rc)" >&2
    cat "$base/success.stderr" >&2
    exit 1
  fi
  if ! BASE="$base" python3 - <<'PY'
import json
import os
import sys

base = os.environ["BASE"]
cwd = os.path.realpath(os.path.join(base, "cwd"))
record = json.load(open(os.path.join(base, "success.record"), encoding="utf-8"))
expected_received = [
    {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "runtime-smoke", "version": "0.0.0"}}},
    {"method": "initialized"},
    {"id": 2, "method": "hooks/list", "params": {"cwds": [os.path.join(base, "cwd")]}},
]
errors = []
if record["argv"] != ["app-server"]:
    errors.append(f"argv: {record['argv']}")
if os.path.realpath(record["cwd"]) != cwd:
    errors.append(f"cwd: {record['cwd']}")
if record["received"] != expected_received:
    errors.append(f"received: {record['received']}")
stdout = open(os.path.join(base, "success.stdout"), encoding="utf-8").read()
expected_stdout = (
    "{\n"
    '  "data": [\n'
    "    {\n"
    '      "pluginId": "日本語@ai-plugins",\n'
    '      "hooks": []\n'
    "    }\n"
    "  ],\n"
    '  "extra": null\n'
    "}\n"
)
if stdout != expected_stdout:
    errors.append(f"stdout: {stdout!r}")
if errors:
    sys.stderr.write("\n".join(errors) + "\n")
    sys.exit(1)
PY
  then
    echo "codex-hooks-list requests or output changed" >&2
    exit 1
  fi
}

test_codex_hooks_list_default_cwd() {
  # --cwd を省くと、起動したディレクトリを app-server の作業ディレクトリと cwds に使う。
  setup_codex_app_server_stub
  mkdir -p "$base/default-cwd"
  hooks_list_rc=0
  (cd "$base/default-cwd" && CODEX_STUB_SCENARIO=success CODEX_STUB_RECORD="$base/default-cwd.record" PATH="$base/bin:$PATH" \
    python3 "$REPO_ROOT/tests/runtime-smoke/lib/codex-hooks-list.py" \
    >"$base/default-cwd.stdout" 2>"$base/default-cwd.stderr") || hooks_list_rc=$?
  if [ "$hooks_list_rc" -ne 0 ] || ! BASE="$base" python3 - <<'PY'
import json
import os
import sys

base = os.environ["BASE"]
expected = os.path.realpath(os.path.join(base, "default-cwd"))
record = json.load(open(os.path.join(base, "default-cwd.record"), encoding="utf-8"))
cwds = record["received"][-1]["params"]["cwds"]
if os.path.realpath(record["cwd"]) != expected or [os.path.realpath(c) for c in cwds] != [expected]:
    sys.stderr.write(f"cwd={record['cwd']} cwds={cwds}\n")
    sys.exit(1)
PY
  then
    echo "codex-hooks-list did not default --cwd to the current directory (exit $hooks_list_rc)" >&2
    cat "$base/default-cwd.stderr" >&2
    exit 1
  fi
}

test_codex_hooks_list_kills_lingering_server() {
  # 応答の後も終わらない app-server は止めて、待たずに 0 で返る。
  setup_codex_app_server_stub
  local started=$SECONDS
  run_codex_hooks_list hang-after-answer hang --cwd "$base/cwd"
  if [ "$hooks_list_rc" -ne 0 ] || [ $((SECONDS - started)) -ge 20 ] \
    || ! grep -Fq '"pluginId": "日本語@ai-plugins"' "$base/hang.stdout"; then
    echo "codex-hooks-list did not stop a lingering app-server (exit $hooks_list_rc)" >&2
    cat "$base/hang.stderr" >&2
    exit 1
  fi
}

test_reject_marketplace_without_hooks() {
  cat >"$base/bin/codex" <<'SH'
#!/usr/bin/env bash
touch "${RUNTIME_CALLED:?}"
exit 99
SH
  chmod +x "$base/bin/codex"

  local rc=0
  # shellcheck disable=SC2097,SC2098  # 起動するスクリプトは外の REPO_ROOT で指し、子へは別の REPO_ROOT を渡す（#1323）
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
import json
import os
from pathlib import Path
import sys

if "--version" in sys.argv:
    print("1.0.0")
    sys.exit(0)

debug_file = sys.argv[sys.argv.index("--debug-file") + 1]
plugin_dirs = [sys.argv[i + 1] for i, arg in enumerate(sys.argv[:-1]) if arg == "--plugin-dir"]
is_control = any("hooks-positive-control" in arg for arg in sys.argv)
scenario = os.environ["CLAUDE_CHARACTERIZATION_SCENARIO"]
with open(debug_file, "w", encoding="utf-8") as f:
    if is_control and scenario != "control-silent":
        f.write("[WARN] Plugin broken-hooks: hooks.json: fixture warning\n")
        f.write("Read hooks.json for plugin broken-hooks (0.0.0)\n")
    elif not is_control:
        # 渡されたプラグインをすべて読んだことにする。名前は列挙せず、各 plugin.json から取る。
        for plugin_dir in plugin_dirs:
            manifest = json.loads(Path(plugin_dir, ".claude-plugin", "plugin.json").read_text(encoding="utf-8"))
            f.write(f"Read hooks.json for plugin {manifest['name']} (0.0.0)\n")
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
test_codex_hooks_list_no_data
test_codex_hooks_list_exit_early
test_codex_hooks_list_stderr_tail
test_codex_hooks_list_requests_and_output
test_codex_hooks_list_default_cwd
test_codex_hooks_list_kills_lingering_server
test_reject_marketplace_without_hooks
test_claude_missing_hooks
test_claude_control_silent
test_claude_plugins_report
test_codex_missing_hooks

echo "hooks characterization tests: pass"
