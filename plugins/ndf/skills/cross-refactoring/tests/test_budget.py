"""想定最大時間から採る項目と締め切りを決める計算（#933 の「時間の決め方」）。"""
from __future__ import annotations

import datetime as dt
import importlib

import pytest


@pytest.fixture(scope="module")
def budget(refactor):
    return importlib.import_module("refactor_lib.budget")


TABLE = {
    "source": "defaults", "test": 2.7, "structure": 1.3, "verify": 0.2, "fix": 5.5,
    "kinds": {"structure/extract_method": 2.0},
}
START = dt.datetime(2026, 9, 24, 10, 0, 0)


def _cand(tier="high", n=1, severity="major", est=None, key="k"):
    return {"key": key, "tier": tier, "proposed_by": ["x"] * n, "severity": severity,
            "estimate": est or {"test": 0.0, "implement": 1.3, "verify": 0.2}}


def test_item_estimate_uses_technique_and_falls_back(budget):
    assert budget.item_estimate(TABLE, "extract_method", True) == {
        "test": 2.7, "implement": 2.0, "verify": 0.2}
    # 標本の無い手法は structure/* をまとめた値へ落ちる。テストを足さなければ 0
    assert budget.item_estimate(TABLE, "rename", False) == {
        "test": 0.0, "implement": 1.3, "verify": 0.2}


def test_reserve_matches_the_917_example(budget):
    r = budget.reserve(60, False, 5.5)
    assert r == {"danger_whole_test": 1.0, "final_whole_test": 1.0, "fix": 5.5}
    assert budget.reserve_total(r) == pytest.approx(7.5)
    # 経過 9 分で使える時間は 60 − 9 − 7.5 = 43.5 分（設計の例）
    assert budget.available_minutes(60, 9, r) == pytest.approx(43.5)


def test_reserve_ci_check_and_missing_baseline(budget):
    assert budget.reserve(120, True, 5.5)["final_whole_test"] == 0.0
    assert budget.reserve(None, False, 3.0) == {
        "danger_whole_test": 0.0, "final_whole_test": 0.0, "fix": 3.0}


def test_rank_key_orders_tier_votes_severity_then_cheaper(budget):
    low = _cand(tier="low", n=3, severity="critical", key="low")
    high_1 = _cand(tier="high", n=1, key="high_1")
    high_2 = _cand(tier="high", n=2, severity="minor", key="high_2")
    high_2_major = _cand(tier="high", n=2, severity="major", key="high_2_major")
    cheap = _cand(tier="high", n=2, severity="major", key="cheap",
                  est={"test": 0, "implement": 0.5, "verify": 0.2})
    ranked = sorted([low, high_1, high_2, high_2_major, cheap], key=budget.rank_key)
    assert [c["key"] for c in ranked] == ["cheap", "high_2_major", "high_2", "high_1", "low"]


def test_select_skips_items_that_do_not_fit_and_keeps_filling(budget):
    big = _cand(key="big", est={"test": 2.7, "implement": 10, "verify": 0.2})
    small1 = _cand(key="s1")
    small2 = _cand(key="s2")
    selected, skipped = budget.select([small1, big, small2], 4.0)
    assert [c["key"] for c in selected] == ["s1", "s2"]
    assert [c["key"] for c in skipped] == ["big"]


def test_select_accepts_exact_fit_despite_float_error(budget):
    items = [_cand(key=str(i)) for i in range(29)]  # 1.5 分 × 29 = 43.5 分
    selected, skipped = budget.select(items, 43.5)
    assert len(selected) == 29 and skipped == []


def test_end_time_and_deadlines(budget):
    T = budget.end_time(START, 60, 7.5)
    assert T == START + dt.timedelta(minutes=52.5)
    a = {"estimate": {"test": 2.0, "implement": 3.0, "verify": 1.0}}
    b = {"estimate": {"test": 0.0, "implement": 4.0, "verify": 1.0}}
    out = budget.deadlines([a, b], T)
    m = dt.timedelta(minutes=1)
    # 実装: T − Σ_{j≥i} implement − Σ verify
    assert out[0]["start_deadline"] == T - (3 + 4 + 2) * m
    assert out[1]["start_deadline"] == T - (4 + 2) * m
    # テストの追加: T − Σ(implement + verify) − Σ_{j≥i} test。足さない項目は None
    assert out[0]["test_start_deadline"] == T - (9 + 2) * m
    assert out[1]["test_start_deadline"] is None


def test_fix_time_left_does_not_subtract_fix_reserve(budget):
    r = {"danger_whole_test": 1.0, "final_whole_test": 1.0, "fix": 5.5}
    now = START + dt.timedelta(minutes=50)
    assert budget.fix_time_left(START, 60, r, now) == pytest.approx(8.0)
