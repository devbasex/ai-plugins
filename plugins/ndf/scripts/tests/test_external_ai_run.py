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
           [ -n "$FAKE_SID" ] && printf 'session id: %s\n' "$FAKE_SID" >&2
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
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_MODE": mode,
        "FAKE_NAME": runtime,
        "FAKE_OUT": str(tmp_path / "out.md"),
        "NDF_EXTERNAL_AI_TMP_DIR": str(tmp_path / "t"),
    }
    env.pop("NDF_SKIP_AUTH_CHECK", None)
    env.update(extra)
    return env


def _run(tmp_path, runtime, mode, *args, **extra):
    prompt = tmp_path / "p.md"
    prompt.write_text("これを読んで答える\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    p = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "run",
            runtime,
            "--prompt-file",
            str(prompt),
            "--output-file",
            str(tmp_path / "out.md"),
            "--workdir",
            str(work),
            "--poll",
            "1",
            *args,
        ],
        env=_env(tmp_path, runtime, mode, **extra),
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert step_result.validate_result(out, p.returncode) == [], (out, p.stderr[-2000:])
    return p.returncode, out


@pytest.mark.parametrize("runtime", ["codex", "agy", "kiro", "claude"])
def test_success_returns_file(tmp_path, runtime):
    code, out = _run(tmp_path, runtime, "ok")
    # CI でだけ codex がまれに落ちる（develop の run 37176848993 でも）。原因を追えるよう結果を出す
    assert code == 0 and out["status"] == "ok", out
    m = out["metrics"]
    assert (m["outcome"], m["source"], m["runtime"]) == ("ok", "file", runtime)
    assert pathlib.Path(m["result"]).read_text(encoding="utf-8").strip() == "レビューの結果"
    assert m["monitor_status"] == "OK"


def test_codex_reports_the_model_that_actually_ran(tmp_path):
    """#759 AC7 — `--model` なしの codex でも、結果の `model` にセッションの記録のモデル名が入る。"""
    import datetime as dt

    day = dt.datetime.now(dt.timezone.utc)
    sessions = tmp_path / "codex-home" / "sessions" / day.strftime("%Y/%m/%d")
    sessions.mkdir(parents=True)
    row = {"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}}
    (sessions / "rollout-x-01a0-test.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    code, out = _run(tmp_path, "codex", "ok", CODEX_HOME=str(tmp_path / "codex-home"), FAKE_SID="01a0-test")
    assert code == 0
    assert out["metrics"]["model"] == "gpt-6.1-sol"


def test_codex_without_a_session_record_says_why(tmp_path):
    """記録が無ければ `default` のまま、取れなかった理由を標準エラーに 1 行出す。"""
    prompt = tmp_path / "p.md"
    prompt.write_text("これを読んで答える\n", encoding="utf-8")
    (tmp_path / "work").mkdir()
    env = _env(tmp_path, "codex", "ok", CODEX_HOME=str(tmp_path / "empty"))
    p = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "run",
            "codex",
            "--prompt-file",
            str(prompt),
            "--output-file",
            str(tmp_path / "out.md"),
            "--workdir",
            str(tmp_path / "work"),
            "--poll",
            "1",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert json.loads(p.stdout.strip().splitlines()[-1])["metrics"]["model"] == "default"
    assert "実測値を取れなかった（no_record）" in p.stderr


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
    assert (rec["status"], rec["reason"]) == (m["monitor_status"], m["reason"]) == ("NO_RESULT", "missing")
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
    assert (m["outcome"], m["monitor_status"], m["reason"]) == ("usage_limit", "EARLY_ERROR", "usage_limit")


def test_auth_failure_does_not_launch(tmp_path):
    code, out = _run(tmp_path, "codex", "ok", FAKE_AUTH="fail")
    assert code == 3 and out["metrics"]["outcome"] == "auth"
    assert not (tmp_path / "out.md").exists()


def test_check(tmp_path):
    env = _env(tmp_path, "agy", "ok")
    p = subprocess.run([sys.executable, str(SCRIPT), "check", "agy"], env=env, capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["status"] == "ok"
    env["FAKE_AUTH"] = "fail"
    p = subprocess.run([sys.executable, str(SCRIPT), "check", "agy"], env=env, capture_output=True, text=True)
    assert p.returncode == 3 and json.loads(p.stdout)["metrics"]["outcome"] == "auth"


def test_missing_cli(tmp_path):
    env = {**os.environ, "PATH": "/nonexistent"}
    p = subprocess.run([sys.executable, str(SCRIPT), "check", "kiro"], env=env, capture_output=True, text=True)
    assert p.returncode == 3 and json.loads(p.stdout)["metrics"]["outcome"] == "missing_cli"


# ---------- cmd_run の前提の確かめとモデルの記録（現状固定） ----------


def _run_raw(tmp_path, runtime, prompt_text, *args):
    prompt = tmp_path / "p.md"
    prompt.write_text(prompt_text, encoding="utf-8")
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "run", runtime, "--prompt-file", str(prompt), "--output-file", str(tmp_path / "out.md"), *args],
        env=_env(tmp_path, runtime, "ok"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert step_result.validate_result(out, p.returncode) == [], (out, p.stderr[-2000:])
    return p.returncode, out


@pytest.mark.parametrize(
    ("prompt_text", "extra", "code", "summary"),
    [
        ("", [], 3, "プロンプトが無いか空"),
        ("x\n", ["--phase", "no-such-phase"], 2, "上限の表に無い工程: no-such-phase"),
        ("x\n", ["--workdir", "/nonexistent/ndf-work"], 3, "作業ディレクトリが無い"),
    ],
)
def test_run_preconditions_stop_before_launch(tmp_path, prompt_text, extra, code, summary):
    """現状固定 — 空のプロンプト・表に無い工程・無い作業ディレクトリは起動せずに launch_failed で止める。"""
    got, out = _run_raw(tmp_path, "kiro", prompt_text, *extra)
    assert got == code and out["status"] == "stopped"
    assert out["metrics"] == {"outcome": "launch_failed", "runtime": "kiro"}
    assert summary in out["summary"]
    assert not (tmp_path / "t").exists() and not (tmp_path / "out.md").exists()


@pytest.mark.parametrize(("args", "model"), [((), "default"), (("--model", "spec-model"), "spec-model")])
def test_run_records_the_specified_model_when_not_observed(tmp_path, args, model):
    """現状固定 — 実測できないランタイムは指定したモデル、無ければ `default` を記録する。"""
    code, out = _run(tmp_path, "kiro", "ok", *args)
    m = out["metrics"]
    assert code == 0 and m["model"] == model
    assert pathlib.Path(m["stem"]).name.startswith("kiro-")


def _load_external_ai():
    import importlib.util

    spec = importlib.util.spec_from_file_location("external_ai_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "rec, source, path, expected",
    [
        ({"status": "OK"}, "file", "/o", ("ok", "codex の結果を回収した（file）: /o", None)),
        (
            {"status": "NO_RESULT", "reason": "x"},
            "stderr",
            "/e",
            ("no_result", "codex は終わったが結果が無い（理由: x）", "stderr の末尾を読む: /e"),
        ),
        (
            {"status": "EARLY_ERROR", "reason": "r", "detail": "HTTP/2 401"},
            None,
            None,
            ("auth", "codex を止めた（EARLY_ERROR / 理由: r）", None),
        ),
        (
            {"status": "EARLY_ERROR", "reason": "usage_limit", "detail": "HTTP/2 401"},
            None,
            None,
            ("usage_limit", "codex を止めた（EARLY_ERROR / 理由: usage_limit）", None),
        ),
        ({"status": "WHATEVER", "reason": "r"}, None, None, ("launch_failed", "codex を止めた（WHATEVER / 理由: r）", None)),
        ({}, None, None, ("launch_failed", "codex を止めた（PIDFILE_BAD / 理由: pidfile_bad）", None)),
    ],
    ids=["ok", "no_result", "auth", "usage_limit_over_auth", "unknown_status", "no_record"],
)
def test_run_outcome_characterization(rec, source, path, expected):
    """現状固定 — 監視後の auth 判定・利用上限の優先・未知の状態の結末。"""
    assert _load_external_ai().run_outcome("codex", rec, source, path) == expected
