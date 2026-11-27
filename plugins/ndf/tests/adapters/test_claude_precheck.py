"""`_prefaced` の起動の直前の確かめ（`recheck` と CLI の下限）と `claude_precheck.require_claude` の今の振る舞い（I-010 の作り直しの前の固定）。

スロットの取得（`launch_slot.gate`）・`Popen`・CLI の下限の確かめは偽物にし、実プロセスを起動しない。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import pytest

from ndf.adapters import claude, claude_precheck, cli_floor, launch_slot

REPLY = json.dumps({"type": "result", "is_error": False, "result": "{}", "total_cost_usd": 0.0})
# 偽のスロットが子へ足す環境
SLOT_ENV = {"NDF_SLOT_HELD": "1"}


class FakeProc:
    """管を持たない偽のプロセス。"""

    def __init__(self) -> None:
        self.returncode = 0
        self.pid = 0
        self.stdin = self.stdout = self.stderr = None

    def communicate(self, prompt: str = "", timeout: float = 0) -> tuple[str, str]:
        return REPLY, ""


class Seen:
    """偽物が受けた呼び出しの順と引数。"""

    def __init__(self) -> None:
        self.order: list[str] = []
        self.floor: list[tuple[str, str, dict, Optional[Path]]] = []
        self.popen: list[list[str]] = []
        self.ended: list[bool] = []
        self.too_old: Optional[cli_floor.TooOld] = None
        self.waited = True  # 下限の確かめが `claude --version` の終わりを待ったか（偽なら記録を再利用した）


@pytest.fixture
def seen(monkeypatch) -> Seen:
    got = Seen()

    @contextmanager
    def gate(agent, *, cwd=None, env=None, wait=None, ensure=True):
        got.order.append("slot")
        yield dict(SLOT_ENV)

    def require(name, argv0, env=None, cwd=None):
        got.order.append("floor")
        got.floor.append((name, argv0, dict(env or {}), cwd))
        if got.too_old is not None:
            raise got.too_old
        return got.waited

    def popen(argv, **kwargs):
        got.order.append("launch")
        got.popen.append(list(argv))
        return FakeProc()

    monkeypatch.setattr(launch_slot, "gate", gate)
    monkeypatch.setattr(claude_precheck.cli_floor, "require", require)
    monkeypatch.setattr(claude.subprocess, "Popen", popen)
    return got


def _run(seen: Seen, recheck=None, argv=("claude", "-p")) -> claude.AccountRun:
    def recheck_logged():
        seen.order.append("recheck")
        return recheck()

    return claude.AccountRun({"A": "1"}, list(argv), end=seen.ended.append, recheck=None if recheck is None else recheck_logged)


def test_i010_recheck_refusal_returns_refused_without_floor_or_launch(seen, tmp_path):
    """recheck が 1 行を返せば下限を確かめず起動もせず、その行を誤りにした `Failure.REFUSED`。end は偽で 1 回。"""
    got = claude._prefaced(_run(seen, lambda: "上書きが足された"), "q", 30, tmp_path, claude.DEFAULT_LAUNCH)

    assert got == claude.ClaudeReply(False, None, "", 0.0, "上書きが足された", claude.Failure.REFUSED)
    assert seen.order == ["slot", "recheck"]
    assert seen.floor == [] and seen.popen == [] and seen.ended == [False]


def test_i010_recheck_refusal_wins_even_when_the_floor_is_skipped(seen, tmp_path):
    """`cli_floor=False` でも recheck の拒否は効く。"""
    options = claude.LaunchOptions(cli_floor=False)
    got = claude._prefaced(_run(seen, lambda: "拒否"), "q", 30, tmp_path, options)

    assert got.failure is claude.Failure.REFUSED and got.error == "拒否" and seen.popen == [] and seen.ended == [False]


def test_i010_recheck_passes_then_floor_then_launch(seen, tmp_path):
    """recheck が None なら、スロット → recheck → 下限 → 起動の順で打ち、end は真で 1 回。"""
    got = claude._prefaced(_run(seen, lambda: None), "q", 30, tmp_path, claude.DEFAULT_LAUNCH)

    assert got.ok and seen.order == ["slot", "recheck", "floor", "launch"] and seen.ended == [True]


def test_i010_without_recheck_the_floor_is_still_checked(seen, tmp_path):
    """recheck を持たない起動は確かめ直しを飛ばし、下限を確かめて起動する。"""
    got = claude._prefaced(_run(seen), "q", 30, tmp_path, claude.DEFAULT_LAUNCH)

    assert got.ok and seen.order == ["slot", "floor", "launch"] and seen.ended == [True]


def test_i010_floor_gets_argv0_the_slot_env_and_cwd(seen, tmp_path):
    """下限の確かめは argv[0]・スロットの環境と AI の子のマーカーを足した環境・作業ディレクトリで打つ。"""
    claude._prefaced(_run(seen, argv=("/opt/claude", "-p")), "q", 30, tmp_path, claude.DEFAULT_LAUNCH)

    assert seen.floor == [("claude", "/opt/claude", {"A": "1", **SLOT_ENV, "NDF_AI_CHILD": "1"}, tmp_path)]


def test_i010_too_old_floor_refuses_without_launch(seen, tmp_path):
    """下限より古ければ起動せず、`TooOld` の文を誤りにした `Failure.REFUSED`。end は偽で 1 回。"""
    seen.too_old = cli_floor.TooOld("claude", "/opt/claude", "1.0.0", "2.0.0")
    got = claude._prefaced(_run(seen, lambda: None), "q", 30, tmp_path, claude.DEFAULT_LAUNCH)

    assert got == claude.ClaudeReply(False, None, "", 0.0, str(seen.too_old), claude.Failure.REFUSED)
    assert seen.order == ["slot", "recheck", "floor"] and seen.popen == [] and seen.ended == [False]


def test_i010_skipped_floor_launches_even_if_too_old(seen, tmp_path):
    """`cli_floor=False` なら下限を確かめずに起動する（古いと分かっていても）。"""
    seen.too_old = cli_floor.TooOld("claude", "/opt/claude", "1.0.0", "2.0.0")
    got = claude._prefaced(_run(seen, lambda: None), "q", 30, tmp_path, claude.LaunchOptions(cli_floor=False))

    assert got.ok and seen.order == ["slot", "recheck", "launch"] and seen.ended == [True]


def test_i010_gated_run_refused_by_recheck_does_not_open_the_gate(seen, tmp_path, monkeypatch):
    """関所を持つ起動でも、recheck か下限で断れば関所の起動（`_gated`）へ進まない。"""
    monkeypatch.setattr(claude, "_gated", lambda run, cwd: pytest.fail("関所へ進んだ"))
    run = _run(seen, lambda: "拒否")._replace(gate=lambda argv, r, w: list(argv))
    got = claude._prefaced(run, "q", 30, tmp_path, claude.DEFAULT_LAUNCH)
    assert got.failure is claude.Failure.REFUSED and seen.ended == [False]

    seen.too_old = cli_floor.TooOld("claude", "/opt/claude", "1.0.0", "2.0.0")
    run = _run(seen, lambda: None)._replace(gate=lambda argv, r, w: list(argv))
    got = claude._prefaced(run, "q", 30, tmp_path, claude.DEFAULT_LAUNCH)
    assert got.failure is claude.Failure.REFUSED and seen.ended == [False, False]


def test_i010_require_claude_without_check_returns_none_without_checking(seen, tmp_path):
    """`require_claude` は check が偽なら確かめずに None。"""
    seen.too_old = cli_floor.TooOld("claude", "/opt/claude", "1.0.0", "2.0.0")

    assert claude_precheck.require_claude(claude.AccountRun({}, ["claude"]), tmp_path, False) is None and seen.floor == []


def test_i010_require_claude_passing_floor_returns_none(seen, tmp_path):
    """`require_claude` は下限を満たせば None（argv[0]・env・cwd を渡す）。"""
    got = claude_precheck.require_claude(claude.AccountRun({"B": "2"}, ["/x/claude", "-p"]), tmp_path, True)

    assert got is None and seen.floor == [("claude", "/x/claude", {"B": "2"}, tmp_path)]


def test_i010_require_claude_too_old_returns_refused_reply(seen, tmp_path):
    """`require_claude` は古ければ `TooOld` の文を理由にした `Check.FLOOR` の `Stop`（`_prefaced` が `Failure.REFUSED` の回答にする）。"""
    seen.too_old = cli_floor.TooOld("claude", "/x/claude", "1.0.0", "2.0.0")
    got = claude_precheck.require_claude(claude.AccountRun({}, ["/x/claude"]), None, True)

    assert got == claude_precheck.Stop(claude_precheck.Check.FLOOR, str(seen.too_old))


def _verify_logged(seen: Seen, reason: Optional[str] = None) -> claude.LaunchOptions:
    def verify() -> Optional[str]:
        seen.order.append("verify")
        return reason

    return claude.LaunchOptions(verify=verify, ready=lambda: seen.order.append("ready"))


def test_issue_1959_verify_runs_before_the_floor_and_ready_after_every_check(seen, tmp_path):
    """照らし直し（`verify`）は確かめ直しの後・下限の前と、下限の後・本体の起動の直前に打ち、起動の準備（`ready`）は確かめをすべて通った後に打つ。"""
    got = claude._prefaced(_run(seen, lambda: None), "q", 30, tmp_path, _verify_logged(seen))

    assert got.ok and seen.order == ["slot", "recheck", "verify", "floor", "verify", "ready", "launch"]


def test_issue_1959_a_floor_from_the_record_verifies_only_once(seen, tmp_path):
    """下限の確かめが記録を再利用して待たなかった起動は、照らし直しを 1 回で済ませる。"""
    seen.waited = False
    got = claude._prefaced(_run(seen, lambda: None), "q", 30, tmp_path, _verify_logged(seen))

    assert got.ok and seen.order == ["slot", "recheck", "verify", "floor", "ready", "launch"]


def test_issue_1959_a_revoke_during_the_floor_check_stops_the_launch(seen, tmp_path):
    """下限の確かめ（`claude --version` の終了を待つ）の間に承認が取り消されたら、本体を起動せず、起動の準備も打たない。"""
    answers = iter([None, "取り消された"])

    def verify() -> Optional[str]:
        seen.order.append("verify")
        return next(answers)

    options = claude.LaunchOptions(verify=verify, ready=lambda: seen.order.append("ready"))
    got = claude._prefaced(_run(seen, lambda: None), "q", 30, tmp_path, options)

    assert got.failure is claude.Failure.LAUNCH and seen.order == ["slot", "recheck", "verify", "floor", "verify"] and seen.popen == []


def test_issue_1959_a_refused_verify_stops_before_the_floor(seen, tmp_path):
    """照らし直しで止まる起動は、下限（`claude --version`）を確かめず、起動の準備も打たない。"""
    got = claude._prefaced(_run(seen, lambda: None), "q", 30, tmp_path, _verify_logged(seen, "取り消された"))

    assert got.failure is claude.Failure.LAUNCH and seen.order == ["slot", "recheck", "verify"] and seen.floor == []


def test_issue_1959_a_too_old_cli_stops_before_ready(seen, tmp_path):
    """下限より古ければ起動の準備を打たない（worker は前の結果ファイルを消さない）。"""
    seen.too_old = cli_floor.TooOld("claude", "/opt/claude", "1.0.0", "2.0.0")
    got = claude._prefaced(_run(seen, lambda: None), "q", 30, tmp_path, _verify_logged(seen))

    assert got.failure is claude.Failure.REFUSED and seen.order == ["slot", "recheck", "verify", "floor"] and seen.popen == []


def test_issue_2944_a_deadline_used_up_while_waiting_stops_the_launch(seen, tmp_path):
    """期限の残り（`left`）はスロットと確かめの後に測る。残りが無ければ起動も準備もせず `Failure.TIMEOUT`、あれば子の期限を残りにする。"""
    spent = claude.LaunchOptions(left=lambda: 0.0, ready=lambda: seen.order.append("ready"))
    got = claude._prefaced(_run(seen), "q", 30, tmp_path, spent)

    assert got.failure is claude.Failure.TIMEOUT and seen.order == ["slot", "floor"] and seen.ended == [False]


def test_issue_2944_the_child_gets_the_time_left_after_the_checks(seen, tmp_path, monkeypatch):
    given: list[float] = []
    monkeypatch.setattr(FakeProc, "communicate", lambda self, prompt="", timeout=0: given.append(timeout) or (REPLY, ""))
    got = claude._prefaced(_run(seen), "q", 30, tmp_path, claude.LaunchOptions(left=lambda: 7.0))

    assert got.ok and given == [7.0]
