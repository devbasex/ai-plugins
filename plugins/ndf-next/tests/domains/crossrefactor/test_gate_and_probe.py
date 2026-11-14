"""最終ゲートとテンプレートの試走の判断。

- #3038: 最終ゲートの直しが、全体テストの失敗を直すためにリファクタリング計画の対象範囲の外を変える
- #2680: 最終ゲートが、衝突して CI の始まらない Pull Request を上限まで待って止まる
- #2158: 着手前のテストが上限の近くで通ると、テンプレートの試走の残りが数秒になり init が止まる
"""

from __future__ import annotations

import pytest

from ndf.contracts import causes
from ndf.domains.crossrefactor import final_gate_decide, probe_decide


def test_fix_that_changes_a_file_outside_the_plan_is_not_taken():
    # volareinc/carte の PR #81 の形: 範囲は backend なのに、直しが frontend/vite.config.ts を変えた
    got = final_gate_decide.verify_final_fix(
        {"a1": ["backend/app/models.py"], "b2": ["backend/app/views.py", "frontend/vite.config.ts"]},
        ["backend/app"],
    )
    assert not got.ok
    assert got.accepted == ("a1",)
    assert got.outside == (("b2", "frontend/vite.config.ts"),)
    assert got.record(["tests/test_ui.py::test_build"]) == {
        "failed_tests": ["tests/test_ui.py::test_build"],
        "outside_files": ["frontend/vite.config.ts"],
        "commits": ["b2"],
    }


def test_fix_inside_the_plan_is_taken():
    got = final_gate_decide.verify_final_fix({"a1": ["backend/app/models.py"]}, ["backend/app"])
    assert got.ok and got.accepted == ("a1",)


def test_new_file_outside_the_plan_counts_as_outside():
    got = final_gate_decide.verify_final_fix({"c3": ["scripts/new_helper.py"]}, ["backend"])
    assert got.outside == (("c3", "scripts/new_helper.py"),)


def test_missing_target_scope_is_an_error_not_a_pass():
    with pytest.raises(ValueError, match="target_scope"):
        final_gate_decide.verify_final_fix({"a1": ["x.py"]}, [])


def test_stop_causes_route_outside_fixes_to_a_human_and_conflicts_to_the_base_merge():
    outside = final_gate_decide.verify_final_fix({"b2": ["frontend/vite.config.ts"]}, ["backend"]).cause()
    assert causes.row(outside["kind"]).route == "human" and "frontend/vite.config.ts" in outside["detail"]
    assert final_gate_decide.before_ci_wait("CONFLICTING", "DIRTY").cause()["kind"] == "base_conflict"
    assert final_gate_decide.before_ci_wait("MERGEABLE", "CLEAN").cause() is None


@pytest.mark.parametrize(
    ("mergeable", "state", "kind"),
    [
        ("CONFLICTING", "DIRTY", "conflict"),
        ("MERGEABLE", "DIRTY", "conflict"),
        ("UNKNOWN", "UNKNOWN", "recheck"),
        (None, None, "recheck"),
        ("MERGEABLE", "BLOCKED", "wait"),
        ("MERGEABLE", "CLEAN", "wait"),
    ],
)
def test_conflicting_pull_request_is_not_waited_for(mergeable, state, kind):
    # PR #2676・#2979 の形: CONFLICTING・DIRTY の PR は CI が 1 本も始まらない
    assert final_gate_decide.before_ci_wait(mergeable, state).kind == kind


def test_probe_budget_keeps_the_floor_when_the_baseline_used_almost_everything():
    assert probe_decide.probe_budget(540, 100, 120) == 440
    assert probe_decide.probe_budget(540, 533.9, 120) == 120


def test_probe_floor_comes_from_the_limits_declaration():
    assert probe_decide.probe_floor() == 120
