"""マージがその方式で断られて止まったかの判定（`merge_pending.refused`）の現状固定。"""

from __future__ import annotations

import json

import pytest

from ndf.contracts import result, worker_paths
from ndf.progress import merge_pending

AT_MERGE = {"kind": "plan", "step": "merge"}


def _report(**changes) -> dict:
    """マージのステップで断られて止まったプランの結果。`changes` で項目を差し替える。"""
    return {"status": result.STOPPED, "cause": {"kind": "merge_method", "detail": "断られた"}, "items": [AT_MERGE], **changes}


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
        {"cause": "merge_method"},
        {"cause": {}},
        {"cause": {"kind": "transient"}},
        {"cause": {"kind": "refused"}},  # HEAD の食い違い・決めていない残リスク（PR 4325 の r1-4）
    ],
    ids=["ok", "no-status", "no-cause", "str-cause", "empty-cause", "other-cause", "other-refusal"],
)
def test_a_plan_stopped_for_another_reason_is_not_refused(changes):
    assert merge_pending.refused(_report(**changes), {}) is False


@pytest.mark.parametrize("method", ["squash", "", None])
def test_a_method_given_as_a_stage_input_is_not_refused(method):
    # 工程の入力で渡した方式は GitHub にもう一度問わない。値ではなく項目の有無で見る
    assert merge_pending.refused(_report(), {"merge_method": method}) is False


KEY = {"repo": "o/r", "base": "develop", "remote": "origin"}


def test_a_remembered_method_is_read_back_for_the_same_values(tmp_path):
    pending = tmp_path / merge_pending.PENDING_FILE
    assert merge_pending.read(pending) is None
    merge_pending.mark(pending)
    assert merge_pending.read(pending) == {}
    merge_pending.remember(pending, KEY, "squash")
    held = merge_pending.read(pending)
    assert merge_pending.held_method(held, KEY) == "squash"
    assert merge_pending.held_method(held, {**KEY, "base": "main"}) is None


@pytest.mark.parametrize("method", ["octopus", 1, None, ["squash"]])
def test_an_unknown_method_is_neither_kept_nor_used(tmp_path, method):
    # PR 4325 の r1-2: 保存済みの任意の値を方式として使わない
    pending = tmp_path / merge_pending.PENDING_FILE
    merge_pending.remember(pending, KEY, method)
    assert merge_pending.read(pending) is None
    assert merge_pending.held_method({"key": list(KEY.values()), "merge_method": method}, KEY) is None


def test_a_mark_others_can_write_or_a_link_is_not_read_or_followed(tmp_path):
    # PR 4325 の r1-2: 目印は values.json と同じ検証を通す
    pending = tmp_path / merge_pending.PENDING_FILE
    pending.write_text(json.dumps({"key": list(KEY.values()), "merge_method": "squash"}), encoding="utf-8")
    pending.chmod(0o666)
    with pytest.raises(worker_paths.EntryUnsafe) as e:
        merge_pending.read(pending)
    assert e.value.kind == "foreign"
    outside = tmp_path / "outside"
    outside.write_text("利用者のファイル", encoding="utf-8")
    pending.unlink()
    pending.symlink_to(outside)
    for act in (lambda: merge_pending.read(pending), lambda: merge_pending.mark(pending)):
        with pytest.raises(worker_paths.EntryUnsafe):
            act()
    assert outside.read_text(encoding="utf-8") == "利用者のファイル"
