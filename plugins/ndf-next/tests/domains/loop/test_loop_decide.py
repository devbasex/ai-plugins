"""開発ループの判断の関数（周の結果・選定・持ち主の鍵・止める要求・ゲートの答え）。"""

from __future__ import annotations

from typing import Any

import pytest

from ndf.domains.loop import control_decide as ctl
from ndf.domains.loop import lap_decide
from ndf.domains.loop import record_decide as rec
from ndf.domains.loop import select_decide as sel

TTL = 100.0
MODES = ("light", "standard")


def _ctx(session: str | None = "s1", now: float = 10.0) -> ctl.Ctx:
    return ctl.Ctx("demo", session, now)


def _apply(state: rec.LoopState, d: ctl.Decision) -> rec.LoopState:
    assert not d.refused, d.summary
    for ev in d.events:
        state = rec.apply(state, ev)
    return state


def _running(issues: tuple[int, ...] = (1, 2), mode: bool = True) -> rec.LoopState:
    state = _apply(rec.LoopState(), ctl.start(rec.LoopState(), _ctx(), TTL))
    state = _apply(state, ctl.open_lap(state, _ctx(), issues))
    if mode:
        state = _apply(state, ctl.decide_mode(state, _ctx(), "standard", "設計が要る", MODES))
    return state


def _argv(step: ctl.Step | None) -> tuple[str, ...]:
    assert step is not None
    return step.args[:2]


@pytest.mark.parametrize(
    ("facts", "outcome"),
    [
        (lap_decide.LapFacts(failed=True, released=("1.2.0",)), rec.DONE),
        (lap_decide.LapFacts(failure_key="merge-gate", returned=True), rec.RETURNED),
        (lap_decide.LapFacts(failed=True, held=True), rec.HELD),
        (lap_decide.LapFacts(failure_key="ci"), rec.FAILED),
        (lap_decide.LapFacts(), rec.DONE),
    ],
)
def test_lap_outcome_prefers_release_facts(facts: lap_decide.LapFacts, outcome: str) -> None:
    """リリースした周は、手順の失敗があっても完了で、理由に失敗を添える（RF7 の I11）。"""
    got, why = lap_decide.lap_outcome(facts)
    assert got == outcome
    if facts.released and facts.failed:
        assert "失敗の印" in why


def test_failure_streak_skips_held_laps() -> None:
    laps = (
        rec.Lap(1, (1,), outcome=rec.FAILED),
        rec.Lap(2, (2,), outcome=rec.HELD),
        rec.Lap(3, (3,), outcome=rec.FAILED),
    )
    assert lap_decide.failure_streak(rec.LoopState(laps=laps)) == 2
    assert lap_decide.failure_streak(rec.LoopState(laps=(*laps, rec.Lap(4, (4,), outcome=rec.DONE)))) == 0


def test_issue_2722_first_issue_survives_backlog_wait() -> None:
    """直して出し直すと答えた課題は、棚卸しの判断待ちの印があっても選定に残る。前のループの判断待ちは外す。"""
    rows = [sel.Row(5, waiting=True), sel.Row(6, prior=True), sel.Row(7, waiting=True)]
    facts = sel.ExclusionFacts(first=(5, 6), size=3)
    got = sel.select(rows, facts)
    assert got.chosen == (5,)
    assert dict(got.skipped) == {6: sel.PRIOR, 7: sel.WAITING}


def test_issue_2746_skipped_rows_do_not_use_capacity() -> None:
    """外す理由のある行は枠を使わず、後ろの選べる行が枠に入る。"""
    rows = [sel.Row(1, blocked="未回答"), sel.Row(2, needs=(9,)), sel.Row(3), sel.Row(4), sel.Row(5)]
    got = sel.select(rows, sel.ExclusionFacts(size=2))
    assert got.chosen == (3, 4)
    assert dict(got.skipped) == {1: sel.BLOCKED, 2: sel.NEEDS, 5: sel.CAPACITY}


def test_issue_2788_parent_issue_is_not_selected() -> None:
    got = sel.select([sel.Row(10, parent=True), sel.Row(11)], sel.ExclusionFacts(size=2))
    assert got.chosen == (11,)
    assert got.skipped == ((10, sel.PARENT),)


def test_issue_2788_exclude_mid_lap_keeps_the_rest() -> None:
    """実装で止まった課題を周の途中で外し、残りの課題で周を続ける。"""
    state = _running((1, 2))
    d = ctl.exclude(state, _ctx(), 1, "実装で止まった")
    after = _apply(state, d)
    assert after.current is not None and after.current.remaining == (2,)
    assert d.metrics["remaining"] == [2]
    assert ctl.exclude(after, _ctx(), 1, "もう一度").kind == ctl.PRECONDITION


def test_issue_2923_mode_needs_basis_and_is_redecided_after_exclusion() -> None:
    state = _running((1, 2), mode=False)
    assert ctl.decide_mode(state, _ctx(), "standard", "  ", MODES).kind == ctl.INVALID
    assert ctl.decide_mode(state, _ctx(), "heavy", "根拠", MODES).kind == ctl.INVALID
    state = _apply(state, ctl.decide_mode(state, _ctx(), "light", "文書だけ", MODES))
    assert state.current is not None and (state.current.mode, state.current.basis) == ("light", "文書だけ")
    d = ctl.exclude(state, _ctx(), 2, "前提が無い")
    after = _apply(state, d)
    assert after.current is not None and after.current.mode is None
    assert d.next is not None and d.next.args[1] == "sprint" and d.next.ask == ("mode", "basis")


def test_issue_2132_created_issues_are_recorded_and_left_out_next_lap() -> None:
    state = _apply(_running((1,)), ctl.created(_running((1,)), _ctx(), [30, 31]))
    assert state.current is not None and state.current.created == (30, 31)
    facts = sel.facts_of(state, (), 3)
    assert sel.exclusion(sel.Row(30), facts) == sel.CREATED


def test_issue_2820_stop_request_keeps_the_lease_until_the_boundary() -> None:
    """止める要求は鍵を返さず、周の途中でほかのセッションは入れない。鍵は周の境で返す。"""
    state = _apply(_running((1,)), ctl.stop(_running((1,)), _ctx(None), "human", "今日は終わり", False))
    assert state.status == rec.STOPPING and state.holder == "s1"
    other = ctl.start(state, _ctx("s2"), TTL)
    assert other.kind == ctl.REFUSED and _argv(other.next) == ("loop", "get")
    closed = ctl.close_lap(state, _ctx(), lap_decide.LapFacts(), TTL)
    after = _apply(state, closed)
    assert after.status == rec.HALTED and after.holder is None
    assert after.stopped is not None and after.stopped.reason == rec.USER_STOP
    assert closed.next is None


def test_issue_2435_stop_now_records_the_stopped_state() -> None:
    """途中で止めると、締めていない周を保留で締め、ループを user_stop で止めた状態にする。"""
    state = _running((1, 2))
    after = _apply(state, ctl.stop(state, _ctx(None), "organizer", "", True))
    assert after.status == rec.HALTED
    assert after.stopped is not None and after.stopped.reason == rec.USER_STOP
    assert after.laps[-1].outcome == rec.HELD
    assert ctl.open_lap(after, _ctx(), (3,)).kind == ctl.REFUSED
    assert sel.facts_of(after, (), 1).pending == frozenset({1, 2})


def test_lease_expires_for_another_session() -> None:
    state = _running((1,))
    assert ctl.start(state, _ctx("s2", now=10.0 + TTL - 1), TTL).kind == ctl.REFUSED
    taken = ctl.start(state, _ctx("s2", now=10.0 + TTL + 1), TTL)
    assert not taken.refused
    assert ctl.close_lap(_apply(state, taken), _ctx("s1", now=10.0 + TTL + 2), lap_decide.LapFacts(), TTL).kind == ctl.REFUSED


def test_close_lap_extends_the_lease_and_records_versions() -> None:
    state = _running((1,))
    d = ctl.close_lap(state, _ctx(now=50.0), lap_decide.LapFacts(failure_key="retro", released=("2.0.0",)), TTL)
    after = _apply(state, d)
    assert after.laps[-1].released == ("2.0.0",) and after.laps[-1].outcome == rec.DONE
    assert after.lease_until == 50.0 + TTL
    assert _argv(d.next) == ("loop", "select")


def _gated(head: str = "a" * 40) -> rec.LoopState:
    state = _running((1,))
    return _apply(state, ctl.gate(state, _ctx(), 77, head))


def test_issue_2714_moved_head_refuses_the_answer() -> None:
    """答えの先端がゲートを出した先端と違えば、答えを書かずに資料の出し直しへ向ける。"""
    state = _gated()
    d = ctl.answer(state, _ctx(), 77, "approved", "b" * 40, "")
    assert d.kind == ctl.PRECONDITION and not d.events
    assert d.next is not None and d.next.args[:2] == ("loop", "gate")
    ok = ctl.answer(state, _ctx(), 77, "approved", "a" * 40, "")
    assert ok.metrics["head"] == "a" * 40


def test_issue_2400_revise_answer_is_kept_as_a_rejection() -> None:
    state = _gated()
    state = _apply(state, ctl.answer(state, _ctx(), 77, "revise", "a" * 40, "受け入れ条件が足りない"))
    state = _apply(state, ctl.answer(state, _ctx(), 77, "approved", "a" * 40, ""))
    got = state.rejections()
    assert [(r["pr"], r["answer"], r["note"]) for r in got] == [(77, "revise", "受け入れ条件が足りない")]


@pytest.mark.parametrize(
    ("make", "kind", "verb"),
    [
        (lambda s: ctl.open_lap(s, _ctx("s9"), (1,)), ctl.PRECONDITION, "start"),
        (lambda s: ctl.open_lap(s, _ctx(), (1,)), ctl.REFUSED, "lap"),
        (lambda s: ctl.decide_mode(s, _ctx(), "nope", "根拠", MODES), ctl.INVALID, "lap"),
    ],
)
def test_refusals_carry_kind_and_next(make: Any, kind: str, verb: str) -> None:
    """断りは止まった理由の種類と、打てる次の 1 手を持つ（SF11）。"""
    state = _running((1,))
    if verb == "start":
        state = rec.LoopState()
    d = make(state)
    assert d.kind == kind
    assert d.next is not None and d.next.args[1] == verb


def test_record_rejects_unknown_events() -> None:
    with pytest.raises(rec.RecordError):
        rec.fold([{"kind": "nope", "at": 1}])
    with pytest.raises(rec.RecordError):
        rec.fold([{"kind": rec.LAP_OPENED, "at": 1}])
    with pytest.raises(rec.RecordError):
        rec.event(rec.LAP_CLOSED, 1, lap=1, outcome="途中", reason="")


def test_candidates_reject_bad_rows() -> None:
    with pytest.raises(sel.CandidateError):
        sel.rows_of([{"issue": 1}, {"issue": 1}])
    with pytest.raises(sel.CandidateError):
        sel.rows_of([{"issue": 1, "waiting": "yes"}])
    assert sel.rows_of([{"issue": 2, "needs": [3]}]) == [sel.Row(2, needs=(3,))]
