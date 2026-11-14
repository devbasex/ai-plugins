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
    return rec.apply_all(state, d.events)


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
    running = _running((1,))
    state = _apply(running, ctl.created(running, _ctx(), [30, 31]))
    assert state.current is not None and state.current.created == (30, 31)
    facts = sel.facts_of(state, (), 3)
    assert sel.exclusion(sel.Row(30), facts) == sel.CREATED


def test_issue_2820_stop_request_keeps_the_lease_until_the_boundary() -> None:
    """止める要求は鍵を返さず、周の途中でほかのセッションは入れない。鍵は周の境で返す。"""
    running = _running((1,))
    state = _apply(running, ctl.stop(running, _ctx(None), "human", "今日は終わり", False))
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
    ("given", "make", "kind", "verb"),
    [
        (lambda: rec.LoopState(), lambda s: ctl.open_lap(s, _ctx("s9"), (1,)), ctl.PRECONDITION, "start"),
        (lambda: _running((1,)), lambda s: ctl.open_lap(s, _ctx(), (1,)), ctl.REFUSED, "lap"),
        (lambda: _running((1,)), lambda s: ctl.decide_mode(s, _ctx(), "nope", "根拠", MODES), ctl.INVALID, "lap"),
    ],
)
def test_refusals_carry_kind_and_next(given: Any, make: Any, kind: str, verb: str) -> None:
    """断りは止まった理由の種類と、打てる次の 1 手を持つ（SF11）。"""
    d = make(given())
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


# --- 現状固定: 周に付く事象の当て方（record_decide._with_lap / _lap / _apply_lap） ---


def _two_laps() -> rec.LoopState:
    """周 1 を締め、周 2 を開いた状態（事象を直に畳む）。"""
    return rec.fold(
        [
            rec.event(rec.LAP_OPENED, 1, lap=1, issues=[1]),
            rec.event(rec.LAP_CLOSED, 2, lap=1, outcome=rec.DONE, reason="r1"),
            rec.event(rec.LAP_OPENED, 3, lap=2, issues=[2, 3]),
        ]
    )


@pytest.mark.parametrize(
    ("ev", "want"),
    [
        (rec.event(rec.MODE_DECIDED, 5, lap=2, mode="light", basis="b"), {"mode": "light", "basis": "b"}),
        (rec.event(rec.ISSUE_EXCLUDED, 5, lap=2, issue="3", reason="止まった"), {"excluded": ((3, "止まった"),), "mode": None}),
        # 重複は記録済みの番号とだけ比べ、同じ事象の中の重複は残す（今の振る舞い）
        (rec.event(rec.ISSUES_CREATED, 5, lap=2, issues=["9", 9, 8]), {"created": (9, 9, 8)}),
        (rec.event(rec.GATE_SHOWN, 5, lap=2, pr="77", head="h1"), {"gates": ((77, "h1"),)}),
        (rec.event(rec.VERSION_SHIPPED, 5, lap=2, version=1), {"released": ("1",)}),
        (rec.event(rec.LAP_CLOSED, 5, lap=2, outcome=rec.FAILED, reason="ci"), {"outcome": rec.FAILED, "reason": "ci"}),
    ],
)
def test_lap_events_change_only_the_named_lap(ev: dict[str, Any], want: dict[str, Any]) -> None:
    """周に付く事象は番号の周だけを変え、ほかの周と状態の鍵はそのまま残す。"""
    before = _two_laps()
    after = rec.apply(before, ev)
    assert after.laps[0] == before.laps[0]
    for key, value in want.items():
        assert getattr(after.laps[1], key) == value
    assert (after.holder, after.lease_until, after.requested, after.stopped) == (None, 0.0, None, None)


def test_answer_event_is_appended_with_typed_fields() -> None:
    before = _two_laps()
    after = rec.apply(before, rec.event(rec.GATE_ANSWERED, "6", lap="2", pr="77", answer="revise", head=None, note="n"))
    assert after.laps[1].answers == (rec.Answer(77, "revise", None, "n", 6.0),)
    assert after.laps[0].answers == ()


def test_lap_events_accumulate_in_order() -> None:
    state = _two_laps()
    for ev in (
        rec.event(rec.MODE_DECIDED, 5, lap=2, mode="standard", basis="b"),
        rec.event(rec.ISSUES_CREATED, 5, lap=2, issues=[10]),
        rec.event(rec.ISSUES_CREATED, 6, lap=2, issues=[10, 11]),
        rec.event(rec.GATE_SHOWN, 7, lap=2, pr=77, head="h1"),
        rec.event(rec.GATE_SHOWN, 8, lap=2, pr=77, head="h2"),
        rec.event(rec.VERSION_SHIPPED, 9, lap=2, version="1.0.0"),
        rec.event(rec.VERSION_SHIPPED, 9, lap=2, version="1.0.0"),
        rec.event(rec.ISSUE_EXCLUDED, 10, lap=2, issue=2, reason="x"),
    ):
        state = rec.apply(state, ev)
    lap = state.laps[1]
    assert lap.created == (10, 11)
    assert lap.gates == ((77, "h1"), (77, "h2")) and lap.gate_head(77) == "h2"
    assert lap.released == ("1.0.0", "1.0.0")
    assert lap.excluded == ((2, "x"),) and lap.remaining == (3,)
    assert (lap.mode, lap.basis) == (None, None)


def test_closed_lap_still_accepts_lap_events() -> None:
    """締めた周への事象も断らずに当てる（今の振る舞い）。"""
    after = rec.apply(_two_laps(), rec.event(rec.VERSION_SHIPPED, 5, lap=1, version="0.9"))
    assert after.laps[0].released == ("0.9",) and after.laps[0].outcome == rec.DONE


@pytest.mark.parametrize(
    "ev",
    [
        rec.event(rec.MODE_DECIDED, 5, lap=3, mode="light", basis="b"),
        rec.event(rec.ISSUE_EXCLUDED, 5, lap=3, issue=1, reason="r"),
        rec.event(rec.ISSUES_CREATED, 5, lap=3, issues=[1]),
        rec.event(rec.GATE_SHOWN, 5, lap=3, pr=1, head="h"),
        rec.event(rec.GATE_ANSWERED, 5, lap=3, pr=1, answer="approved", head="h", note=""),
        rec.event(rec.VERSION_SHIPPED, 5, lap=3, version="1"),
        rec.event(rec.LAP_CLOSED, 5, lap=3, outcome=rec.DONE, reason=""),
    ],
)
def test_lap_events_for_unknown_lap_raise(ev: dict[str, Any]) -> None:
    with pytest.raises(rec.RecordError, match="記録に無い周 3 への事象"):
        rec.apply(_two_laps(), ev)
    with pytest.raises(rec.RecordError, match="記録に無い周 1 への事象"):
        rec.apply(rec.LoopState(), {**ev, "lap": 1})


def test_duplicate_lap_numbers_change_the_first_match() -> None:
    """同じ番号の周が 2 つあるときは、先に開いた方を変える（今の振る舞い）。"""
    state = rec.fold([rec.event(rec.LAP_OPENED, 1, lap=1, issues=[1]), rec.event(rec.LAP_OPENED, 2, lap=1, issues=[2])])
    after = rec.apply(state, rec.event(rec.ISSUES_CREATED, 3, lap=1, issues=[5]))
    assert after.laps[0].created == (5,) and after.laps[1].created == ()


# --- 現状固定: 周を締める（control_decide.close_lap） ---


def _kinds(d: ctl.Decision) -> list[str]:
    return [ev["kind"] for ev in d.events]


def test_close_lap_events_order_and_fields() -> None:
    """出した版（記録に無いものだけ・重複なし）→ 締め → 鍵の延長の順に、今の時刻で事象を並べる。"""
    state = _running((1,))
    state = _apply(state, ctl.released(state, _ctx(), "1.0"))
    d = ctl.close_lap(state, _ctx(now=40.0), lap_decide.LapFacts(released=("1.0", "2.0", "2.0", "3.0")), TTL)
    assert not d.refused
    assert _kinds(d) == [rec.VERSION_SHIPPED, rec.VERSION_SHIPPED, rec.LAP_CLOSED, rec.LEASE_TAKEN]
    assert [ev.get("version") for ev in d.events[:2]] == ["2.0", "3.0"]
    assert all(ev["at"] == 40.0 for ev in d.events)
    assert all(ev["lap"] == 1 for ev in d.events[:3])
    closed = d.events[2]
    assert closed["outcome"] == rec.DONE and closed["reason"] == "版をリリースした（1.0・2.0・3.0）"
    assert d.events[3]["session"] == "s1" and d.events[3]["until"] == 40.0 + TTL
    assert d.summary == "周 1 を完了で締めた: 版をリリースした（1.0・2.0・3.0）"
    assert d.metrics == {"lap": 1, "outcome": rec.DONE, "streak": 0}
    assert _argv(d.next) == ("loop", "select") and d.next is not None and d.next.session
    after = _apply(state, d)
    assert after.laps[-1].released == ("1.0", "2.0", "3.0")


def test_close_lap_uses_recorded_versions_when_facts_have_none() -> None:
    state = _running((1,))
    state = _apply(state, ctl.released(state, _ctx(), "1.0"))
    d = ctl.close_lap(state, _ctx(), lap_decide.LapFacts(failure_key="ci"), TTL)
    assert _kinds(d) == [rec.LAP_CLOSED, rec.LEASE_TAKEN]
    assert d.metrics["outcome"] == rec.DONE
    assert d.events[0]["reason"] == "版をリリースした（1.0）。手順の失敗 ci は結果を変えない"


def test_close_lap_failure_counts_the_streak() -> None:
    state = _running((1,))
    d1 = ctl.close_lap(state, _ctx(), lap_decide.LapFacts(failure_key="ci"), TTL)
    assert d1.metrics == {"lap": 1, "outcome": rec.FAILED, "streak": 1}
    assert d1.summary == "周 1 を失敗で締めた: ci"
    state = _apply(state, d1)
    state = _apply(state, ctl.open_lap(state, _ctx(), (2,)))
    d2 = ctl.close_lap(state, _ctx(), lap_decide.LapFacts(failed=True), TTL)
    assert d2.metrics == {"lap": 2, "outcome": rec.FAILED, "streak": 2}
    assert d2.events[0]["reason"] == "失敗の印"


def test_close_lap_with_stop_request_closes_then_halts() -> None:
    """止める要求があれば、締めた後に鍵を延ばさず、止めて鍵を返す。"""
    state = _running((1,))
    state = _apply(state, ctl.stop(state, _ctx(None), "human", "終わり", False))
    d = ctl.close_lap(state, _ctx(now=30.0), lap_decide.LapFacts(released=("1.0",)), TTL)
    assert _kinds(d) == [rec.VERSION_SHIPPED, rec.LAP_CLOSED, rec.LOOP_HALTED, rec.LEASE_RETURNED]
    assert d.events[2]["by"] == "human" and d.events[2]["reason"] == rec.USER_STOP
    assert d.events[3]["session"] == "s1"
    assert d.next is None
    assert d.metrics == {"lap": 1, "outcome": rec.DONE, "streak": 0}


def test_close_lap_without_open_lap() -> None:
    """締めていない周が無ければ、止める要求があれば止め、無ければ前提の断りで選定へ向ける。"""
    state = _apply(_running((1,)), ctl.close_lap(_running((1,)), _ctx(), lap_decide.LapFacts(), TTL))
    d = ctl.close_lap(state, _ctx(), lap_decide.LapFacts(), TTL)
    assert d.kind == ctl.PRECONDITION and d.summary == "締めていない周が無い" and not d.events
    assert _argv(d.next) == ("loop", "select")
    stopping = _apply(state, ctl.stop(state, _ctx(None), "organizer", "", False))
    s = ctl.close_lap(stopping, _ctx(), lap_decide.LapFacts(released=("9.9",)), TTL)
    assert not s.refused
    assert _kinds(s) == [rec.LOOP_HALTED, rec.LEASE_RETURNED]
    assert s.summary == "止める要求があり、ループ demo を止めた"
    assert s.next is None and s.metrics == {}


def test_close_lap_refusals() -> None:
    """鍵を持たないセッションと、止めたループの締めは断る。"""
    state = _running((1,))
    no_key = ctl.close_lap(state, _ctx("s2"), lap_decide.LapFacts(), TTL)
    assert no_key.kind == ctl.REFUSED and not no_key.events and _argv(no_key.next) == ("loop", "get")
    fresh = ctl.close_lap(rec.LoopState(), _ctx(), lap_decide.LapFacts(), TTL)
    assert fresh.kind == ctl.PRECONDITION and _argv(fresh.next) == ("loop", "start")
    halted = _apply(state, ctl.stop(state, _ctx(None), "human", "", True))
    h = ctl.close_lap(halted, _ctx(), lap_decide.LapFacts(), TTL)
    assert h.kind == ctl.REFUSED and h.next is None and not h.events
