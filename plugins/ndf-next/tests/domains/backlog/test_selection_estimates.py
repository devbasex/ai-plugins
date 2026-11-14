"""候補を上限で切る並びと見積の採り方（#2064・#1640・#2540）。"""

from __future__ import annotations

from ndf.domains.backlog import estimates_decide, model
from ndf.domains.backlog import selection_decide as sel
from ndf.domains.backlog.model import Config


def test_manual_issues_are_never_cut_by_the_limit():
    # #2064 の表: --add の 33 件と diff-path の 38 件で --limit 45 のとき、--add の 33 件がすべて残る
    found = {n: {sel.DIFF_PATH} for n in range(1, 39)}
    found |= {n: {sel.MANUAL} for n in range(1600, 1633)}
    got = sel.select(found, 45, model.DIFF, {})
    assert set(range(1600, 1633)) <= set(got.keep)
    assert len(got.keep) == 45 and len(got.deferred) == 26


def test_manual_beyond_the_limit_is_kept_and_counted():
    found = {n: {sel.MANUAL} for n in range(1, 6)}
    got = sel.select(found, 3, model.DIFF, {})
    assert got.keep == [1, 2, 3, 4, 5] and got.manual_over == 2


def test_carried_issues_come_before_other_routes():
    # #1640: 前回の持ち越しは経路の多い課題より先に残る
    found = {1: {sel.DIFF_PATH, sel.NO_MILESTONE}, 2: {sel.DIFF_PATH, sel.NO_MILESTONE}, 50: {sel.CARRIED}}
    got = sel.select(found, 1, model.DIFF, {})
    assert got.keep == [50] and got.deferred == [1, 2]


def test_review_keeps_manual_first_then_oldest():
    found = {1: {sel.ALL}, 2: {sel.ALL}, 3: {sel.ALL, sel.MANUAL}}
    got = sel.select(found, 2, model.REVIEW, {1: "2026-11-02", 2: "2026-10-01", 3: "2026-11-09"})
    assert got.keep == [3, 2]


def test_carry_does_not_count_issues_deferred_only_for_missing_estimates():
    # #2540: 見積の欠けだけで入った課題は、見直しの起動の材料（carry）に数えない
    found = {1: {sel.MANUAL}, **{n: {sel.UNSCORED} for n in range(10, 20)}, 30: {sel.DIFF_PATH, sel.UNSCORED}}
    got = sel.select(found, 1, model.DIFF, {})
    assert len(got.deferred) == 11 and got.carry == 1


def _raw(n: int, digest: str | None = None) -> dict:
    return {"number": n, "ubv": 3, "tc": 2, "rr_oe": 1, "size": 2, "digest": digest}


def test_review_excludes_stale_and_restored_estimates():
    # #2540: 見直しの回に、要約値の違う記録と前回の表から戻した見積を excluded で返す
    stored = {"1": _raw(1, "now1"), "2": _raw(2, "old2")}
    previous = {3: {"number": 3, "ubv": 3, "tc": 2, "rr_oe": 1, "size": 2}}
    digests = {1: "now1", 2: "now2", 3: "now3", 4: "now4"}
    ests, excluded = estimates_decide.estimates([1, 2, 3, 4], {}, stored, previous, Config(), digests)
    assert list(ests) == [1]
    assert {e["number"]: e["reason"] for e in excluded} == {
        2: estimates_decide.STALE, 3: estimates_decide.STALE, 4: estimates_decide.MISSING,
    }  # fmt: skip


def test_diff_depth_takes_stored_and_previous_estimates():
    stored = {"2": _raw(2, "old2")}
    previous = {3: {"number": 3, "ubv": 3, "tc": 2, "rr_oe": 1, "size": 2}}
    ests, excluded = estimates_decide.estimates([2, 3], {}, stored, previous, Config())
    assert sorted(ests) == [2, 3] and excluded == []


def test_given_estimates_need_a_digest_and_a_scale_value():
    given = {1: {"ubv": 3, "tc": 2, "rr_oe": 1, "size": 2}, 2: {**_raw(2, "x"), "ubv": 4}}
    ests, excluded = estimates_decide.estimates([1, 2], given, {}, {}, Config())
    assert ests == {} and excluded[0]["reason"] == estimates_decide.NO_DIGEST and "UBV" in excluded[1]["reason"]


def test_unscored_matches_the_review_rule():
    stored = {"1": _raw(1, "a"), "2": _raw(2, "old")}
    assert estimates_decide.unscored([1, 2, 3], stored, {1: "a", 2: "new", 3: "c"}) == [2, 3]
    assert estimates_decide.unscored([1], {}, {1: "a"}) == []


# --- 現状固定: 記録の鮮度（_pick の記録の枝と unscored）。辞書でない記録と digest の欠けた記録の扱いを記録する ---


def test_review_treats_non_dict_and_digestless_records_as_stale():
    stored = {"1": "壊れた記録", "2": {"number": 2, "ubv": 3, "tc": 2, "rr_oe": 1, "size": 2}, "3": _raw(3, "now3")}
    digests = {1: "now1", 2: "now2", 3: "now3"}
    ests, excluded = estimates_decide.estimates([1, 2, 3], {}, stored, {}, Config(), digests)
    assert list(ests) == [3]
    assert [(e["number"], e["reason"]) for e in excluded] == [(1, estimates_decide.STALE), (2, estimates_decide.STALE)]
    assert estimates_decide.unscored([3, 2, 1, 4], stored, {**digests, 4: "now4"}) == [2, 1, 4]


def test_review_checks_the_given_scores_against_the_current_issue():
    # 候補の収集の後に本文が書き換わった課題の古い scores で見直しを終えない
    given = {1: _raw(1, "now1"), 2: _raw(2, "old2")}
    ests, excluded = estimates_decide.estimates([1, 2], given, {}, {}, Config(), {1: "now1", 2: "now2"})
    assert list(ests) == [1] and excluded == [{"number": 2, "reason": estimates_decide.STALE}]


def test_issues_deferred_only_for_missing_estimates_stay_out_of_carry_when_carried():
    # 見積の欠けだけで持ち越した課題は、次の回に carried が付いても carry に数えない
    first = sel.select({1: {sel.MANUAL}, 10: {sel.UNSCORED}, 30: {sel.DIFF_PATH}}, 1, model.DIFF, {})
    assert first.unscored_only == [10] and first.carry == 1
    found = {1: {sel.MANUAL}, 10: {sel.CARRIED, sel.UNSCORED}, 30: {sel.CARRIED, sel.DIFF_PATH}}
    again = sel.select(found, 1, model.DIFF, {}, frozenset(first.unscored_only))
    assert sorted(again.deferred) == [10, 30] and again.carry == 1 and again.unscored_only == [10]
