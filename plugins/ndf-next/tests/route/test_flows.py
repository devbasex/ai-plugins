"""雛形から組んだプランの流れ（遷移の判断を台本の結果で回す）。各テストの docstring に確かめる Issue を書く。"""

from __future__ import annotations

import pytest

from ndf.contracts.exit_codes import GATE, OK, STOPPED
from ndf.core.plan.transition_decide import Outcome

from ._flow import gate, plan_of, step_of, stopped, walk


def test_fix_plan_runs_test_then_pr_then_merge_when_green():
    """C3-1（#1142 の要求の不足 a）: 即時修正の雛形は、テスト → PR → merge-when-green を順に流して終わる。"""
    visited, state = walk(plan_of("immediate_fix"), {})
    assert visited == ["test", "push", "pr", "conflict-check", "ci", "merge"]
    assert state.status == OK


def test_fix_plan_stops_on_failed_test_before_pr():
    """C3-1: 即時修正のテストが落ちたら PR を作らずに止まる（行き先は conductor）。"""
    visited, state = walk(plan_of("immediate_fix"), {"test": [stopped("test_failed")]})
    assert visited == ["test"]
    assert (state.status, state.kind) == (STOPPED, "test_failed")


def test_ci_fix_runs_failed_tests_locally_before_push():
    """#3188: CI の修正は、落ちたテストを手元で打って通してから push する。

    手元で落ちれば同じ原因の数え（repeat-check）を通って ci-fix へ戻り（push しない）、同じ原因で CI を往復しない。
    CI を待つのは修正の前と後の 2 回だけ。
    """
    script = {"ci": [stopped("test_failed")], "ci-local": [stopped("test_failed")]}
    visited, state = walk(plan_of("implement"), script)
    first_ci = visited.index("ci")
    after = visited[first_ci + 1 :]
    assert after[:6] == ["repeat-check", "ci-fix", "ci-local", "repeat-check", "ci-fix", "ci-local"]
    assert after[6] == "push"
    assert visited.count("ci") == 2
    assert state.status == OK


def test_push_retries_once_on_transient_rejection():
    """#1653: pr の前の push が一時的な拒否で落ちても、待ってから打ち直して続ける（プランを止めない）。"""
    visited, state = walk(plan_of("implement"), {"push": [stopped("push_rejected")]})
    assert visited[visited.index("push") + 1] == "push"
    assert state.status == OK


def test_push_rejected_twice_goes_to_fix_push():
    """#1653: 打ち直しても断られれば、push 前の検査の指摘を worker が直す（fix-push）へ進む。"""
    visited, _ = walk(plan_of("implement"), {"push": [stopped("push_rejected"), stopped("push_rejected")]})
    i = visited.index("fix-push")
    assert visited[i - 2 : i + 2] == ["push", "push", "fix-push", "push"]


def test_design_merge_returns_to_base_sync_on_conflict():
    """#2273: 設計 PR のマージのプランは、宛先との衝突（DIRTY）で止まらず、取り込み → 直し → push → 確かめ直しへ戻る。"""
    script = {"conflict-check": [stopped("base_conflict")], "base-sync": [stopped("base_conflict")], "merge-gate": [gate()]}
    visited, state = walk(plan_of("design_merge"), script, answers=["approved"])
    assert visited == ["conflict-check", "base-sync", "fix-base", "push", "conflict-check", "merge-gate", "merge"]
    assert state.status == OK


def test_conflicted_pr_is_synced_instead_of_waiting_for_ci():
    """#2752・#2680: 衝突して CI の始まらない PR を待たない。CI の待ちの前に衝突を確かめ、宛先の取り込みへ進む。"""
    for purpose in ("implement", "check", "close"):
        visited, state = walk(plan_of(purpose), {"conflict-check": [stopped("base_conflict")]})
        i = visited.index("conflict-check")
        assert visited[i + 1] == "base-sync", purpose
        assert visited.index("ci") > i
        assert state.status == OK


def test_design_worker_gate_ends_plan_at_gate():
    """#2867: 設計のプランで worker が承認ゲートを返すと、失敗でなく承認ゲートで終わる（人の判断待ちへ移せる）。"""
    visited, state = walk(plan_of("design"), {"design": [gate()]})
    assert visited == ["design"]
    assert state.status == GATE


def test_close_plan_ends_without_pr_when_nothing_changed():
    """#3039: 閉じのプランは、確定仕様化が変更を作らなければ PR を作らずに終える（No commits between で止まらない）。"""
    visited, state = walk(plan_of("close"), {"changes-check": [stopped("not_applicable")]})
    assert visited == ["spec", "changes-check"]
    assert state.status == OK


def test_docs_only_check_skips_whole_tests():
    """#3023: 文書だけの PR の検査は、全体テストとリファクタリングを打たずにコードレビューへ進む。"""
    visited, state = walk(plan_of("check"), {"assess": [stopped("not_applicable")], "merge-gate": [gate()]}, answers=["approved"])
    assert visited[:2] == ["assess", "review"]
    assert "test-all" not in visited and "refactor" not in visited
    assert state.status == OK


def test_merged_impl_plan_is_not_rerun():
    """#2349: 取り込み済みの実装のプランを流し直しても、worker も PR の作成も打たずに終える。"""
    visited, state = walk(plan_of("implement"), {"done-check": [stopped("not_applicable")]})
    assert visited == ["done-check"]
    assert state.status == OK


def test_behavior_is_rechecked_right_before_merge_gate():
    """#3003: 振る舞いの確認の後に head が進んでも（CI の修正の push）、マージの承認ゲートの直前に確かめ直す。"""
    script = {"ci": [stopped("test_failed")]}
    visited, _ = walk(plan_of("check"), script, answers=["approved"])
    gate_at = visited.index("merge-gate")
    assert visited[gate_at - 2 : gate_at] == ["ci", "behavior-recheck"]
    assert visited.index("ci-fix") < gate_at


def test_review_incomplete_continues_check():
    """#2370: コードレビューが欠けを残して終わっても（incomplete）、検査ごと落とさずに振る舞いの確認へ進む。"""
    visited, state = walk(plan_of("check"), {"review": [stopped("incomplete")]}, answers=["approved"])
    assert visited[visited.index("review") + 1] == "behavior"
    assert state.status == OK


def test_refactor_gets_sync_command():
    """#2049: 検査のプランのリファクタリングに --sync-command を渡す（生成元に触れる改善が push で止まらない）。"""
    step = step_of(plan_of("check", refactor=["ndf-refactor"], sync_command="make gen"), "refactor")
    assert step.run[step.run.index("--sync-command") + 1] == "make gen"


def test_same_ci_failure_streak_stops_to_human():
    """#2844: CI の落ちが同じ原因で続いたら（repeat-check が fix_limit）、修正を返す循環を止めて人へ戻す。"""
    visited, state = walk(plan_of("implement"), {"ci": [stopped("test_failed")], "repeat-check": [stopped("fix_limit")]})
    assert visited[-2:] == ["ci", "repeat-check"]
    assert (state.status, state.kind) == (STOPPED, "fix_limit")


# 現状固定（I-001）: 検査と設計 PR のマージの雛形が同じ形で持つマージの承認ゲートとマージ。
# 部品へまとめても、埋めたプランのステップと遷移が変わらないことを確かめる。
_MERGE_GATE = {"id": "merge-gate", "run": ["<gate>"], "on_answer": {"approved": "merge", "rejected": "end"}}


def test_merge_gate_and_merge_steps_are_fixed_in_check_and_design_merge():
    """現状固定: check と design_merge の merge-gate と merge は、埋めた後に同じステップになる。"""
    found = {p: [s for s in plan_of(p)["steps"] if s["id"] in ("merge-gate", "merge")] for p in ("check", "design_merge")}
    assert found["check"] == found["design_merge"]
    gate_step, merge = found["check"]
    assert gate_step == _MERGE_GATE
    assert (merge["run"][-2:], merge["next"]) == (["<branch>", "<merge_method>"], "end")


def test_merge_gate_rejected_ends_without_merge():
    """現状固定: マージの承認ゲートで rejected と答えると、merge を打たずに ok で終わる。"""
    expected_tail = {"check": ["ci", "behavior-recheck", "merge-gate"], "design_merge": ["conflict-check", "merge-gate"]}
    for purpose, tail in expected_tail.items():
        visited, state = walk(plan_of(purpose), {"merge-gate": [gate()]}, answers=["rejected"])
        assert visited[-len(tail) :] == tail, purpose
        assert "merge" not in visited, purpose
        assert state.status == OK, purpose


def test_merge_gate_approved_merges_then_ends():
    """現状固定: マージの承認ゲートで approved と答えると、merge を打って終わる。"""
    for purpose in ("check", "design_merge"):
        visited, state = walk(plan_of(purpose), {"merge-gate": [gate()]}, answers=["approved"])
        assert visited[-2:] == ["merge-gate", "merge"], purpose
        assert state.status == OK, purpose


def test_same_local_test_failure_streak_stops_to_human():
    """手元のテストの落ち（test → fix → test、検査では test-all → fix → test-all）も同じ原因の数え（test-repeat）を通り、
    続けば fix_limit で人へ戻す（プラン全体のステップの上限まで worker とテストを繰り返さない）。"""
    for purpose, test_id in (("implement", "test"), ("check", "test-all")):
        script = {test_id: [stopped("test_failed")] * 5, "test-repeat": [Outcome(OK), stopped("fix_limit")]}
        visited, state = walk(plan_of(purpose), script)
        i = visited.index(test_id)
        assert visited[i:] == [test_id, "test-repeat", "fix", test_id, "test-repeat"], purpose
        assert (state.status, state.kind) == (STOPPED, "fix_limit"), purpose


def test_design_plan_checks_ci_after_push():
    """#2932（#2741 の形）: 設計のプランは push の後に CI を待つ。CI の合格は push の前の worker（design）でなく、
    push → PR → レビュー → push の後の CI の待ちが確かめる。"""
    visited, state = walk(plan_of("design"), {})
    assert visited == ["design", "rebuild", "push", "pr", "head-check", "review", "push-reviewed", "conflict-check", "ci"]
    assert state.status == OK


@pytest.mark.parametrize("kind", ["test_failed", "check_failed"])
def test_design_plan_ci_failure_is_fixed_then_pushed_and_rechecked(kind):
    """#2932: 設計の CI が落ちたら（テストでも、設計文書の検査・静的解析などテスト以外のチェックでも）同じ原因の数えを
    通って worker が直し、push してから CI を待ち直す（止まらない）。"""
    visited, state = walk(plan_of("design"), {"ci": [stopped(kind)]})
    i = visited.index("ci")
    assert visited[i + 1 :] == ["repeat-check", "ci-fix", "push-reviewed", "conflict-check", "ci"]
    assert state.status == OK


def test_design_plan_stops_before_review_when_pr_head_lags():
    """#2932（#2721 の形）: PR の作成の後、手元のコミットが PR の head に入っていなければ、間を置いて 3 回まで読み直し、
    それでも違えばレビューへ進まずに止まる。"""
    visited, state = walk(plan_of("design"), {"head-check": [stopped("precondition")] * 4})
    assert visited[-5:] == ["pr", "head-check", "head-check", "head-check", "head-check"]
    assert "review" not in visited
    assert (state.status, state.kind) == (STOPPED, "precondition")


def test_design_plan_rereads_pr_head_until_github_catches_up():
    """#3292: 既存の PR へ push した直後は GitHub の PR の head の更新が遅れる。読み直して揃えばレビューへ進む。"""
    visited, state = walk(plan_of("design"), {"head-check": [stopped("precondition")]})
    i = visited.index("head-check")
    assert visited[i : i + 3] == ["head-check", "head-check", "review"]
    assert state.status == OK


def test_design_review_gets_rebuild_findings():
    """#2444: 組み直しが残した「足すべき内容」（rebuild の --out のファイル）を、後のレビューの --findings へ渡す。

    レビューが欠けを残して終わっても（incomplete）設計のプランを落とさずに push と CI へ進む。
    """
    plan = plan_of("design", rebuild=["ndf-rebuild"], review=["ndf-review"])
    rebuild, review = step_of(plan, "rebuild"), step_of(plan, "review")
    out = rebuild.run[rebuild.run.index("--out") + 1]
    assert review.run[review.run.index("--findings") + 1] == out
    visited, state = walk(plan, {"review": [stopped("incomplete")]})
    assert visited.index("rebuild") < visited.index("review")
    assert visited[visited.index("review") + 1] == "push-reviewed"
    assert state.status == OK
