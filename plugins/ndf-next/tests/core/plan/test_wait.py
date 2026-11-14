"""待ちの結果（wait_decide。SF7）: 人の手が要るものを種類つきで返し、next に続きの待ちを載せる。"""

from __future__ import annotations

from ndf.contracts import causes, result
from ndf.core.plan.wait_decide import Watched, attention, settled, timed_out

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
    """結果の routes の鍵は答えの語彙だけで、承認ゲートが 2 本あれば先頭の分にし、各行にそれぞれの分を残す。"""
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
