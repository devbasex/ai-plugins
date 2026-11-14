"""雛形から組んだプランの流れ（遷移の判断を台本の結果で回す）。各テストの docstring に確かめる Issue を書く。"""

from __future__ import annotations

from ndf.contracts.exit_codes import GATE, OK, STOPPED

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

    手元で落ちれば ci-fix へ戻り（push しない）、同じ原因で CI を往復しない。CI を待つのは修正の前と後の 2 回だけ。
    """
    script = {"ci": [stopped("test_failed")], "ci-local": [stopped("test_failed")]}
    visited, state = walk(plan_of("implement"), script)
    first_ci = visited.index("ci")
    after = visited[first_ci + 1 :]
    assert after[:5] == ["repeat-check", "ci-fix", "ci-local", "ci-fix", "ci-local"]
    assert after[5] == "push"
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
