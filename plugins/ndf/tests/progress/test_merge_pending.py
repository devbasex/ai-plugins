"""マージがその方式で断られて止まったかの判定（`merge_pending.refused`）の現状固定。"""

from __future__ import annotations

import pytest

from ndf.contracts import result
from ndf.progress import merge_pending

AT_MERGE = {"kind": "plan", "step": "merge"}


def _report(**changes) -> dict:
    """マージのステップで断られて止まったプランの結果。`changes` で項目を差し替える。"""
    return {"status": result.STOPPED, "cause": {"kind": "refused", "detail": "断られた"}, "items": [AT_MERGE], **changes}


@pytest.mark.parametrize(
    "items",
    [
        [AT_MERGE],
        [{"kind": "step", "step": "ci"}, AT_MERGE],
        [AT_MERGE, {"kind": "plan", "step": "ci"}],
        ["merge", None, 1, AT_MERGE],
    ],
    ids=["only", "after-others", "before-others", "after-non-dicts"],
)
def test_a_plan_refused_at_the_merge_step_with_an_asked_method_is_refused(items):
    # プランの行が 1 つでもマージのステップなら真。並びの位置と、dict でない行は問わない
    assert merge_pending.refused(_report(items=items), {"issues": [1]}) is True


@pytest.mark.parametrize(
    "items",
    [
        [],
        None,
        "merge",
        AT_MERGE,
        (AT_MERGE,),
        ["merge", None],
        [{"kind": "plan", "step": "ci"}],
        [{"kind": "step", "step": "merge"}],
        [{"kind": "plan"}, {"step": "merge"}],
        [{"kind": "plan", "step": "ci"}, {"kind": "step", "step": "merge"}],
    ],
    ids=["empty", "none", "str", "dict", "tuple", "non-dicts", "other-step", "other-kind", "missing-keys", "split-rows"],
)
def test_a_plan_not_stopped_at_the_merge_step_is_not_refused(items):
    # items がリストでなければ行が無いとして扱い、kind と step の両方が 1 つの行にそろわなければ偽
    assert merge_pending.refused(_report(items=items), {}) is False


def test_a_report_without_items_is_not_refused():
    report = _report()
    del report["items"]
    assert merge_pending.refused(report, {}) is False


@pytest.mark.parametrize(
    "changes",
    [
        {"status": result.OK},
        {"status": None},
        {"cause": None},
        {"cause": "refused"},
        {"cause": {}},
        {"cause": {"kind": "transient"}},
    ],
    ids=["ok", "no-status", "no-cause", "str-cause", "empty-cause", "other-cause"],
)
def test_a_plan_stopped_for_another_reason_is_not_refused(changes):
    assert merge_pending.refused(_report(**changes), {}) is False


@pytest.mark.parametrize("method", ["squash", "", None])
def test_a_method_given_as_a_stage_input_is_not_refused(method):
    # 工程の入力で渡した方式は問い直さない。値ではなく項目の有無で見る
    assert merge_pending.refused(_report(), {"merge_method": method}) is False
