"""claude の起動と回答の読み方（縛った回答・本文の JSON・失敗の種類・期限切れ・子へ渡す環境）。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from ndf.adapters import claude, run_log


def test_call_claude_reads_structured_output(tmp_path):
    fake = tmp_path / "claude"
    payload = {"result": "", "structured_output": {"decision": "fix"}, "total_cost_usd": 0.25}
    fake.write_text(f"#!/bin/sh\ncat > /dev/null\necho '{json.dumps(payload)}'\n")
    fake.chmod(0o755)

    got = claude.call_claude([str(fake)], "質問", 30)

    assert got == claude.ClaudeReply(True, {"decision": "fix"}, "", 0.25)


def fake_claude(tmp_path: Path, stdout: str) -> Path:
    """質問を読み捨て、`stdout` をそのまま標準出力へ書く偽の claude。"""
    out = tmp_path / "claude.stdout"
    out.write_text(stdout)
    fake = tmp_path / "claude"
    fake.write_text(f"#!/bin/sh\ncat > /dev/null\ncat '{out}'\n")
    fake.chmod(0o755)
    return fake


def test_call_claude_missing_command_is_not_ok(tmp_path):
    got = claude.call_claude([str(tmp_path / "missing")], "質問", 30)

    assert not got.ok and got.failure is claude.Failure.LAUNCH


@pytest.mark.parametrize("stdout", ["not json", json.dumps({"is_error": True, "result": "boom"})])
def test_call_claude_failures_are_not_ok(tmp_path, stdout):
    assert not claude.call_claude([str(fake_claude(tmp_path, stdout))], "質問", 30).ok


@pytest.mark.parametrize(
    ("stdout", "answer"),
    [(json.dumps({"result": '{"a": 1}'}), {"a": 1}), (json.dumps({"result": "fix です"}), None)],
)
def test_call_claude_reads_json_in_result_text(tmp_path, stdout, answer):
    assert claude.call_claude([str(fake_claude(tmp_path, stdout))], "質問", 30).answer == answer


# ---------------------------------------------------------------- 現状固定: 子へ渡す環境と失敗の形（I-011・I-012）


ENV_DUMP = (
    "import json, os, sys\n"
    "prompt = sys.stdin.read()\n"
    "seen = {k: os.environ.get(k) for k in ('NDF_RUN_ID', 'NDF_RUN_LOG', 'PIN_PARENT')}\n"
    "print(json.dumps({'result': json.dumps({'prompt': prompt, 'cwd': os.getcwd(), 'env': seen})}))\n"
)


def test_call_claude_passes_parent_env_prompt_and_cwd_without_run_log(tmp_path, no_run_env):
    got = claude.call_claude([sys.executable, "-c", ENV_DUMP], "質問", 30, cwd=tmp_path)

    assert (got.ok, got.cost, got.error) == (True, 0.0, None)
    env = {"NDF_RUN_ID": None, "NDF_RUN_LOG": None, "PIN_PARENT": "kept"}
    assert got.answer == {"prompt": "質問", "cwd": str(tmp_path), "env": env}


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


# ---------------------------------------------------------------- 中継の確かめの差し込み（#3604 の 11。確かめの本体は relay のテスト）


RECORD = "import json, os, sys; sys.stdin.read(); open(os.environ['OUT'], 'a').write('x'); print(json.dumps({'result': '{}'}))"
LIMITED = "import json, sys; sys.stdin.read(); print(json.dumps({'is_error': True, 'api_error_status': 429, 'result': 'limit'}))"


class FakeChecks:
    """差し込む偽の確かめ。`begin` は `got` を返し、`retry` は `again` を返す。呼ばれた回数を数える。"""

    def __init__(self, got, again=None):
        self.got, self.again, self.calls = got, again, []

    def begin(self, argv, env, cwd, keys):
        self.calls.append(("begin", keys))
        return self.got

    def retry(self, argv, env, cwd, keys):
        self.calls.append(("retry", keys))
        return self.again


def _inherit(monkeypatch, tmp_path, name: str) -> None:
    """中継の子の環境（中継が確定した起動先 `NDF_CLAUDE_ACCOUNT` と、中継のマーカー `NDF_RELAY_DIR`）を作る。"""
    monkeypatch.setenv("NDF_CLAUDE_ACCOUNT", name)
    monkeypatch.setenv("NDF_RELAY_DIR", str(tmp_path / "relay"))


def test_without_injected_checks_an_inherited_account_launches_as_before(tmp_path, monkeypatch):
    monkeypatch.setattr(claude, "relay_checks", None)
    _inherit(monkeypatch, tmp_path, "work1")
    monkeypatch.setenv("OUT", str(tmp_path / "out"))

    got = claude.call_claude([sys.executable, "-c", RECORD], "q", 30)

    assert got.ok and (tmp_path / "out").read_text() == "x"


def test_injected_checks_are_not_used_for_the_default_login(tmp_path, monkeypatch):
    checks = FakeChecks(claude.AccountRefused(claude.Failure.REFUSED, "x"))
    monkeypatch.setattr(claude, "relay_checks", checks)
    monkeypatch.delenv("NDF_CLAUDE_ACCOUNT", raising=False)
    monkeypatch.setenv("OUT", str(tmp_path / "out"))

    assert claude.call_claude([sys.executable, "-c", RECORD], "q", 30).ok and checks.calls == []


def test_4136_an_account_exported_outside_the_relay_is_not_checked(tmp_path, monkeypatch):
    """中継を経ずに利用者が `NDF_CLAUDE_ACCOUNT` を export しただけなら、継いだ起動先として確かめずに今までどおり起動する。"""
    checks = FakeChecks(claude.AccountRefused(claude.Failure.LAUNCH, "x"))
    monkeypatch.setattr(claude, "relay_checks", checks)
    monkeypatch.setenv("NDF_CLAUDE_ACCOUNT", "work1")
    monkeypatch.delenv("NDF_RELAY_DIR", raising=False)
    monkeypatch.setenv("OUT", str(tmp_path / "out"))

    got = claude.call_claude([sys.executable, "-c", RECORD], "q", 30)

    assert got.ok and (tmp_path / "out").read_text() == "x" and checks.calls == []


def test_a_refusal_from_the_injected_checks_does_not_launch(tmp_path, monkeypatch):
    checks = FakeChecks(claude.AccountRefused(claude.Failure.REFUSED, "key_gate"))
    monkeypatch.setattr(claude, "relay_checks", checks)
    _inherit(monkeypatch, tmp_path, "key:k1")
    monkeypatch.setenv("OUT", str(tmp_path / "out"))

    got = claude.call_claude([sys.executable, "-c", RECORD, *claude.PRINT_JSON], "q", 30)

    assert (got.ok, got.failure, got.error) == (False, claude.Failure.REFUSED, "key_gate")
    assert not (tmp_path / "out").exists() and checks.calls == [("begin", False)]


def test_a_limit_is_retried_once_with_the_run_from_the_injected_checks(tmp_path, monkeypatch):
    ended = []
    out = tmp_path / "out"
    first = claude.AccountRun(dict(os.environ), [sys.executable, "-c", LIMITED], end=lambda launched: ended.append(("first", launched)))
    env = {**os.environ, "OUT": str(out)}
    again = claude.AccountRun(env, [sys.executable, "-c", RECORD], end=lambda launched: ended.append(("again", launched)))
    checks = FakeChecks(first, again)
    monkeypatch.setattr(claude, "relay_checks", checks)
    _inherit(monkeypatch, tmp_path, "work1")

    got = claude.call_claude([sys.executable, "-c", LIMITED], "q", 30)

    assert got.ok and out.read_text() == "x"
    assert checks.calls == [("begin", False), ("retry", False)] and ended == [("first", True), ("again", True)]


def test_a_failed_attach_stops_the_gated_child_before_it_becomes_claude(tmp_path, monkeypatch):
    """関所の子は `attach` が理由を返すと開けられずに止まり、偽の claude は呼ばれず、`end` は起動しなかったとして呼ばれる。"""
    ended = []

    def gate(argv, fd, status_fd):
        code = "import os, sys; fd = int(sys.argv[1]); go = os.read(fd, 1); os._exit(0 if go != b'1' else os.execvp(sys.argv[3], sys.argv[3:]))"
        return [sys.executable, "-c", code, str(fd), str(status_fd), *argv]

    run = claude.AccountRun(
        {**os.environ, "OUT": str(tmp_path / "out")},
        [sys.executable, "-c", RECORD],
        gate=gate,
        attach=lambda pid: "lease_lost",
        end=ended.append,
    )
    monkeypatch.setattr(claude, "relay_checks", FakeChecks(run))
    _inherit(monkeypatch, tmp_path, "work1")

    got = claude.call_claude([sys.executable, "-c", RECORD], "q", 30)

    assert got.failure is claude.Failure.LAUNCH and "lease_lost" in str(got.error)
    assert not (tmp_path / "out").exists() and ended == [False]


# 偽の関所: 開けの 1 バイトを待ち、`MODE` が errno なら起動の知らせに errno の数値（ENOENT）を書いて終わり、silent なら知らせを返さずに居残る
FAKE_GATE = (
    "import os, sys, time; fd, sfd = int(sys.argv[1]), int(sys.argv[2]); os.read(fd, 1)\n"
    "if sys.argv[3] == 'errno':\n    os.write(sfd, str(2).encode()); os._exit(1)\n"  # 本物の関所と同じく errno を数値で書く
    "time.sleep(30)"
)


def _gated_run(tmp_path, mode, ended, pids, attach=None):
    def gate(argv, fd, status_fd):
        return [sys.executable, "-c", FAKE_GATE, str(fd), str(status_fd), mode, *argv]

    def record(pid):
        pids.append(pid)
        return None if attach is None else attach(pid)

    return claude.AccountRun(
        {**os.environ, "OUT": str(tmp_path / "out")}, [sys.executable, "-c", RECORD], gate=gate, attach=record, end=ended.append
    )


def _reaped(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.mark.parametrize(("mode", "why"), [("errno", "launch_failed errno ENOENT"), ("silent", "gate_timeout")])
def test_a_gate_that_does_not_become_claude_is_stopped_and_ended(tmp_path, monkeypatch, mode, why):
    ended, pids = [], []
    real = claude.limits.seconds
    monkeypatch.setattr(claude.limits, "seconds", lambda key: 0.5 if key == "relay.gate-exec" else real(key))
    monkeypatch.setattr(claude, "relay_checks", FakeChecks(_gated_run(tmp_path, mode, ended, pids)))
    _inherit(monkeypatch, tmp_path, "work1")

    got = claude.call_claude([sys.executable, "-c", RECORD], "q", 30)

    assert got.failure is claude.Failure.LAUNCH and why in str(got.error)
    assert not (tmp_path / "out").exists() and ended == [False] and _reaped(pids[0])


def test_a_gate_that_cannot_be_spawned_ends_without_launching(tmp_path, monkeypatch):
    ended = []
    run = claude.AccountRun(dict(os.environ), ["x"], gate=lambda argv, fd, sfd: [str(tmp_path / "missing")], end=ended.append)
    monkeypatch.setattr(claude, "relay_checks", FakeChecks(run))
    _inherit(monkeypatch, tmp_path, "work1")

    got = claude.call_claude([sys.executable, "-c", RECORD], "q", 30)

    assert got.failure is claude.Failure.LAUNCH and ended == [False]


def test_an_os_error_after_the_gate_spawned_stops_the_child(tmp_path, monkeypatch):
    ended, pids = [], []

    def broken(fd, seconds):
        raise OSError("読めない")

    monkeypatch.setattr(claude.process_group, "read_status", broken)
    monkeypatch.setattr(claude, "relay_checks", FakeChecks(_gated_run(tmp_path, "silent", ended, pids)))
    _inherit(monkeypatch, tmp_path, "work1")

    got = claude.call_claude([sys.executable, "-c", RECORD], "q", 30)

    assert got.failure is claude.Failure.LAUNCH and "gate_error OSError" in str(got.error)
    assert ended == [False] and _reaped(pids[0])


def test_an_exception_from_attach_stops_the_child_and_ends(tmp_path, monkeypatch):
    ended, pids = [], []

    def boom(pid):
        raise RuntimeError("attach")

    monkeypatch.setattr(claude, "relay_checks", FakeChecks(_gated_run(tmp_path, "silent", ended, pids, boom)))
    _inherit(monkeypatch, tmp_path, "work1")

    with pytest.raises(RuntimeError):
        claude.call_claude([sys.executable, "-c", RECORD], "q", 30)

    assert ended == [False] and _reaped(pids[0])


def test_the_entry_point_injects_the_relay_checks(monkeypatch):
    import runpy

    from ndf import cli
    from ndf.domains.relay import plan_claude

    monkeypatch.setattr(claude, "relay_checks", None)
    monkeypatch.setattr(cli, "main", lambda: 0)
    codes = []
    monkeypatch.setattr(sys, "exit", codes.append)

    runpy.run_module("ndf", run_name="__main__")

    assert claude.relay_checks is plan_claude and codes == [0]


def test_an_exception_while_talking_reaps_the_child_before_the_lease_is_dropped(tmp_path, monkeypatch):
    """関所を通った後の応答待ちで例外（中断）が来ても、子の組を止めて回収してから `end` を打つ。"""
    pids, order = [], []

    def gate(argv, fd, status_fd):
        code = "import os, sys; fd = int(sys.argv[1]); os.read(fd, 1); os.close(int(sys.argv[2])); os.execvp(sys.argv[3], sys.argv[3:])"
        return [sys.executable, "-c", code, str(fd), str(status_fd), *argv]

    real = claude.subprocess.Popen.communicate

    def interrupted(self, *args, **kwargs):
        if self.pid not in pids:  # 下限の確かめの --version などはそのまま通す
            return real(self, *args, **kwargs)
        raise KeyboardInterrupt

    run = claude.AccountRun(
        dict(os.environ),
        [sys.executable, "-c", "import time; time.sleep(30)"],
        gate=gate,
        attach=lambda pid: pids.append(pid),
        end=lambda launched: order.append((launched, _reaped(pids[0]))),
    )
    monkeypatch.setattr(claude, "relay_checks", FakeChecks(run))
    _inherit(monkeypatch, tmp_path, "work1")
    monkeypatch.setattr(claude.subprocess.Popen, "communicate", interrupted)

    with pytest.raises(KeyboardInterrupt):
        claude.call_claude([sys.executable, "-c", RECORD], "q", 30)

    assert order == [(True, True)]


def test_a_failed_second_pipe_closes_the_first_and_ends(tmp_path, monkeypatch):
    ended, made = [], []
    real = os.pipe

    def pipe():
        if made:
            raise OSError(24, "Too many open files")
        made.extend(real())
        return made[0], made[1]

    run = claude.AccountRun(dict(os.environ), ["x"], gate=lambda argv, fd, sfd: list(argv), end=ended.append)
    monkeypatch.setattr(claude, "relay_checks", FakeChecks(run))
    _inherit(monkeypatch, tmp_path, "work1")
    monkeypatch.setattr(claude.os, "pipe", pipe)

    got = claude.call_claude([sys.executable, "-c", RECORD], "q", 30)

    monkeypatch.setattr(claude.os, "pipe", real)
    assert got.failure is claude.Failure.LAUNCH and ended == [False]
    for fd in made:
        with pytest.raises(OSError):
            os.fstat(fd)
