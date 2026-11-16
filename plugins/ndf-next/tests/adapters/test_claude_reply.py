"""claude の起動と答えの読み方（縛った答え・本文の JSON・失敗の種類・期限切れ・子へ渡す環境）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ndf.adapters import claude, run_log


def test_call_claude_reads_structured_output(tmp_path):
    fake = tmp_path / "claude"
    payload = {"result": "", "structured_output": {"decision": "fix"}, "total_cost_usd": 0.25}
    fake.write_text(f"#!/bin/sh\ncat > /dev/null\necho '{json.dumps(payload)}'\n")
    fake.chmod(0o755)

    got = claude.call_claude([str(fake)], "問い", 30)

    assert got == claude.ClaudeReply(True, {"decision": "fix"}, "", 0.25)


def fake_claude(tmp_path: Path, stdout: str) -> Path:
    """問いを読み捨て、`stdout` をそのまま標準出力へ書く偽の claude。"""
    out = tmp_path / "claude.stdout"
    out.write_text(stdout)
    fake = tmp_path / "claude"
    fake.write_text(f"#!/bin/sh\ncat > /dev/null\ncat '{out}'\n")
    fake.chmod(0o755)
    return fake


def test_call_claude_missing_command_is_not_ok(tmp_path):
    got = claude.call_claude([str(tmp_path / "missing")], "問い", 30)

    assert not got.ok and got.failure is claude.Failure.LAUNCH


@pytest.mark.parametrize("stdout", ["not json", json.dumps({"is_error": True, "result": "boom"})])
def test_call_claude_failures_are_not_ok(tmp_path, stdout):
    assert not claude.call_claude([str(fake_claude(tmp_path, stdout))], "問い", 30).ok


@pytest.mark.parametrize(
    ("stdout", "answer"),
    [(json.dumps({"result": '{"a": 1}'}), {"a": 1}), (json.dumps({"result": "fix です"}), None)],
)
def test_call_claude_reads_json_in_result_text(tmp_path, stdout, answer):
    assert claude.call_claude([str(fake_claude(tmp_path, stdout))], "問い", 30).answer == answer


# ---------------------------------------------------------------- 現状固定: 子へ渡す環境と失敗の形（I-011・I-012）


ENV_DUMP = (
    "import json, os, sys\n"
    "prompt = sys.stdin.read()\n"
    "seen = {k: os.environ.get(k) for k in ('NDF_RUN_ID', 'NDF_RUN_LOG', 'PIN_PARENT')}\n"
    "print(json.dumps({'result': json.dumps({'prompt': prompt, 'cwd': os.getcwd(), 'env': seen})}))\n"
)


def test_call_claude_passes_parent_env_prompt_and_cwd_without_run_log(tmp_path, no_run_env):
    got = claude.call_claude([sys.executable, "-c", ENV_DUMP], "問い", 30, cwd=tmp_path)

    assert (got.ok, got.cost, got.error) == (True, 0.0, None)
    env = {"NDF_RUN_ID": None, "NDF_RUN_LOG": None, "PIN_PARENT": "kept"}
    assert got.answer == {"prompt": "問い", "cwd": str(tmp_path), "env": env}


def test_call_claude_passes_open_run_log_to_child(no_run_env):
    log = run_log.open_run("entry", run_id="r1", location="repo")

    got = claude.call_claude([sys.executable, "-c", ENV_DUMP], "q", 30)

    assert got.answer["env"] == {"NDF_RUN_ID": "r1", "NDF_RUN_LOG": str(log.path), "PIN_PARENT": "kept"}


def test_call_claude_nonzero_exit_without_stdout_reports_stderr(no_run_env):
    script = "import sys; sys.stdin.read(); sys.stderr.write('boom'); sys.exit(3)"

    got = claude.call_claude([sys.executable, "-c", script], "q", 30)

    assert got == claude.ClaudeReply(False, None, "boom", 0.0, "claude が終了コード 3 で終えた: boom")


LIMIT_JSON = json.dumps({"is_error": True, "api_error_status": 429, "result": "You've hit your limit · resets 3pm"})


@pytest.mark.parametrize(
    "script",
    [
        f"import sys; sys.stdin.read(); print({LIMIT_JSON!r})",
        'import sys; sys.stdin.read(); sys.stderr.write("You\'ve hit your limit · resets 3pm\\n"); sys.exit(1)',
    ],
)
def test_call_claude_tells_usage_limit_apart(no_run_env, script):
    got = claude.call_claude([sys.executable, "-c", script], "q", 30)

    assert not got.ok and got.failure is claude.Failure.USAGE_LIMIT


def test_call_claude_other_error_can_be_retried(tmp_path):
    got = claude.call_claude([str(fake_claude(tmp_path, json.dumps({"is_error": True, "result": "boom"})))], "q", 30)

    assert not got.ok and got.failure is None


def test_call_claude_nonzero_exit_with_stdout_still_parses(no_run_env):
    script = 'import sys; sys.stdin.read(); print(\'{"result": "{}", "total_cost_usd": 1}\'); sys.exit(1)'

    got = claude.call_claude([sys.executable, "-c", script], "q", 30)

    assert got == claude.ClaudeReply(True, {}, "{}", 1.0)


def test_call_claude_timeout_is_not_ok(no_run_env):
    got = claude.call_claude([sys.executable, "-c", "import time; time.sleep(5)"], "q", 0.2)

    assert (got.ok, got.answer, got.raw, got.cost) == (False, None, "", 0.0)
    assert got.error.startswith("claude が期限を超えた: ") and got.failure is claude.Failure.TIMEOUT
