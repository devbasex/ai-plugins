"""`external-ai.py run / check` を見本の CLI で確かめる（実機の CLI は起動しない）。"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills" / "external-ai" / "scripts" / "external-ai.py"
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import monitor_outcome  # noqa: E402
import step_result  # noqa: E402

# 見本の CLI。FAKE_MODE で振る舞いを変える。認証の確認（login status など）にも答える。
FAKE = r"""#!/bin/sh
case "$1 $2" in
  "login status"|"auth status"|"models "|"whoami ")
    if [ "$FAKE_AUTH" = fail ]; then echo "not logged in" >&2; exit 1; fi
    echo ok; exit 0;;
esac
[ -t 0 ] || cat > /dev/null
case "$FAKE_MODE" in
  ok)      echo "レビューの結果" > "$FAKE_OUT"; echo "thinking" >&2
           [ "$FAKE_NAME" = codex ] && printf 'tokens used\n123\n' >&2;;
  stdout)  if [ "$FAKE_NAME" = claude ]; then
             printf '{"type":"result","is_error":false,"result":"標準出力の結果","modelUsage":{"claude-x-1":{}}}'
           else printf '\033[1m標準出力の結果\033[0m\n'; fi;;
  none)    echo "何も書かずに終わる" >&2;;
  hang)    echo "start" >&2; sleep 30;;
  usage)   echo "Monthly request limit reached" >&2; sleep 30;;
esac
exit 0
"""


def _env(tmp_path: pathlib.Path, runtime: str, mode: str, **extra) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    exe = "kiro-cli" if runtime == "kiro" else runtime
    stub = bin_dir / exe
    stub.write_text(FAKE, encoding="utf-8")
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
           "FAKE_MODE": mode, "FAKE_NAME": runtime, "FAKE_OUT": str(tmp_path / "out.md"),
           "NDF_EXTERNAL_AI_TMP_DIR": str(tmp_path / "t")}
    env.pop("NDF_SKIP_AUTH_CHECK", None)
    env.update(extra)
    return env


def _run(tmp_path, runtime, mode, *args, **extra):
    prompt = tmp_path / "p.md"
    prompt.write_text("これを読んで答える\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "run", runtime, "--prompt-file", str(prompt),
         "--output-file", str(tmp_path / "out.md"), "--workdir", str(work), "--poll", "1", *args],
        env=_env(tmp_path, runtime, mode, **extra), capture_output=True, text=True, timeout=60)
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert step_result.validate_result(out, p.returncode) == [], (out, p.stderr[-2000:])
    return p.returncode, out


@pytest.mark.parametrize("runtime", ["codex", "agy", "kiro", "claude"])
def test_success_returns_file(tmp_path, runtime):
    code, out = _run(tmp_path, runtime, "ok")
    assert code == 0 and out["status"] == "ok"
    m = out["metrics"]
    assert (m["outcome"], m["source"], m["runtime"]) == ("ok", "file", runtime)
    assert pathlib.Path(m["result"]).read_text(encoding="utf-8").strip() == "レビューの結果"
    assert m["monitor_status"] == "OK"


@pytest.mark.parametrize("runtime", ["kiro", "claude"])
def test_stdout_fallback_writes_output(tmp_path, runtime):
    code, out = _run(tmp_path, runtime, "stdout")
    assert code == 0
    m = out["metrics"]
    assert m["source"] == "stdout"
    text = (tmp_path / "out.md").read_text(encoding="utf-8")
    assert text.strip() == "標準出力の結果" and "\x1b" not in text
    if runtime == "claude":
        assert m["model"] == "claude-x-1"


def test_no_result_reason_matches_monitor_record(tmp_path):
    code, out = _run(tmp_path, "codex", "none")
    assert code == 1 and out["status"] == "stopped"
    m = out["metrics"]
    assert m["outcome"] == "no_result" and m["source"] == "stderr"
    stem = pathlib.Path(m["stem"])
    rec = monitor_outcome.read_outcome(stem.parent, stem.name)
    assert (rec["status"], rec["reason"]) == (m["monitor_status"], m["reason"]) == (
        "NO_RESULT", "missing")
    assert "何も書かずに終わる" in pathlib.Path(m["result"]).read_text(encoding="utf-8")


def test_timeout_ends_within_limit(tmp_path):
    code, out = _run(tmp_path, "agy", "hang", "--timeout", "2", "--stall-timeout", "20")
    assert code == 1
    assert (out["metrics"]["outcome"], out["metrics"]["monitor_status"]) == ("timeout", "TIMEOUT")


def test_stalled_is_not_ok(tmp_path):
    code, out = _run(tmp_path, "kiro", "hang", "--timeout", "20", "--stall-timeout", "2")
    assert code == 1
    assert (out["metrics"]["outcome"], out["metrics"]["monitor_status"]) == ("stalled", "STALLED")


def test_usage_limit(tmp_path):
    code, out = _run(tmp_path, "kiro", "usage", "--timeout", "20")
    assert code == 1
    m = out["metrics"]
    assert (m["outcome"], m["monitor_status"], m["reason"]) == (
        "usage_limit", "EARLY_ERROR", "usage_limit")


def test_auth_failure_does_not_launch(tmp_path):
    code, out = _run(tmp_path, "codex", "ok", FAKE_AUTH="fail")
    assert code == 3 and out["metrics"]["outcome"] == "auth"
    assert not (tmp_path / "out.md").exists()


def test_check(tmp_path):
    env = _env(tmp_path, "agy", "ok")
    p = subprocess.run([sys.executable, str(SCRIPT), "check", "agy"], env=env,
                       capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["status"] == "ok"
    env["FAKE_AUTH"] = "fail"
    p = subprocess.run([sys.executable, str(SCRIPT), "check", "agy"], env=env,
                       capture_output=True, text=True)
    assert p.returncode == 3 and json.loads(p.stdout)["metrics"]["outcome"] == "auth"


def test_missing_cli(tmp_path):
    env = {**os.environ, "PATH": "/nonexistent"}
    p = subprocess.run([sys.executable, str(SCRIPT), "check", "kiro"], env=env,
                       capture_output=True, text=True)
    assert p.returncode == 3 and json.loads(p.stdout)["metrics"]["outcome"] == "missing_cli"
