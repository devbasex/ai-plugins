"""待ちの結果（wait_decide。SF7）: 人の手が要るものを種類つきで返し、next に続きの待ちを載せる。"""

from __future__ import annotations

import json

import pytest
from result_json import last_result

from ndf.contracts import causes, result
from ndf.core import durable
from ndf.core.plan import cli as plan_cli
from ndf.core.plan import runner
from ndf.core.plan.wait_decide import Watched, attention, settled, timed_out

from . import _plan_helpers as H

AGAIN = result.command("plan", "wait", "a.json")


def _valid(out: dict) -> dict:
    assert result.errors(out, result.code_for(out)) == []
    return out


def test_settled_waits_while_plans_run():
    assert not settled([Watched("a", None), Watched("b", "ok")])
    assert settled([Watched("a", None), Watched("b", "gate")])
    assert settled([Watched("a", "ok")])


def test_a_stop_returns_its_kind_and_the_next_wait():
    out = _valid(attention("plan", [Watched("a", "ok"), Watched("b", "stopped", "test_failed", "落ちた")], AGAIN))
    assert out["status"] == "stopped"
    assert out["cause"]["kind"] == "test_failed"
    assert out["next"] == AGAIN
    assert out["items"][1]["cause"]["instance"] == "b"
    assert result.code_for(out) == causes.exit_code("test_failed")


def _answer(plan: str) -> dict:
    return {"approved": result.command("plan", "next", plan, "--answer", "approved"), "rejected": None}


def test_a_gate_carries_its_routes():
    routes = _answer("a.json")
    out = _valid(attention("plan", [Watched("a", "gate", routes=routes)], AGAIN))
    assert out["status"] == "gate"
    assert out["routes"] == routes
    assert out["items"][0]["gate"] is True
    assert out["next"] == AGAIN


def test_two_gates_keep_answer_keys_and_each_plan_keeps_its_routes():
    """結果の routes のキーは回答の語彙だけで、承認ゲートが 2 本あれば先頭の分にし、各行にそれぞれの分を残す。"""
    out = _valid(attention("plan", [Watched("a", "gate", routes=_answer("a.json")), Watched("b", "gate", routes=_answer("b.json"))], AGAIN))
    assert out["routes"] == _answer("a.json")
    assert [i["routes"] for i in out["items"]] == [_answer("a.json"), _answer("b.json")]


def test_a_gate_without_routes_retries_the_same_wait():
    out = _valid(attention("plan", [Watched("a", "gate")], AGAIN))
    assert out["routes"] == {"retry": AGAIN}


def test_all_done_is_ok():
    out = _valid(attention("plan", [Watched("a", "ok")], AGAIN))
    assert out["status"] == "ok"


def test_timeout_is_wait_timeout_with_the_same_wait():
    out = _valid(timed_out("plan", [Watched("a", None)], 30.0, AGAIN))
    assert out["cause"]["kind"] == "wait_timeout"
    assert out["next"] == AGAIN


# --- 現状固定（I-020: `ndf plan wait`（`_cmd_wait`）の時計と待ちを引数で渡す形にする前） ------------------------


@pytest.fixture
def waiting(tmp_path, monkeypatch):
    """プラン 1 本と、その状態の保存先（`--state-dir` で渡す）。作業ディレクトリはプランのリポジトリへ移す。"""
    repo = H.make_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    plan = str(repo / H.write_plan(repo, "w", [H.py_step("s", "pass")]))
    return plan, tmp_path / "sd"


def _wait(capsys, *argv: str) -> tuple[int, dict]:
    code = plan_cli.main(["wait", *argv])
    return code, last_result(capsys.readouterr().out)


def _started(sdir) -> str:
    sdir.mkdir(exist_ok=True)
    (sdir / runner.RUN_FILE).write_text(json.dumps({"key": "k1"}), encoding="utf-8")
    return durable.run_name("k1")


def _finished(sdir, run: str, done: dict) -> None:
    path = runner.result_path(sdir, run)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(done), encoding="utf-8")


def test_wait_refuses_a_state_dir_for_two_plans(waiting, capsys):
    plan, sdir = waiting
    code, out = _wait(capsys, plan, plan, "--state-dir", str(sdir))
    assert code == causes.exit_code("invalid")
    assert out["cause"] == {"kind": "invalid", "detail": "--state-dir はプラン 1 本のときだけ渡せる"} and "next" not in out


def test_wait_reports_an_unreadable_plan_as_stopped(tmp_path, capsys):
    missing = str(tmp_path / "none.json")
    code, out = _wait(capsys, missing, "--timeout", "0")
    detail = f"プラン {missing} を読めない: No such file or directory"
    assert code == causes.exit_code("invalid")
    assert out["cause"] == {"kind": "invalid", "detail": detail, "instance": "none"}
    assert out["next"] == result.command("plan", "wait", missing, "--timeout", "0.0")


@pytest.mark.parametrize("started", [False, True])
def test_wait_times_out_while_the_plan_has_no_result(waiting, capsys, started):
    plan, sdir = waiting
    if started:
        _started(sdir)
    code, out = _wait(capsys, plan, "--state-dir", str(sdir), "--timeout", "0")
    assert code == causes.exit_code("wait_timeout") and out["cause"]["kind"] == "wait_timeout"
    assert out["items"] == [{"kind": "plan", "name": "w", "status": None}]
    assert out["next"] == result.command("plan", "wait", plan, "--state-dir", str(sdir), "--timeout", "0.0")


def test_wait_reads_the_gate_marker_and_then_the_result(waiting, capsys):
    plan, sdir = waiting
    run = _started(sdir)
    routes = {"approved": result.command("plan", "next", plan, "--answer", "approved")}
    shown = {"presented_id": "p1", "presentation_path": "m.md", "production": True}
    marker = {"step": "s", "seq": 3, "run": run, "shown": shown, "routes": routes}
    runner.waiting_path(sdir, run).write_text(json.dumps(marker), encoding="utf-8")
    # 目印の外の提示の写し（待ちの間に出し直した資料）は読まず、資料と回答コマンドを目印の一組から作る（r2-2）
    runner.write_json(runner.step_presented_path(sdir, "s"), {"step": "s", "presented_id": "p2", "presentation_path": "n.md"})
    code, out = _wait(capsys, plan, "--state-dir", str(sdir), "--timeout", "0")
    pin = ["--waiting", f"{run}:s:3", "--presented", "p1"]
    pinned = {"approved": result.command("plan", "next", plan, "--answer", "approved", *pin)}
    assert out["status"] == "gate" and code == result.code_for(out) and out["routes"] == pinned
    # 待っている行は、回答コマンドと同じ提示の承認資料と「本番へ反映され得るか」を持つ（#4409）
    assert (out["items"][0]["presentation_path"], out["items"][0]["production"]) == ("m.md", True)
    # 結果のバックアップがあれば、承認の目印より先に読む
    _finished(sdir, run, {"status": "stopped", "cause": {"kind": "test_failed", "detail": "落ちた"}})
    code, out = _wait(capsys, plan, "--state-dir", str(sdir), "--timeout", "0")
    assert code == causes.exit_code("test_failed")
    assert out["cause"] == {"kind": "test_failed", "detail": "落ちた", "instance": "w"}
    _finished(sdir, run, {"status": "ok"})
    code, out = _wait(capsys, plan, "--state-dir", str(sdir), "--timeout", "0")
    assert code == 0 and out["summary"] == "1 本のプランが終わった" and "next" not in out


def test_a_stored_result_of_a_newer_version_is_watched_as_stopped(tmp_path):
    plan, sdir = tmp_path / "w.json", tmp_path / "sd"
    plan.write_text(json.dumps({"name": "w", "steps": [H.py_step("s", "pass")]}), encoding="utf-8")
    _finished(sdir, _started(sdir), {"status": "gate", "version": 2, "routes": {"approved": None}})
    got = plan_cli._watched(plan, str(sdir))
    assert (got.status, got.kind, got.routes) == ("stopped", "precondition", None)


class _Clock:
    """`time.monotonic` と `time.sleep` の代わり。眠った分だけ進み、眠った秒を記録する。"""

    def __init__(self, on_sleep=None):
        self.now = 100.0
        self.slept: list[float] = []
        self.on_sleep = on_sleep

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep(len(self.slept))


def test_wait_polls_at_the_contract_interval_until_the_timeout(waiting, capsys, monkeypatch):
    plan, sdir = waiting
    clock = _Clock()
    monkeypatch.setattr(plan_cli.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(plan_cli.time, "sleep", clock.sleep)
    code, out = _wait(capsys, plan, "--state-dir", str(sdir), "--timeout", "2.5")
    assert clock.slept == [1, 1, 1]
    assert code == causes.exit_code("wait_timeout") and out["metrics"] == {"waited_seconds": 3.0}


def test_wait_returns_as_soon_as_the_plan_settles(waiting, capsys, monkeypatch):
    plan, sdir = waiting
    run = _started(sdir)
    clock = _Clock(lambda n: n == 2 and _finished(sdir, run, {"status": "ok"}))
    monkeypatch.setattr(plan_cli.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(plan_cli.time, "sleep", clock.sleep)
    code, out = _wait(capsys, plan, "--state-dir", str(sdir), "--timeout", "60")
    assert clock.slept == [1, 1] and code == 0 and out["status"] == "ok"


def test_poll_reads_until_the_deadline_with_the_given_clock():
    """I-020: 時計・待ち方・間隔を引数で渡し、期限の直前は読み直し、期限に達した読みで止まる。"""
    clock = _Clock()
    reads: list[float] = []

    def read() -> list[Watched]:
        reads.append(clock.now)
        return [Watched("a", None)]

    poll = plan_cli.Poll(monotonic=clock.monotonic, sleep=clock.sleep, interval=0.5)
    out = plan_cli._poll(read, 1.0, AGAIN, poll)
    assert reads == [100.0, 100.5, 101.0] and clock.slept == [0.5, 0.5]
    assert out == timed_out("plan", [Watched("a", None)], 1.0, AGAIN)


def test_poll_returns_the_settled_result_even_on_the_read_after_the_deadline():
    """I-020: 確定の判定は期限の比較より先に行う。"""
    clock = _Clock()
    seen = iter([[Watched("a", None)], [Watched("a", "ok")]])
    poll = plan_cli.Poll(monotonic=clock.monotonic, sleep=lambda s: clock.sleep(5.0), interval=1.0)
    out = plan_cli._poll(lambda: next(seen), 2.0, AGAIN, poll)
    assert out == attention("plan", [Watched("a", "ok")], AGAIN)
