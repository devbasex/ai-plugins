"""#2944: 出現率の計算（純粋な関数）。重み付きの平均・打ち切り・出現率・判断できる件数を、作った単位の値で確かめる。"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

import pytest

from ndf.adapters.model_choice import Where
from ndf.domains.records import allocation_decide as ad
from ndf.domains.records import experiments as ex
from ndf.domains.records.experiments import UnitValue

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
ALLOCATION = {"floor": 0.05, "min_units": 10, "margin": 0.1, "window_days": 60}
EVEN = (1 / 3, 1 / 3, 1 / 3)
WHERE = Where("/r", "impl", "claude", "k")


def _units(arm: int, n: int, quality: float, cost: float | None = 1.0, probs=EVEN, *, start: int = 0, prefix: str = "") -> list[UnitValue]:
    """組 `arm` の単位を `n` 件。割り付けの日時は 1 時間ずつ古くなる。"""
    return [
        UnitValue(f"{prefix}{arm}-{start + i:03d}", arm, tuple(probs), NOW - timedelta(hours=start + i), quality, cost, None)
        for i in range(n)
    ]


def _mixed(arm: int, n: int, share: float, cost: float = 1.0) -> list[UnitValue]:
    """効果の平均が `share` になる 0 と 1 の単位を `n` 件。"""
    ones = round(n * share)
    return _units(arm, ones, 1.0, cost) + _units(arm, n - ones, 0.0, cost, start=ones)


# --- 重み付きの平均 ---


def test_weighted_uses_the_inverse_of_the_probability_and_the_effective_count():
    units = [UnitValue("a", 0, (0.5, 0.5), NOW, 1.0, 2.0, None), UnitValue("b", 0, (0.25, 0.75), NOW, 0.0, 8.0, None)]
    stats = ad.weighted(units)
    # 重みは 2 と 4
    assert stats.units == 2 and stats.quality == pytest.approx(2 / 6)
    assert stats.quality_n_eff == pytest.approx(36 / 20)
    assert stats.cost == pytest.approx((2 * 2 + 4 * 8) / 6)
    assert stats.log_cost == pytest.approx((2 * math.log(2) + 4 * math.log(8)) / 6)


def test_weighted_leaves_unknown_costs_out_of_the_cost_but_keeps_the_effect():
    units = _units(0, 3, 1.0, None) + _units(0, 2, 0.0, 3.0, start=3)
    stats = ad.weighted(units)
    assert (stats.units, stats.cost_units) == (5, 2)
    assert stats.quality == pytest.approx(3 / 5) and stats.cost == pytest.approx(3.0)
    assert stats.log_cost_sd == pytest.approx(0.0)


def test_weighted_of_nothing_has_no_cost():
    stats = ad.weighted([])
    assert (stats.units, stats.cost_units, stats.cost, stats.log_cost) == (0, 0, None, None)


# --- 出現率 ---


def test_burn_in_until_every_candidate_has_min_units_with_an_effect():
    units = _mixed(0, 10, 0.8) + _mixed(1, 10, 0.8) + _mixed(2, 9, 0.8)
    entry = ad.probabilities(units, [0, 1, 2], ALLOCATION, random.Random(1))
    assert entry.phase == "burn_in" and entry.probs is None and entry.counts == (10, 10, 9)


def test_burn_in_while_a_candidate_has_fewer_than_two_units_of_known_cost():
    units = _mixed(0, 10, 0.8) + _units(1, 9, 0.8, None) + _units(1, 1, 0.8, 1.0, start=9) + _mixed(2, 10, 0.8)
    assert ad.probabilities(units, [0, 1, 2], ALLOCATION, random.Random(1)).phase == "burn_in"


def test_adaptive_probabilities_sum_to_one_with_the_floor_and_favour_the_cheaper_equal_arm():
    units = _mixed(0, 40, 0.8, 4.0) + _mixed(1, 40, 0.8, 1.0) + _mixed(2, 40, 0.8, 4.0)
    entry = ad.probabilities(units, [0, 1, 2], ALLOCATION, random.Random(7))
    assert entry.phase == "adaptive" and entry.probs is not None
    assert sum(entry.probs) == pytest.approx(1.0) and min(entry.probs) >= ALLOCATION["floor"]
    assert entry.probs[1] > 0.8 and entry.counts == (40, 40, 40)


def test_an_arm_that_is_not_a_candidate_gets_zero_and_units_drawn_without_it_are_not_compared():
    units = _mixed(0, 20, 0.8) + _mixed(1, 20, 0.8)
    # 組 2 が候補でなかった時期の単位（確率 0）は、組 2 を比べないので使える。候補に組 2 を入れると比べられない
    lean = [UnitValue(u.assign_id, u.arm, (0.5, 0.5, 0.0), u.at, u.quality, u.cost, None) for u in units]
    entry = ad.probabilities(lean, [0, 1], ALLOCATION, random.Random(3))
    assert entry.phase == "adaptive" and entry.probs is not None and entry.probs[2] == 0
    assert sum(entry.probs) == pytest.approx(1.0)
    assert ad.probabilities(lean + _mixed(2, 20, 0.8), [0, 1, 2], ALLOCATION, random.Random(3)).counts == (0, 0, 20)


def test_a_cheap_arm_well_below_the_base_stays_at_the_floor():
    # 条件 18: 組 1 は 1/100 の費用でも、効果が基準より margin を超えて低いので「最良」を数えない
    units = _mixed(0, 60, 0.9, 1.0) + _mixed(1, 60, 0.5, 0.01) + _mixed(2, 60, 0.9, 1.0)
    entry = ad.probabilities(units, [0, 1, 2], ALLOCATION, random.Random(11))
    assert entry.probs is not None and entry.probs[1] == pytest.approx(ALLOCATION["floor"])
    assert entry.best is not None and entry.best[1] == 0


def test_the_same_units_give_the_same_probabilities_and_the_seed_ignores_their_order():
    units = _mixed(0, 30, 0.7, 2.0) + _mixed(1, 30, 0.8, 1.5) + _mixed(2, 30, 0.6, 1.0)
    first = ad.probabilities(units, [0, 1, 2], ALLOCATION, random.Random(ex.seed_of(WHERE, ALLOCATION, units)))
    again = ad.probabilities(units, [0, 1, 2], ALLOCATION, random.Random(ex.seed_of(WHERE, ALLOCATION, list(reversed(units)))))
    assert first == again
    assert ex.seed_of(WHERE, ALLOCATION, units) != ex.seed_of(WHERE, {**ALLOCATION, "margin": 0.2}, units)


# --- 打ち切り ---


def test_an_arm_well_below_the_base_is_cut_off_until_the_older_thirtieth_newest_plus_the_window():
    units = _mixed(0, 30, 0.9) + _units(1, 30, 0.1, start=5) + _mixed(2, 30, 0.9)
    cut = ad.eliminated(units, [0, 1, 2], 0.1, 60, random.Random(5))
    # 新しいほうから 30 件目: 基準は 29 時間前、組 1 は 34 時間前。古いほうに 60 日を足す
    assert cut == {1: NOW - timedelta(hours=34) + timedelta(days=60)}


def test_no_cutoff_below_thirty_units_and_the_base_is_never_cut():
    assert ad.eliminated(_mixed(0, 30, 0.9) + _units(1, 29, 0.0), [0, 1], 0.1, 60, random.Random(5)) == {}
    assert ad.eliminated(_units(0, 40, 0.0) + _units(1, 40, 1.0), [0, 1], 0.1, 60, random.Random(5)) == {}


def test_cutoff_uses_only_units_drawn_while_both_arms_had_a_chance():
    early = [UnitValue(u.assign_id, u.arm, (0.5, 0.0, 0.5), u.at, u.quality, u.cost, None) for u in _units(0, 30, 1.0, prefix="e")]
    units = early + _units(1, 30, 0.0) + _units(0, 29, 1.0)
    assert ad.eliminated(units, [0, 1], 0.1, 60, random.Random(5)) == {}


# --- 判断できる件数 ---


def test_judgement_without_units_drawn_while_all_were_candidates_is_incomparable():
    lean = [UnitValue(u.assign_id, u.arm, (0.5, 0.5, 0.0), u.at, u.quality, u.cost, None) for u in _mixed(0, 40, 0.8)]
    assert ad.judgement(lean, [0, 1, 2], ALLOCATION, random.Random(1)).verdict is ad.Verdict.INCOMPARABLE


def test_judgement_needs_thirty_units_per_arm():
    units = _mixed(0, 30, 0.8) + _mixed(1, 29, 0.8)
    found = ad.judgement(units, [0, 1], ALLOCATION, random.Random(1))
    assert found.verdict is ad.Verdict.SHORT and found.counts == (30, 29, 0) and found.best is None


def test_judgement_names_a_clear_leader_at_thirty_units():
    units = _mixed(0, 30, 0.8, 10.0) + _mixed(1, 30, 0.8, 1.0)
    found = ad.judgement(units, [0, 1], ALLOCATION, random.Random(1))
    assert found.verdict is ad.Verdict.DECIDED and found.leader == 1 and found.best is not None and found.best[1] >= 0.95


def test_judgement_of_close_arms_is_undecided():
    units = _mixed(0, 30, 0.8, 1.0) + _mixed(1, 30, 0.8, 1.0)
    found = ad.judgement(units, [0, 1], ALLOCATION, random.Random(1))
    assert found.verdict is ad.Verdict.UNDECIDED and found.leader is None
