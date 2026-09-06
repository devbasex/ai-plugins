"""コミットの粒度のテスト。

**適用は適用ラウンド（群）ごとに 1 コミットである**（#436 決定 2）。取り消しの
単位と一致させるためで、群の中の項目はまとめて 1 つのコミットへ入れる。

修正コミットの側は項目ごとに 1 コミットのままである。現状固定テストが要る項目
（`test_gap`）だけは、テストと実装を混ぜないために 2 コミットを許す。
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


# ---------- 適用フェーズ（適用ラウンド = 1 コミット） ----------

def test_one_commit_for_the_apply_round_passes(refactor):
    assert refactor.verify_apply_round([item()], [fact()]) is None


def test_several_items_share_the_one_commit(refactor):
    """群の中の項目はまとめて 1 コミットへ入れる。**同じ SHA の申告は正しい形。**"""
    items = [item(item_id="R1-001"), item(item_id="R1-002", symbol="Foo.other")]
    assert refactor.verify_apply_round(items, [fact()]) is None


def test_two_commits_in_one_apply_round_fail(refactor):
    """途中経過を刻むと、取り消しの単位とコミットの単位が食い違う。"""
    problem = refactor.verify_apply_round(
        [item()], [fact(sha="aaa"), fact(sha="bbb")]
    )
    assert problem is not None and "適用ラウンド = 1 コミット" in problem


def test_the_granularity_message_shows_the_count(refactor):
    """何コミットに刻まれたのかを読めるようにする。"""
    problem = refactor.verify_apply_round(
        [item()], [fact(sha="aaa"), fact(sha="bbb"), fact(sha="ccc")]
    )
    assert "3 件" in problem


def test_test_gap_still_needs_the_characterization_test_in_the_commit(refactor):
    """テストが乏しい項目を含む群は、そのコミットがテストを触っていること。"""
    assert refactor.verify_apply_round(
        [item(test_gap=True)], [fact(touches_tests=True)]
    ) is None
    problem = refactor.verify_apply_round([item(test_gap=True)], [fact()])
    assert problem is not None and "現状固定テスト" in problem


def test_test_gap_does_not_buy_a_second_commit(refactor):
    """テストを分けたい場合も、群のコミットは 1 つである。"""
    facts = [fact(sha="aaa", touches_tests=True), fact(sha="bbb")]
    problem = refactor.verify_apply_round([item(test_gap=True)], facts)
    assert problem is not None and "適用ラウンド = 1 コミット" in problem


def test_a_broken_commit_is_reported_before_the_granularity(refactor):
    """粒度は最後に見る。トレーラーの問題を粒度で覆い隠さない。"""
    broken = fact(sha="bbb", trailers=trailers(model=""))
    problem = refactor.verify_apply_round([item()], [fact(sha="aaa"), broken])
    assert problem is not None and "トレーラーが欠けています" in problem


def test_the_budget_is_reported_before_the_granularity(refactor):
    """差分予算の超過は原因が別なので、粒度より先に伝える。"""
    problem = refactor.verify_apply_round(
        [item(technique="rename", estimated_diff_lines=10)],
        [fact(sha="aaa", diff_lines=50), fact(sha="bbb", diff_lines=50)],
    )
    assert problem is not None and "差分予算" in problem


def test_the_apply_check_does_not_look_at_the_test_result(refactor):
    """**テストの合否は `verify-round` が見る**（決定 3）。

    適用そのものが通らないことと、テストが落ちることは扱いが違う。前者はその群を
    取り消し、後者は修正ラウンドを回す。
    """
    assert refactor.verify_apply_round(
        [item()], [fact(test_status="fail")]
    ) is None


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
