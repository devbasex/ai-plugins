"""実行の核（`lib/run_core.py`・`lib/run-core.sh`）の振る舞い（#2337 の受け入れ条件 1〜4・8・17・19）。

一時ディレクトリに契約の項目と本体を置き、核を通して起動した結果 JSON・標準出力・終了コードを見る。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import step_result  # noqa: E402

ENTRY = "plugins/ndf/scripts/tool.py"
EXITS = {"0": "成功", "1": "失敗", "2": "読めない・呼び出しの誤り", "6": "欠けが残る", "10": "承認ゲート"}

PY = """import json, os, signal, sys, time
sys.path.insert(0, {lib!r})
import run_core, step_result
from pathlib import Path
run_core.CONTRACTS = Path({contracts!r})

def body(argv):
    how = argv[0] if argv else "ok"
{body}

if __name__ == "__main__":
    run_core.main({entry!r}, body)
"""

BODY = """    if how == "ok":
        step_result.emit(step_result.result("tool", "ok", "済んだ"))
    if how == "gaps":
        step_result.emit({"tool": "tool", "status": "ok", "summary": "x", "items": [], "metrics": {},
                          "gaps": [{"kind": "k", "name": "n", "reason": "欠け"}]})
    if how == "gate":
        step_result.emit(step_result.result("tool", "gate", "待つ", next="人へ聞く"))
    if how == "raise":
        raise RuntimeError("壊れた https://user:secret@example.com")
    if how == "term":
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(5)
    if how == "sleep":
        time.sleep(5)
    if how == "none":
        return None
    if how == "int":
        return 0
    if how == "str":
        return "x"
    if how == "badshape":
        step_result.emit({"tool": "tool", "status": "ok"})
    if how == "mismatch":
        step_result.emit(step_result.result("tool", "ok", "x"), 1)
    if how == "unlisted":
        step_result.emit(step_result.result("tool", "stopped", "x"), 3)
    if how == "help":
        print("usage")
        raise SystemExit(0)
    if how == "print":
        print("そのままの文")
        return 0
    if how == "child":
        print(os.environ.get("NDF_RESULT_FILE", "-"), os.environ.get("NDF_RUN_TIMEOUT", "-"))
        return 0
    if how == "except":
        try:
            step_result.emit(step_result.result("tool", "ok", "握られない"))
        except Exception:
            return 1
"""


def setup(tmp_path: Path, stdout: str = "step-result", entry: str = ENTRY) -> Path:
    c = tmp_path / "contracts"
    c.mkdir(exist_ok=True)
    item = {"path": entry, "stdout": stdout, "exit": EXITS}
    (c / (entry.replace("/", "__") + ".json")).write_text(json.dumps(item, ensure_ascii=False))
    script = tmp_path / "tool.py"
    script.write_text(PY.format(lib=str(LIB), contracts=str(c), body=BODY, entry=entry))
    return script


def run(script: Path, *args: str, env: dict | None = None, timeout: float = 30) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if k not in ("NDF_RESULT_FILE", "NDF_RUN_TIMEOUT", "NDF_RUN_ENTRY")}
    e.update(env or {})
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, env=e, timeout=timeout)


def last(p: subprocess.CompletedProcess) -> dict:
    return json.loads(p.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    "how,env,status,code,gap",
    [
        ("ok", {}, "ok", 0, None),
        ("gate", {}, "gate", 10, None),
        ("raise", {}, "stopped", 1, "exception"),
        ("term", {}, "stopped", 1, "interrupted"),
        ("sleep", {"NDF_RUN_TIMEOUT": "0.5"}, "stopped", 1, "timeout"),
    ],
)
def test_every_way_of_ending_returns_one_of_three_states_with_a_matching_code(tmp_path, how, env, status, code, gap):
    p = run(setup(tmp_path), how, env=env)
    r = last(p)
    assert (r["status"], p.returncode) == (status, code)
    assert step_result.code_matches(r["status"], p.returncode)
    assert r["summary"].strip() and r["run_id"]
    if gap:
        assert [g["kind"] for g in r["gaps"]] == [gap]


def test_an_exception_keeps_its_traceback_on_stderr_and_hides_secrets(tmp_path):
    p = run(setup(tmp_path), "raise")
    assert "Traceback" in p.stderr
    assert "secret" not in last(p)["summary"]


def test_ok_with_gaps_becomes_stopped_6(tmp_path):
    p = run(setup(tmp_path), "gaps")
    r = last(p)
    assert (r["status"], p.returncode) == ("stopped", 6)
    assert r["gaps"][0]["reason"] == "欠け"


@pytest.mark.parametrize("how", ["none", "int", "str", "badshape", "mismatch", "unlisted"])
def test_a_body_that_breaks_the_result_shape_stops_with_1(tmp_path, how):
    p = run(setup(tmp_path), how)
    r = last(p)
    assert (r["status"], p.returncode) == ("stopped", 1)
    assert r["gaps"][0]["kind"] == "result-shape"


def test_help_exits_0_without_adding_a_result(tmp_path):
    p = run(setup(tmp_path), "help")
    assert p.returncode == 0 and p.stdout == "usage\n"


def test_emit_is_not_swallowed_by_except_exception(tmp_path):
    p = run(setup(tmp_path), "except")
    assert (last(p)["status"], p.returncode) == ("ok", 0)


def test_unknown_entrypoint_stops_with_2(tmp_path):
    script = setup(tmp_path)
    script.write_text(script.read_text().replace(f"run_core.main({ENTRY!r}", 'run_core.main("plugins/ndf/scripts/nope.py"'))
    p = run(script, "ok")
    r = last(p)
    assert (r["status"], p.returncode) == ("stopped", 2) and r["gaps"][0]["kind"] == "contract"


def test_text_entrypoint_keeps_its_stdout_and_writes_the_result_file(tmp_path):
    script = setup(tmp_path, stdout="text")
    out = tmp_path / "r.json"
    p = run(script, "print", env={"NDF_RESULT_FILE": str(out)})
    assert p.stdout == "そのままの文\n" and p.returncode == 0
    assert json.loads(out.read_text())["status"] == "ok"


def test_text_entrypoint_failure_goes_to_stderr_and_the_result_file(tmp_path):
    script = setup(tmp_path, stdout="text")
    out = tmp_path / "r.json"
    p = run(script, "gaps", env={"NDF_RESULT_FILE": str(out)})
    assert p.stdout == "" and p.returncode == 6
    assert "[ndf run-core]" in p.stderr
    assert json.loads(out.read_text())["status"] == "stopped"


def test_result_file_and_timeout_are_not_inherited(tmp_path):
    script = setup(tmp_path, stdout="text")
    p = run(script, "child", env={"NDF_RESULT_FILE": str(tmp_path / "r.json"), "NDF_RUN_TIMEOUT": "30"})
    assert p.stdout == "- -\n"


def test_result_keeps_the_existing_keys_and_adds_only_run_id(tmp_path):
    r = last(run(setup(tmp_path), "ok"))
    assert set(r) - {"run_id"} <= set(step_result.REQUIRED) | set(step_result.OPTIONAL) | set(step_result.RECORD_KEYS)
    assert not step_result.validate_result(r, 0)


def test_missing_extras_stop_with_69_without_a_traceback(tmp_path):
    script = setup(tmp_path)
    script.write_text(
        script.read_text().replace(
            "def body(argv):",
            "import deps\ndeps.GROUPS['nothing'] = ['ndf_no_such_module']\nos.environ['NDF_DEPS_REEXEC'] = str(Path(sys.argv[0]).resolve())\n"
            "run_core.PLUGIN_ROOT = Path(sys.argv[0]).resolve().parents[0]\n"
            "run_core.entry_of_script = lambda s: {entry!r}\n"
            "deps.require('nothing')\n\ndef body(argv):".replace("{entry!r}", repr(ENTRY)),
        )
    )
    p = run(script, "ok")
    assert p.returncode == 69 and "Traceback" not in p.stderr
    r = last(p)
    assert r["status"] == "stopped" and r["gaps"][0] == {"kind": "deps", "name": "nothing", "reason": r["gaps"][0]["reason"]}


# --- シェル ----------------------------------------------------------------------

SH = """#!/usr/bin/env bash
. {lib}/run-core.sh; rc_enter {entry} "$@"
case "$1" in
  ok) echo "途中の行"; python3 {lib}/step_result.py emit --tool tool --status ok --summary 済んだ ;;
  gaps) python3 {lib}/step_result.py emit --tool tool --status stopped --summary x --gap k n 欠け --code 1 ;;
  okgaps) echo '{{"tool":"tool","status":"ok","summary":"x","items":[],"metrics":{{}},"gaps":[{{"kind":"k","name":"n","reason":"r"}}]}}' ;;
  sleep) sleep 30 ;;
  fail) exit 1 ;;
esac
"""


def sh_setup(tmp_path: Path, stdout: str = "step-result") -> Path:
    entry = "plugins/ndf/scripts/tool.sh"
    c = tmp_path / "contracts"
    c.mkdir(exist_ok=True)
    (c / (entry.replace("/", "__") + ".json")).write_text(json.dumps({"path": entry, "stdout": stdout, "exit": EXITS}))
    # 核の契約の置き場を一時ディレクトリへ向けた複製を使う
    lib = tmp_path / "lib"
    lib.mkdir()
    for f in ("run_core.py", "run-core.sh", "step_result.py", "deps.py", "proc.py", "secret_redact.py"):
        (lib / f).write_text((LIB / f).read_text())
    core = lib / "run_core.py"
    core.write_text(core.read_text().replace('CONTRACTS = PLUGIN_ROOT / "contracts" / "entrypoints"', f"CONTRACTS = Path({str(c)!r})"))
    script = tmp_path / "tool.sh"
    script.write_text(SH.format(lib=lib, entry=entry))
    return script


def sh_run(script: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if k not in ("NDF_RESULT_FILE", "NDF_RUN_TIMEOUT", "NDF_RUN_ENTRY")}
    e.update(env or {})
    return subprocess.run(["bash", str(script), *args], capture_output=True, text=True, env=e, timeout=60)


@pytest.mark.parametrize(
    "how,env,status,code",
    [
        ("ok", {}, "ok", 0),
        ("gaps", {}, "stopped", 1),
        ("okgaps", {}, "stopped", 6),
        ("sleep", {"NDF_RUN_TIMEOUT": "0.5"}, "stopped", 1),
    ],
)
def test_shell_entrypoint_runs_as_a_child_of_the_core(tmp_path, how, env, status, code):
    p = sh_run(sh_setup(tmp_path), how, env=env)
    lines = p.stdout.strip().splitlines()
    r = json.loads(lines[-1])
    assert (r["status"], p.returncode) == (status, code)
    if how == "ok":
        assert lines[0] == "途中の行"


def test_shell_exit_without_a_result_keeps_stdout_and_writes_the_result_file(tmp_path):
    out = tmp_path / "r.json"
    p = sh_run(sh_setup(tmp_path), "fail", env={"NDF_RESULT_FILE": str(out)})
    assert (p.stdout, p.returncode) == ("", 1)
    assert json.loads(out.read_text())["status"] == "stopped"


def test_shell_child_receives_sigterm_through_the_core(tmp_path):
    e = {k: v for k, v in os.environ.items() if k not in ("NDF_RESULT_FILE", "NDF_RUN_TIMEOUT", "NDF_RUN_ENTRY")}
    p = subprocess.Popen(["bash", str(sh_setup(tmp_path)), "sleep"], stdout=subprocess.PIPE, text=True, env=e)
    time.sleep(1.5)
    p.send_signal(signal.SIGTERM)
    out, _ = p.communicate(timeout=30)
    r = json.loads(out.strip().splitlines()[-1])
    assert (r["status"], p.returncode, r["gaps"][0]["kind"]) == ("stopped", 1, "interrupted")
