"""スプリントの閉じの判断（RF5b）。"""

from __future__ import annotations

import pytest

from ndf.domains.loop import sprint_decide as sd

PACES = ("normal", "fast", "auto")


def test_issue_2968_record_pr_zero_means_no_record() -> None:
    """--record-pr 0 を偽として読み捨てず、リリース記録なしとして組む（印 {queue_record} を残さない）。"""
    plan = sd.close_plan(sd.CloseFacts("fast", (sd.MERGED_BY_CHECK,), mvv=True, record_pr=0), PACES)
    assert plan.record_pr == sd.RECORD_NONE
    assert sd.VERIFY not in plan.steps
    assert sd.record_pr_of(None, sd.RECORD_FOUND) == sd.RECORD_FOUND
    assert sd.record_pr_of(12, sd.RECORD_FOUND) == "12"


def test_issue_2967_normal_pace_does_not_need_verify() -> None:
    """進め方 normal では導入の確認（pace.json の fast.verify）を要求しない。"""
    assert sd.close_plan(sd.CloseFacts("normal", (sd.MERGED_BY_CHECK,), mvv=True), PACES).verify is False
    assert sd.close_plan(sd.CloseFacts("fast", (sd.MERGED_BY_CHECK,), mvv=True), PACES).verify is True
    assert sd.close_plan(sd.CloseFacts("auto", (sd.PROMOTE,), mvv=False), PACES).verify is False
    assert sd.closing_pace("normal") == "normal"


def test_close_steps_follow_the_order() -> None:
    plan = sd.close_plan(sd.CloseFacts("auto", (sd.PROMOTE, sd.MANUAL), record_pr=None, retrospective_due=True), PACES)
    assert plan.record_pr == sd.RECORD_FOUND
    assert plan.steps == (sd.SPEC, sd.PR, sd.VERIFY, sd.DEEP_DUE, sd.RETROSPECTIVE, sd.BACKLOG, sd.MERGE, sd.CLOSE_ISSUES)
    none = sd.close_plan(sd.CloseFacts("normal", (sd.NONE,)), PACES)
    assert none.record_pr == sd.RECORD_NONE and sd.RETROSPECTIVE not in none.steps


@pytest.mark.parametrize("facts", [sd.CloseFacts("slow", (sd.NONE,)), sd.CloseFacts("fast", ()), sd.CloseFacts("fast", ("ship",))])
def test_close_plan_rejects_unknown_values(facts: sd.CloseFacts) -> None:
    with pytest.raises(sd.SprintError):
        sd.close_plan(facts, PACES)
