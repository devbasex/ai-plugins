"""コミットの粒度（1 改善項目 = 1 コミット）のテスト。

手順を 1 手ずつ進めることと、その途中経過を履歴に残すことは別である。
**残すのは項目単位の 1 コミットだけ**にして、Pull Request を読む側が
改善項目と履歴を 1 対 1 で辿れるようにする。

現状固定テストが要る項目（`test_gap`）だけは、テストと実装を混ぜないために
2 コミットを許す。
"""
from __future__ import annotations

import pytest


def trailers(item_id="R1-001", round_no="1", runtime="codex", model="gpt-5.5"):
    return {
        "Item-Id": item_id, "Round": round_no,
        "Impl-Runtime": runtime, "Impl-Model": model,
    }


def fact(sha="abc1234", **over):
    base = {
        "sha": sha, "exists": True, "test_status": "pass",
        "touches_tests": False, "diff_lines": 30, "trailers": trailers(),
    }
    base.update(over)
    return base


def item(**over):
    base = {
        "item_id": "R1-001", "round": 1, "path": "src/foo.py", "symbol": "Foo.handle",
        "smell": "long_method", "technique": "extract_method", "severity": "major",
        "rationale": "", "plan": "", "test_gap": False,
        "estimated_diff_lines": 400, "proposed_by": ["codex"],
        "status": "pending", "commits": [],
    }
    base.update(over)
    return base


# ---------- 適用フェーズ ----------

def test_one_commit_per_item_passes(refactor):
    assert refactor.verify_apply_item(item(), [fact()]) is None


def test_two_commits_for_one_item_fails(refactor):
    """途中経過を刻むと、改善項目と履歴が 1 対 1 で対応しなくなる。"""
    problem = refactor.verify_apply_item(
        item(), [fact(sha="aaa"), fact(sha="bbb")]
    )
    assert problem is not None and "1 コミット" in problem


def test_the_granularity_message_names_the_item(refactor):
    """どの項目が刻みすぎたのかを読めるようにする。"""
    problem = refactor.verify_apply_item(
        item(item_id="R2-003"), [fact(sha="aaa", trailers=trailers(item_id="R2-003")),
                                 fact(sha="bbb", trailers=trailers(item_id="R2-003"))]
    )
    assert "R2-003" in problem


def test_test_gap_allows_the_characterization_test_commit(refactor):
    """テストと実装を 1 コミットへ混ぜないため、この項目だけ 2 コミットを許す。"""
    facts = [fact(sha="aaa", touches_tests=True), fact(sha="bbb")]
    assert refactor.verify_apply_item(item(test_gap=True), facts) is None


def test_test_gap_still_rejects_three_commits(refactor):
    facts = [fact(sha="aaa", touches_tests=True), fact(sha="bbb"), fact(sha="ccc")]
    problem = refactor.verify_apply_item(item(test_gap=True), facts)
    assert problem is not None and "2 コミット" in problem


def test_a_broken_commit_is_reported_before_the_granularity(refactor):
    """粒度は最後に見る。トレーラーやテストの問題を粒度で覆い隠さない。"""
    problem = refactor.verify_apply_item(
        item(), [fact(sha="aaa"), fact(sha="bbb", test_status="fail")]
    )
    assert problem is not None and "テストが成功していません" in problem


def test_the_budget_is_reported_before_the_granularity(refactor):
    """差分予算の超過は原因が別なので、粒度より先に伝える。"""
    problem = refactor.verify_apply_item(
        item(technique="rename", estimated_diff_lines=10),
        [fact(sha="aaa", diff_lines=50), fact(sha="bbb", diff_lines=50)],
    )
    assert problem is not None and "差分予算" in problem


# ---------- 修正フェーズ ----------

def test_fix_accepts_one_commit_per_item(refactor):
    facts = [fact(sha="aaa", trailers=trailers(item_id="R1-001")),
             fact(sha="bbb", trailers=trailers(item_id="R1-002"))]
    problems, accepted = refactor._verify_fix_commits(facts, ["src"])
    assert problems == []
    assert accepted == [("R1-001", "aaa"), ("R1-002", "bbb")]


def test_fix_rejects_two_commits_for_the_same_item(refactor):
    """適用側だけ揃えると、指摘への対応という名目で刻んだ履歴が戻ってくる。"""
    facts = [fact(sha="aaa", trailers=trailers(item_id="R1-001")),
             fact(sha="bbb", trailers=trailers(item_id="R1-001"))]
    problems, _ = refactor._verify_fix_commits(facts, ["src"])
    assert problems and any("R1-001" in p and "1 コミット" in p for p in problems)


def test_fix_granularity_does_not_hide_a_broken_commit(refactor):
    facts = [fact(sha="aaa", trailers=trailers(item_id="R1-001")),
             fact(sha="bbb", test_status="fail", trailers=trailers(item_id="R1-001"))]
    problems, _ = refactor._verify_fix_commits(facts, ["src"])
    assert any("テストが成功していません" in p for p in problems)


@pytest.mark.parametrize("count", [1, 2, 3])
def test_fix_allows_one_commit_for_each_distinct_item(refactor, count):
    facts = [fact(sha=f"s{i}", trailers=trailers(item_id=f"R1-00{i}"))
             for i in range(count)]
    problems, accepted = refactor._verify_fix_commits(facts, ["src"])
    assert problems == [] and len(accepted) == count
