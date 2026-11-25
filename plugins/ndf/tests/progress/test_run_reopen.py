"""数えない MVV 判定の回答で済んだ承認ゲートの開き直し（`ndf.progress.run._reopen`。#3959）の今の振る舞い。

開き直すのは、回答が数えない `service:mvv` の回答で済んだ承認ゲートのステップのうち、工程の中でそれより後ろに済んだステップが
1 つも無いものだけ。後ろに済んだステップがあるかは、開き直す前の済みの集まりで見る（同じ呼び出しで消した承認ゲートは数えたまま）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from ndf.progress import definition, invoke, model, run, stages

STAGE = "デプロイ"
REV = 1
VOID = {"by": model.MVV_SERVICE}  # 判定の写しを持たない service:mvv の回答（数えない）
HUMAN = {"by": "human:reviewer"}


def _step(sid: str, *, listen: bool, gate: Optional[str] = "release") -> stages.StepDef:
    call = definition.LISTEN_CALL if listen else "ndf.plan"
    return stages.StepDef(sid, STAGE, call, "k", "r", gate=gate if listen else None)


def _stage(*steps: stages.StepDef) -> stages.StageDef:
    return stages.StageDef("deploy", STAGE, "p", tuple(steps))


def _answer(seq: int, by: dict[str, Any], *, row: str = STAGE, rev: int = REV) -> model.Event:
    payload = {"answer": run.APPROVED, "kind": invoke.LISTEN, **by}
    return model.Event(seq, datetime.now(timezone.utc), model.EventDraft(run.ANSWERED, run.CONDUCTOR, row, payload=payload, rev=rev))


def _view(*events: model.Event) -> run.View:
    return run.View("m", REV, None, {}, list(events))  # type: ignore[arg-type]  # _reopen は snap を読まない


def _gate_done(seq: Any) -> dict[str, Any]:
    return {"status": "ok", "metrics": {"answer": run.APPROVED, "seq": seq}}


def _reopened(v: run.View, s: stages.StageDef, done: dict[str, dict[str, Any]]) -> list[str]:
    prog = run.StepProgress(done=dict(done))
    run._reopen(v, s, prog)
    return [sid for sid in done if sid not in prog.done]


def test_the_last_done_gate_with_a_void_answer_is_reopened():
    s = _stage(_step("mvv", listen=False), _step("gate", listen=True), _step("release", listen=False))
    done = {"mvv": {"status": "ok"}, "gate": _gate_done(5)}
    assert _reopened(_view(_answer(5, VOID)), s, done) == ["gate"]


def test_a_gate_with_a_done_step_right_after_is_kept():
    s = _stage(_step("gate", listen=True), _step("release", listen=False))
    done = {"gate": _gate_done(5), "release": {"status": "ok"}}
    assert _reopened(_view(_answer(5, VOID)), s, done) == []


def test_a_gate_with_a_done_step_further_after_a_gap_is_kept():
    s = _stage(_step("gate", listen=True), _step("middle", listen=False), _step("release", listen=False))
    done = {"gate": _gate_done(5), "release": {"status": "ok"}}
    assert _reopened(_view(_answer(5, VOID)), s, done) == []


def test_a_gate_followed_only_by_undone_steps_is_reopened():
    s = _stage(_step("gate", listen=True), _step("middle", listen=False), _step("release", listen=False))
    assert _reopened(_view(_answer(5, VOID)), s, {"gate": _gate_done(5)}) == ["gate"]


def test_of_two_void_gates_only_the_last_is_reopened():
    # 前の承認ゲートの後ろの済みは、同じ呼び出しで消す前の後ろの承認ゲートで数える
    s = _stage(_step("g1", listen=True), _step("g2", listen=True))
    done = {"g1": _gate_done(5), "g2": _gate_done(7)}
    assert _reopened(_view(_answer(5, VOID), _answer(7, VOID)), s, done) == ["g2"]


def test_a_void_gate_before_a_counted_last_gate_is_kept():
    s = _stage(_step("g1", listen=True), _step("g2", listen=True))
    done = {"g1": _gate_done(5), "g2": _gate_done(7)}
    assert _reopened(_view(_answer(5, VOID), _answer(7, HUMAN)), s, done) == []


def test_a_gate_answered_by_a_human_is_kept():
    s = _stage(_step("gate", listen=True))
    assert _reopened(_view(_answer(5, HUMAN)), s, {"gate": _gate_done(5)}) == []


def test_a_non_gate_step_is_never_reopened():
    s = _stage(_step("plan", listen=False))
    assert _reopened(_view(_answer(5, VOID)), s, {"plan": _gate_done(5)}) == []


def test_an_undone_gate_stays_undone_and_others_are_untouched():
    s = _stage(_step("mvv", listen=False), _step("gate", listen=True))
    assert _reopened(_view(_answer(5, VOID)), s, {"mvv": {"status": "ok"}}) == []


def test_a_gate_whose_seq_is_missing_or_points_nowhere_is_kept():
    s = _stage(_step("gate", listen=True))
    v = _view(_answer(5, VOID))
    for report in ({"status": "ok"}, {"status": "ok", "metrics": "x"}, _gate_done(None), _gate_done(99)):
        assert _reopened(v, s, {"gate": report}) == []


def test_only_answers_of_the_same_row_and_revision_count():
    s = _stage(_step("gate", listen=True))
    for other in (_answer(5, VOID, row="設計"), _answer(5, VOID, rev=REV + 1)):
        assert _reopened(_view(other), s, {"gate": _gate_done(5)}) == []


def test_the_gate_kind_of_the_step_decides_whether_the_answer_counts():
    # 保留を確かめた判定の写しは設計承認ゲートでは数え、リリース承認ゲートでは数えない
    checked = {"by": model.MVV_SERVICE, model.MVV_KEY: {"holds": []}}
    for gate, reopened in (("design", []), ("release", ["gate"])):
        s = _stage(_step("gate", listen=True, gate=gate))
        assert _reopened(_view(_answer(5, checked)), s, {"gate": _gate_done(5)}) == reopened
