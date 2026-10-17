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

test_codex_hooks_list_no_data() {
  cat >"$base/bin/codex" <<'PY'
#!/usr/bin/env python3
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    if message.get("id") == 2:
        print(json.dumps({"id": 2, "result": {"items": []}}), flush=True)
        break
PY
  chmod +x "$base/bin/codex"

  local rc=0
  PATH="$base/bin:$PATH" python3 "$REPO_ROOT/tests/runtime-smoke/lib/codex-hooks-list.py" \
    --cwd "$base/cwd" >"$base/no-data.stdout" 2>"$base/no-data.stderr" || rc=$?
  if [ "$rc" -ne 1 ] || ! grep -Fq "hooks/list response has no result.data" "$base/no-data.stderr" \
    || ! grep -Fq '"items": []' "$base/no-data.stderr" || [ -s "$base/no-data.stdout" ]; then
    echo "codex-hooks-list did not reject a response without result.data (exit $rc)" >&2
    cat "$base/no-data.stderr" >&2
    exit 1
  fi
}

test_codex_hooks_list_exit_before_answer() {
  # 標準エラーの末尾が失敗の説明に付くことも固定する。読み取りのスレッドが追いつくよう、書いてから少し待って終わる。
  cat >"$base/bin/codex" <<'PY'
#!/usr/bin/env python3
import json
import sys
import time

for line in sys.stdin:
    message = json.loads(line)
    if message.get("id") == 2:
        print(json.dumps({"id": 1, "result": {}}), flush=True)
        sys.stderr.write("fixture stderr line\n")
        sys.stderr.flush()
        time.sleep(0.5)
        break
sys.exit(0)
PY
  chmod +x "$base/bin/codex"

  local rc=0
  PATH="$base/bin:$PATH" python3 "$REPO_ROOT/tests/runtime-smoke/lib/codex-hooks-list.py" \
    --cwd "$base/cwd" >"$base/exit-early.stdout" 2>"$base/exit-early.stderr" || rc=$?
  if [ "$rc" -ne 1 ] || ! grep -Fq "codex app-server exited before answering hooks/list:" "$base/exit-early.stderr" \
    || ! grep -Fq '{"id": 1, "result": {}}' "$base/exit-early.stderr" \
    || ! grep -Fq "app-server stderr (tail):" "$base/exit-early.stderr" \
    || ! grep -Fq "fixture stderr line" "$base/exit-early.stderr"; then
    echo "codex-hooks-list did not report an app-server that exited before answering (exit $rc)" >&2
    cat "$base/exit-early.stderr" >&2
    exit 1
  fi
}

setup_codex_recording_stub() {
  # 受け取った要求と起動時の作業ディレクトリを記録し、非 ASCII を含む result を返す。
  cat >"$base/bin/codex" <<'PY'
#!/usr/bin/env python3
import json
import os
import sys

record = os.environ["CODEX_REQUESTS"]
with open(record, "w", encoding="utf-8") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\n")
    for line in sys.stdin:
        f.write(line)
        f.flush()
        message = json.loads(line)
        if message.get("id") == 2:
            print(json.dumps({"id": 2, "result": {"data": [{"名前": "フック"}], "extra": 1}}), flush=True)
            break
PY
  chmod +x "$base/bin/codex"
}

assert_codex_requests() {
  local record="$1" expected_cwd="$2"
  if ! python3 - "$record" "$expected_cwd" <<'PY'
import json
import sys

lines = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
expected_cwd = sys.argv[2]
expected = [
    {"argv": ["app-server"], "cwd": expected_cwd},
    {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "runtime-smoke", "version": "0.0.0"}}},
    {"method": "initialized"},
    {"id": 2, "method": "hooks/list", "params": {"cwds": [expected_cwd]}},
]
if lines != expected:
    sys.stderr.write(f"unexpected app-server requests: {lines}\n")
    sys.exit(1)
PY
  then
    echo "codex-hooks-list sent unexpected requests" >&2
    exit 1
  fi
}

test_codex_hooks_list_requests_and_output() {
  setup_codex_recording_stub

  local rc=0
  CODEX_REQUESTS="$base/requests.jsonl" PATH="$base/bin:$PATH" \
    python3 "$REPO_ROOT/tests/runtime-smoke/lib/codex-hooks-list.py" \
    --cwd "$base/cwd" >"$base/requests.stdout" 2>"$base/requests.stderr" || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "codex-hooks-list failed with a recording stub (exit $rc)" >&2
    cat "$base/requests.stderr" >&2
    exit 1
  fi
  assert_codex_requests "$base/requests.jsonl" "$base/cwd"

  # 出力は indent=2・ensure_ascii=False・末尾に改行 1 つ。result の全体（data 以外も）をそのまま書く。
  if ! python3 - "$base/requests.stdout" <<'PY'
import sys

actual = open(sys.argv[1], encoding="utf-8").read()
expected = '{\n  "data": [\n    {\n      "名前": "フック"\n    }\n  ],\n  "extra": 1\n}\n'
sys.exit(0 if actual == expected else 1)
PY
  then
    echo "codex-hooks-list output format changed:" >&2
    cat "$base/requests.stdout" >&2
    exit 1
  fi
}

test_codex_hooks_list_default_cwd() {
  setup_codex_recording_stub

  local rc=0
  (cd "$base/cwd" && CODEX_REQUESTS="$base/requests-default.jsonl" PATH="$base/bin:$PATH" \
    python3 "$REPO_ROOT/tests/runtime-smoke/lib/codex-hooks-list.py" \
    >"$base/requests-default.stdout" 2>"$base/requests-default.stderr") || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "codex-hooks-list failed without --cwd (exit $rc)" >&2
    cat "$base/requests-default.stderr" >&2
    exit 1
  fi
  assert_codex_requests "$base/requests-default.jsonl" "$(cd "$base/cwd" && pwd)"
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
test_codex_hooks_list_exit_before_answer
test_codex_hooks_list_requests_and_output
test_codex_hooks_list_default_cwd
test_reject_marketplace_without_hooks
test_claude_missing_hooks
test_claude_control_silent
test_claude_plugins_report
test_codex_missing_hooks

echo "hooks characterization tests: pass"
