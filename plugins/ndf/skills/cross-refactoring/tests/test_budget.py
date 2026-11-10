"""実装の予算から採る項目を決める計算（#933 の「時間の決め方」、#2457 決定 1・2・I1〜I3）。"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture(scope="module")
def budget(refactor):
    return importlib.import_module("refactor_lib.budget")


TABLE = {
    "source": "defaults",
    "test": 2.7,
    "structure": 1.3,
    "verify": 0.2,
    "fix": 5.5,
    "kinds": {"structure/extract_method": 2.0},
}


def _cand(tier="high", n=1, severity="major", est=None, key="k"):
    return {
        "key": key,
        "tier": tier,
        "proposed_by": ["x"] * n,
        "severity": severity,
        "estimate": est or {"test": 0.0, "implement": 1.5},
    }


def test_the_estimate_is_the_test_and_the_implementation_only(budget):
    """決定 1: 見積りに検証を入れない。"""
    assert budget.item_estimate(TABLE, "extract_method", True) == {"test": 2.7, "implement": 2.0}
    assert budget.item_estimate(TABLE, "rename", False) == {"test": 0.0, "implement": 1.3}
    assert budget.estimate_total({"test": 2.7, "implement": 2.0, "verify": 30.0}) == pytest.approx(4.7)


def test_rank_key_orders_tier_votes_severity_then_cheaper(budget):
    low = _cand(tier="low", n=3, severity="critical", key="low")
    high_1 = _cand(tier="high", n=1, key="high_1")
    high_2 = _cand(tier="high", n=2, severity="minor", key="high_2")
    high_2_major = _cand(tier="high", n=2, severity="major", key="high_2_major")
    cheap = _cand(tier="high", n=2, severity="major", key="cheap", est={"test": 0, "implement": 0.5})
    ranked = sorted([low, high_1, high_2, high_2_major, cheap], key=budget.rank_key)
    assert [c["key"] for c in ranked] == ["cheap", "high_2_major", "high_2", "high_1", "low"]


def test_select_skips_items_that_do_not_fit_and_keeps_filling(budget):
    """受け入れ条件 3: 順位の順に入る候補を入れ、入らない候補は見送る（飛ばして次を見る）。"""
    big = _cand(key="big", est={"test": 2.7, "implement": 10})
    small1 = _cand(key="s1")
    small2 = _cand(key="s2")
    selected, skipped = budget.select([small1, big, small2], 4.0)
    assert [c["key"] for c in selected] == ["s1", "s2"]
    assert [c["key"] for c in skipped] == ["big"]


def test_select_accepts_exact_fit_despite_float_error(budget):
    items = [_cand(key=str(i)) for i in range(29)]  # 1.5 分 × 29 = 43.5 分
    selected, skipped = budget.select(items, 43.5)
    assert len(selected) == 29 and skipped == []


@pytest.mark.parametrize(
    "high, budget_minutes, expected",
    [
        ([20, 20], 60, None),  # 和 40 ≤ 予算 60: 延ばさない
        ([40, 30.5], 60, 71),  # 60 < 和 70.5 ≤ 120: ceil(和) へ延ばす
        ([60, 60], 60, 120),  # 和がちょうど 2 倍: 延ばす
        ([70, 60], 60, None),  # 和 130 > 120: 延ばさない
    ],
)
def test_the_budget_is_extended_only_between_the_budget_and_twice_it(budget, high, budget_minutes, expected):
    """受け入れ条件 4・I2: high の見積りの和が予算と 2 倍の間にあるときだけ ceil(和) へ延ばし、理由と前後の値を残す。"""
    ranked = [_cand(tier="high", key=f"h{i}", est={"test": 0, "implement": m}) for i, m in enumerate(high)]
    ranked.append(_cand(tier="medium", key="m", est={"test": 0, "implement": 50}))
    ext = budget.extension(ranked, budget_minutes, factor=2)
    if expected is None:
        assert ext is None
        return
    assert ext["from"] == budget_minutes and ext["to"] == expected
    assert ext["high_minutes"] == pytest.approx(sum(high))
    assert "high の候補の見積りの和" in ext["reason"]
    assert budget.effective_minutes({"minutes": budget_minutes, "extension": ext}) == expected


def test_readopt_left_counts_the_adopted_estimates_not_the_actuals(budget):
    """I1・I3: 採り直しの残りは「実効の予算 − 採っていて取り消していない項目の見積りの和」。実装時間の実績は使わない。"""
    plan_budget = {"minutes": 60, "extension": None}
    a = {"status": "verified", "estimate": {"test": 0, "implement": 40}, "seconds": {"implement": 20 * 60}}
    b = {"status": "verified", "estimate": {"test": 0, "implement": 20}, "seconds": {"implement": 0}}
    assert budget.readopt_left(plan_budget, [a, b]) == pytest.approx(0.0)
    b["status"] = "reverted"
    assert budget.readopt_left(plan_budget, [a, b]) == pytest.approx(20.0)


def test_the_safety_net_is_twice_the_effective_budget_unless_given(budget):
    """決定 5: 安全網の上限は実効の予算 × 倍率（既定 2）。明示すればその値。"""
    assert budget.safety_net_minutes({"minutes": 60, "extension": None}) == pytest.approx(120.0)
    assert budget.safety_net_minutes({"minutes": 60, "extension": {"to": 80}}) == pytest.approx(160.0)
    assert budget.safety_net_minutes({"minutes": 60}, 15) == pytest.approx(15.0)
