"""出現率の計算（#2944 の設計 issue-2944-design-allocation.md の「出現率の計算」「必要な件数と、判断できる件数」）。

純粋な関数で、ファイルも時刻も読まない。単位の値（`experiments.Unit`）を受け、重み付きの平均（`weighted`）、打ち切り
（`eliminated`）、段階と確率（`probabilities`）、`report` の 4 つの表示（`judgement`）を返す。

- 比べる単位は、割り付けた時に比べる組のすべての確率が 0 より大きかった単位だけ。重みは割り付けた時の確率の逆数
- 効果はベータ分布、費用は費用の対数の平均の事後分布（正規分布）から組ごとに標本を引き、効果 ÷ 費用が最大の組を数える
- 標本は呼び手が渡す乱数（`Sampler`。標準ライブラリの `random.Random` を種で作ったもの）から引く。種は呼び手
  （`experiments.seed_of`）が計算に使う単位から作るので、同じ単位の集まりからは何度計算しても同じ値になる。判断のモジュールは
  乱数の生成器を作らない（規則 decide-pure）
"""

from __future__ import annotations

import enum
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional, Protocol

# 組ごとに引く標本の数
SAMPLES = 2000
# 基準の組（添字 0）
BASE = 0
# 効果の下限: 基準より margin を超えて低い確率がこれを超える組は「最良」を数えない
LOWER_BOUND = 0.9
# 打ち切り: 基準より margin を超えて低い確率がこれを超える組は出現率を 0 にする
CUTOFF = 0.99
# 打ち切りの判定と「判断できる」に要る、組ごとの単位の数（重みを付けない）
ENOUGH_UNITS = 30
# 出現率の計算に要る、組ごとの費用の分かる単位の数（標本の標準偏差に 2 件要る）
COST_UNITS = 2
# 「判断できる」とする最良である確率
DECIDED = 0.95


class Unit(Protocol):
    """計算に使う単位 1 つ（`experiments.Unit`）: 選んだ組の添字・割り付けた時の確率・割り付けた日時・効果・費用（不明は None）。"""

    @property
    def arm(self) -> int: ...
    @property
    def probs(self) -> tuple[float, ...]: ...
    @property
    def at(self) -> datetime: ...
    @property
    def quality(self) -> float: ...
    @property
    def cost(self) -> Optional[float]: ...


class Sampler(Protocol):
    """標本を引く乱数（`random.Random` の 2 つのメソッド）。"""

    def betavariate(self, alpha: float, beta: float) -> float: ...
    def gauss(self, mu: float, sigma: float) -> float: ...


@dataclass(frozen=True)
class Stats:
    """組 1 つの重み付きの値。`units` と `cost_units` は重みを付けない数。費用は費用の分かる単位だけから出し、無ければ None。"""

    units: int
    quality: float
    quality_n_eff: float
    cost_units: int
    cost: Optional[float]
    log_cost: Optional[float]
    log_cost_sd: Optional[float]
    cost_n_eff: float


@dataclass(frozen=True)
class Entry:
    """出現率の計算の結果。`burn_in` は `probs` と `best` を持たない。`counts` は組ごとの比べた単位の数（候補でない組は 0）。"""

    phase: str
    probs: Optional[tuple[float, ...]]
    best: Optional[tuple[float, ...]]
    counts: tuple[int, ...]


class Verdict(enum.StrEnum):
    """`report` の優劣の表示（設計の「必要な件数と、判断できる件数」の 4 つ）。"""

    INCOMPARABLE = "incomparable"  # 比べる組がそろって候補だった単位が無い
    SHORT = "short"  # 30 件未満の組がある
    DECIDED = "decided"  # 最良である確率が 0.95 以上の組がある
    UNDECIDED = "undecided"  # 0.95 以上の組が無い


@dataclass(frozen=True)
class Judgement:
    """優劣の表示と、組ごとの比べた単位の数・最良である確率（30 件以上で費用が分かるときだけ）・最良の組（`DECIDED` のときだけ）。"""

    verdict: Verdict
    counts: tuple[int, ...]
    best: Optional[tuple[float, ...]] = None
    leader: Optional[int] = None


def _inverse_probability(u: Unit) -> float:
    return 1 / u.probs[u.arm]


def _n_eff(weights: Sequence[float]) -> float:
    return sum(weights) ** 2 / sum(w * w for w in weights) if weights else 0.0


def _mean(values: Sequence[float], weights: Sequence[float]) -> float:
    return sum(v * w for v, w in zip(values, weights)) / sum(weights)


def _weighted_sd(values: Sequence[float], weights: Sequence[float], mean: float) -> float:
    """重み付きの標本の標準偏差（信頼度の重みの不偏な形。重みが等しければ n − 1 で割る形と同じ）。"""
    total = sum(weights)
    spread = total - sum(w * w for w in weights) / total
    return math.sqrt(sum(w * (v - mean) ** 2 for v, w in zip(values, weights)) / spread) if spread > 0 else 0.0


def weighted(units: Sequence[Unit]) -> Stats:
    """組 1 つの単位の、重み付きの効果と費用の平均と有効な件数（`(重みの和)² ÷ 重みの 2 乗の和`）。"""
    weights = [_inverse_probability(u) for u in units]
    priced = [(math.log(u.cost), u.cost, w) for u, w in zip(units, weights) if u.cost is not None]
    quality = _mean([u.quality for u in units], weights) if units else 0.0
    if not priced:
        return Stats(len(units), quality, _n_eff(weights), 0, None, None, None, 0.0)
    logs, costs, cost_weights = (list(x) for x in zip(*priced))
    log_mean = _mean(logs, cost_weights)
    return Stats(
        len(units),
        quality,
        _n_eff(weights),
        len(priced),
        _mean(costs, cost_weights),
        log_mean,
        _weighted_sd(logs, cost_weights, log_mean),
        _n_eff(cost_weights),
    )


def comparable(units: Sequence[Unit], arms: Sequence[int]) -> list[Unit]:
    """割り付けた時に、`arms` のすべての確率が 0 より大きかった単位。"""
    return [u for u in units if all(u.probs[a] > 0 for a in arms)]


def _by_arm(units: Sequence[Unit], arms: Sequence[int]) -> dict[int, Stats]:
    return {a: weighted([u for u in units if u.arm == a]) for a in arms}


def _counts(stats: Mapping[int, Stats], width: int) -> tuple[int, ...]:
    return tuple(stats[i].units if i in stats else 0 for i in range(width))


def _per_arm(values: Mapping[int, float], width: int) -> tuple[float, ...]:
    """組の添字 → 値を、組の数の並びにする（値の無い組は 0）。"""
    return tuple(values[i] if i in values else 0.0 for i in range(width))


def _quality_draws(s: Stats, rng: Sampler) -> list[float]:
    n = s.quality_n_eff
    return [rng.betavariate(1 + n * s.quality, 1 + n * (1 - s.quality)) for _ in range(SAMPLES)]


def _cost_draws(s: Stats, rng: Sampler) -> list[float]:
    """費用の標本。費用の分かる単位が 2 件以上ある組（`log_cost` と `log_cost_sd` を持つ）だけを呼ぶ（`_enough`）。"""
    if s.log_cost is None or s.log_cost_sd is None:
        raise ValueError("費用の分かる単位の無い組から費用の標本を引いた")
    spread = s.log_cost_sd / math.sqrt(s.cost_n_eff)
    return [math.exp(rng.gauss(s.log_cost, spread)) for _ in range(SAMPLES)]


def _below(arm: Sequence[float], base: Sequence[float], margin: float) -> float:
    """標本のうち、組の効果が基準より `margin` を超えて低い割合。"""
    return sum(q < b - margin for q, b in zip(arm, base)) / SAMPLES


def _best(stats: Mapping[int, Stats], margin: float, rng: Sampler) -> dict[int, float]:
    """組ごとの「最良である確率」。効果の下限を外れた組（基準より `margin` を超えて低い確率が 0.9 を超える）は数えず 0。"""
    arms = sorted(stats)
    quality = {a: _quality_draws(stats[a], rng) for a in arms}
    cost = {a: _cost_draws(stats[a], rng) for a in arms}
    counted = [a for a in arms if a == BASE or _below(quality[a], quality[BASE], margin) <= LOWER_BOUND]
    wins = dict.fromkeys(arms, 0)
    for s in range(SAMPLES):
        wins[max(counted, key=lambda a: quality[a][s] / cost[a][s])] += 1
    return {a: wins[a] / SAMPLES for a in arms}


def _enough(stats: Mapping[int, Stats], units: int) -> bool:
    return all(s.units >= units and s.cost_units >= COST_UNITS for s in stats.values())


def probabilities(units: Sequence[Unit], candidates: Sequence[int], allocation: Mapping[str, Any], rng: Sampler) -> Entry:
    """段階と確率。候補のどれかで効果のある単位が `min_units` に満たないか、費用の分かる単位が 2 件に満たなければ `burn_in`。

    満たせば、候補の組の出現率は `floor + (1 − floor × 候補の数) × 最良である確率`、候補でない組は 0（`adaptive`）。
    """
    cands = sorted(candidates)
    used = comparable(units, cands)
    stats = _by_arm(used, cands)
    width = len(units[0].probs) if units else max(cands) + 1
    counts = _counts(stats, width)
    if not _enough(stats, allocation["min_units"]):
        return Entry("burn_in", None, None, counts)
    best = _best(stats, allocation["margin"], rng)
    floor = allocation["floor"]
    probs = tuple(floor + (1 - floor * len(cands)) * best[i] if i in best else 0.0 for i in range(width))
    return Entry("adaptive", probs, _per_arm(best, width), counts)


def _thirtieth(units: Sequence[Unit]) -> datetime:
    """新しいほうから 30 件目の割り付けの日時。"""
    return sorted((u.at for u in units), reverse=True)[ENOUGH_UNITS - 1]


def eliminated(units: Sequence[Unit], arms: Sequence[int], margin: float, window_days: int, rng: Sampler) -> dict[int, datetime]:
    """打ち切る組 → 期限。基準でない組を 1 つずつ、両方の確率が 0 より大きかった時期の単位で基準と比べ、どちらも 30 件以上で、
    基準より `margin` を超えて低い確率が 0.99 を超える組を打ち切る。期限は、両方の新しいほうから 30 件目の割り付けの日時の古いほうに
    `window_days` を足した日時。基準は打ち切らない。"""
    out: dict[int, datetime] = {}
    for arm in sorted(a for a in arms if a != BASE):
        pair = comparable(units, [BASE, arm])
        base, other = [u for u in pair if u.arm == BASE], [u for u in pair if u.arm == arm]
        if len(base) < ENOUGH_UNITS or len(other) < ENOUGH_UNITS:
            continue
        below = _below(_quality_draws(weighted(other), rng), _quality_draws(weighted(base), rng), margin)
        if below > CUTOFF:
            out[arm] = min(_thirtieth(base), _thirtieth(other)) + timedelta(days=window_days)
    return out


def judgement(units: Sequence[Unit], candidates: Sequence[int], allocation: Mapping[str, Any], rng: Sampler) -> Judgement:
    """`report` の優劣の表示。比べる組がそろって候補だった単位が無ければ比較できない、30 件未満の組があれば件数が足りない、
    最良である確率（`probabilities` と同じ効果の下限を当てる）が 0.95 以上の組があれば判断できる、無ければ優劣を判定できない。"""
    cands = sorted(candidates)
    used = comparable(units, cands)
    stats = _by_arm(used, cands)
    counts = _counts(stats, len(units[0].probs) if units else max(cands) + 1)
    if not used:
        return Judgement(Verdict.INCOMPARABLE, counts)
    if any(s.units < ENOUGH_UNITS for s in stats.values()):
        return Judgement(Verdict.SHORT, counts)
    if not _enough(stats, ENOUGH_UNITS):
        return Judgement(Verdict.UNDECIDED, counts)  # 費用の分かる単位が足りず、効果 ÷ 費用を比べられない
    best = _best(stats, allocation["margin"], rng)
    shown = _per_arm(best, len(counts))
    leader = max(best, key=lambda a: best[a])
    if best[leader] >= DECIDED:
        return Judgement(Verdict.DECIDED, counts, shown, leader)
    return Judgement(Verdict.UNDECIDED, counts, shown)
