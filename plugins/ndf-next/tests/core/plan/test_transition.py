"""遷移の判断（transition_decide）。DBOS を起こさず、偽の手の実行で判断のループを回す（RF4）。"""

from __future__ import annotations

from ndf.core import loop
from ndf.core.plan import model, transition_decide
from ndf.core.plan.transition_decide import ANSWERED, SLEPT, AwaitAnswer, Outcome, RunStep, Sleep


def _plan(*steps: dict, limit: int | None = None) -> model.Plan:
    raw: dict = {"name": "p", "steps": [{"run": ["true"], **s} for s in steps]}
    if limit is not None:
        raw["limit"] = limit
    return model.parse(raw)


def _drive(plan: model.Plan, outcomes: dict, start=None, prelude=()):
    """ステップの id ごとの結果の並び `outcomes` で回し、打った手と終わりの状態を返す。"""
    acts: list = []
    queues = {k: list(v) for k, v in outcomes.items()}

    def perform(act):
        acts.append(act)
        if isinstance(act, Sleep):
            return Outcome(SLEPT)
        if isinstance(act, AwaitAnswer):
            return Outcome(ANSWERED, answer=queues[f"answer:{act.step}"].pop(0))
        q = queues.get(act.step)
        return q.pop(0) if q else Outcome("ok")

    state = transition_decide.start_state(plan, start, prelude)
    final = loop.run(lambda s, d: transition_decide.decide(plan, s, d), perform, state)
    return acts, final


def _ran(acts) -> list[str]:
    return [a.step for a in acts if isinstance(a, RunStep)]


def test_runs_the_steps_in_order_and_follows_next():
    plan = _plan({"id": "a", "next": "c"}, {"id": "b"}, {"id": "c"})
    acts, final = _drive(plan, {})
    assert _ran(acts) == ["a", "c"]
    assert final.status == "ok"


def test_on_cause_picks_the_step_for_the_kind():
    plan = _plan({"id": "a", "on_cause": {"test_failed": "fix"}}, {"id": "b"}, {"id": "fix", "next": "end"})
    acts, final = _drive(plan, {"a": [Outcome("stopped", "test_failed")]})
    assert _ran(acts) == ["a", "fix"]
    assert final.status == "ok"


def test_retry_waits_then_reruns_the_same_step_without_counting_it():
    plan = _plan({"id": "a", "retry": {"ci_pending": {"attempts": 2, "interval": 5}}}, limit=1)
    acts, final = _drive(plan, {"a": [Outcome("stopped", "ci_pending"), Outcome("ok")]})
    assert acts == [RunStep("a", 0), Sleep("a", 5.0), RunStep("a", 1)]
    assert final.status == "ok"
    assert final.count == 1


def test_after_the_retry_limit_the_step_goes_to_on_fail():
    plan = _plan({"id": "a", "on_fail": "fix", "retry": {"ci_pending": {"attempts": 1, "interval": 0}}}, {"id": "fix", "next": "end"})
    stopped = Outcome("stopped", "ci_pending")
    acts, _ = _drive(plan, {"a": [stopped, stopped]})
    assert _ran(acts) == ["a", "a", "fix"]


def test_a_human_kind_does_not_go_to_on_fail():
    plan = _plan({"id": "a", "on_fail": "fix"}, {"id": "fix"})
    acts, final = _drive(plan, {"a": [Outcome("stopped", None)]})
    assert _ran(acts) == ["a"]
    assert (final.status, final.kind) == ("stopped", "unknown")


def test_not_applicable_to_end_finishes_ok():
    plan = _plan({"id": "a", "on_cause": {"not_applicable": "end"}}, {"id": "b"})
    acts, final = _drive(plan, {"a": [Outcome("stopped", "not_applicable")]})
    assert _ran(acts) == ["a"]
    assert final.status == "ok"


def test_resume_runs_the_prelude_before_the_start():
    """--from が base-sync より後でも、間に入った変更を取り込んでから流す（#3114）。"""
    plan = _plan({"id": "base-sync"}, {"id": "impl"}, {"id": "pr"})
    acts, final = _drive(plan, {}, start="pr", prelude=("base-sync",))
    assert _ran(acts) == ["base-sync", "pr"]
    assert final.status == "ok"


def test_a_stopped_prelude_step_ends_with_its_cause():
    plan = _plan({"id": "base-sync", "on_fail": "pr"}, {"id": "pr"})
    acts, final = _drive(plan, {"base-sync": [Outcome("stopped", "base_conflict")]}, start="pr", prelude=("base-sync",))
    assert _ran(acts) == ["base-sync"]
    assert (final.status, final.kind) == ("stopped", "base_conflict")


def test_starting_at_end_finishes_without_running_a_step():
    """リリースの依頼の答えの後に --from <プラン>=end で続けても止まらない（#3086）。"""
    acts, final = _drive(_plan({"id": "a"}), {}, start="end")
    assert acts == []
    assert final.status == "ok"


def test_the_step_limit_stops_the_plan():
    plan = _plan({"id": "a", "next": "a"}, limit=3)
    acts, final = _drive(plan, {})
    assert len(_ran(acts)) == 3
    assert (final.status, final.kind) == ("stopped", "fix_limit")


def test_a_gate_without_answers_ends_the_plan_at_the_gate():
    plan = _plan({"id": "ask"}, {"id": "release"})
    acts, final = _drive(plan, {"ask": [Outcome("gate")]})
    assert _ran(acts) == ["ask"]
    assert (final.status, final.last) == ("gate", "ask")


def test_a_gate_with_answers_waits_and_follows_the_answer():
    plan = _plan({"id": "ask", "on_answer": {"approved": "release", "rejected": "stop"}}, {"id": "other"}, {"id": "release"})
    acts, final = _drive(plan, {"ask": [Outcome("gate")], "answer:ask": [None, "approved"]})
    assert [type(a).__name__ for a in acts] == ["RunStep", "AwaitAnswer", "AwaitAnswer", "RunStep"]
    assert _ran(acts) == ["ask", "release"]
    assert final.status == "ok"


def test_an_unknown_answer_stops():
    plan = _plan({"id": "ask", "on_answer": {"approved": "end"}})
    _, final = _drive(plan, {"ask": [Outcome("gate")], "answer:ask": ["maybe"]})
    assert (final.status, final.kind) == ("stopped", "invalid")


def test_decide_returns_the_same_next_for_the_same_input():
    """回復のときに判断の関数をもう一度呼んでも、同じ手が返る。"""
    plan = _plan({"id": "a", "retry": {"ci_pending": {"attempts": 2, "interval": 1}}})
    state = transition_decide.start_state(plan, None)
    first = transition_decide.decide(plan, state, None)
    done = Outcome("stopped", "ci_pending")
    assert transition_decide.decide(plan, first.state, done) == transition_decide.decide(plan, first.state, done)
