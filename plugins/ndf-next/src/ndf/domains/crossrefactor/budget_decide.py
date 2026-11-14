"""実装の予算で採る項目を選ぶ判定と、安全網（コーディングの CLI の累計の上限）の判定。純粋な関数だけを置く。

- 予算は選定にだけ使う。見積りはテストの追加と実装の和（分）で、テストの実行・CI の待ち・経過時間を入れない
- 計画の担当が項目ごとにグレード（`tier`: high・medium・low）と見積り（`estimate`: {"test", "implement"} の分）を返す。
  見積りの無い項目は予算に載せられないため、採らずに見送る
- 順位は（グレード, 提案した担当の数）の降順で、同じなら見積りの昇順。順にたどり、入る項目は入れ、入らない項目は飛ばして
  次を見る（大きな項目 1 件で後ろの小さな項目が入らず予算が余ることを避ける）
- グレード high の見積りの和 S が予算 B を超え、宣言 `cross-refactoring.budget-extension` の倍率 × B 以下なら、実効の予算を
  ceil(S) 分へ延ばす
- 安全網は、コードを書く工程（表 `loop-phases` の coding が真の行）の起動ごとの秒の累計に置く上限で、実効の予算 × 宣言
  `cross-refactoring.safety-net` の倍率（分）。当たったら項目を取り消さずに止めて人へ渡す
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from ndf.contracts import limits
from ndf.domains.crossrefactor import launch_decide

EXTENSION_POLICY = "cross-refactoring.budget-extension"
SAFETY_NET_POLICY = "cross-refactoring.safety-net"
DEFAULT_POLICY = "cross-refactoring.default-budget"
TIER_ORDER = {"high": 3, "medium": 2, "low": 1}
ESTIMATE_PARTS = ("test", "implement")
NO_ESTIMATE = "見積りが無いため予算に載せられない"
OVER_BUDGET = "実装の予算に入らない"


def default_minutes() -> int:
    """実装の予算の既定（分）。"""
    return int(limits.seconds(DEFAULT_POLICY) // 60)


def estimate_of(item: Mapping[str, Any]) -> Optional[float]:
    """項目の見積りの和（分）。形が違えば None。"""
    est = item.get("estimate")
    if not isinstance(est, Mapping):
        return None
    total = 0.0
    for part in ESTIMATE_PARTS:
        value = est.get(part)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return None
        total += float(value)
    return total


def _tier(item: Mapping[str, Any]) -> int:
    tier = item.get("tier")
    return TIER_ORDER[tier] if isinstance(tier, str) and tier in TIER_ORDER else 0


def _rank_key(entry: tuple[float, Mapping[str, Any]]) -> tuple[int, int, float]:
    cost, item = entry
    by = item.get("by")
    return (-_tier(item), -(len(by) if isinstance(by, list) else 0), cost)


@dataclass(frozen=True)
class Budget:
    """予算（分）。`extended_to` は延ばした後の分（延ばしていなければ None）。"""

    minutes: int
    extended_to: Optional[int] = None
    reason: str = ""

    @property
    def effective(self) -> int:
        return self.extended_to if self.extended_to is not None else self.minutes

    def as_json(self) -> dict[str, Any]:
        return {"minutes": self.minutes, "extended_to": self.extended_to, "reason": self.reason}


def extend(items: Iterable[Mapping[str, Any]], minutes: int, factor: Optional[float] = None) -> Budget:
    """グレード high の見積りの和で予算を延ばすか決める。"""
    f = float(limits.value(EXTENSION_POLICY, "factor")) if factor is None else factor
    s = round(sum(c for c in (estimate_of(i) for i in items if i.get("tier") == "high") if c is not None), 6)
    if s <= minutes + 1e-9 or s > f * minutes + 1e-9:
        return Budget(minutes)
    return Budget(minutes, int(math.ceil(s)), f"high の項目の見積りの和 {s:.1f} 分が予算 {minutes} 分を超えた")


@dataclass(frozen=True)
class Selection:
    adopted: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    budget: Budget


def select(items: Sequence[Mapping[str, Any]], minutes: int, factor: Optional[float] = None) -> Selection:
    """予算に入る項目を順位の順で選ぶ。入らない項目と見積りの無い項目は理由を付けて `skipped` へ置く。"""
    costs = [(estimate_of(i), i) for i in items]
    estimated = [(c, dict(i)) for c, i in costs if c is not None]
    skipped = [{**i, "reason": NO_ESTIMATE} for c, i in costs if c is None]
    budget = extend([i for _, i in estimated], minutes, factor)
    left = float(budget.effective)
    adopted: list[dict[str, Any]] = []
    for cost, item in sorted(estimated, key=_rank_key):
        if cost <= left + 1e-9:
            adopted.append(item)
            left -= cost
        else:
            skipped.append({**item, "reason": f"{OVER_BUDGET}（見積り {cost:.0f} 分・残り {left:.0f} 分）"})
    return Selection(adopted, skipped, budget)


@dataclass(frozen=True)
class SafetyNet:
    """安全網。`limit_minutes` は上限、`used_seconds` はコーディングの工程の起動の秒の累計。"""

    limit_minutes: float
    used_seconds: float = 0.0

    @classmethod
    def of(cls, effective_minutes: int, explicit: Optional[float] = None, used: float = 0.0) -> SafetyNet:
        limit = float(explicit) if explicit is not None else effective_minutes * float(limits.value(SAFETY_NET_POLICY, "factor"))
        return cls(limit, used)

    @property
    def remaining_seconds(self) -> float:
        return self.limit_minutes * 60 - self.used_seconds

    @property
    def halted(self) -> bool:
        return self.remaining_seconds <= 0

    def add(self, phase: str, seconds: float) -> SafetyNet:
        """起動 1 回の秒を足す。コーディングの工程でなければ数えない（提案・計画・判定）。"""
        if phase not in launch_decide.coding_phases():
            return self
        return SafetyNet(self.limit_minutes, round(self.used_seconds + max(0.0, seconds), 1))

    def reason(self) -> str:
        return (
            f"安全網の上限 {self.limit_minutes:.0f} 分に当たった（コーディングの CLI の累計 {self.used_seconds / 60:.1f} 分）。"
            "項目は取り消していない。続けるなら --safety-net-minutes を大きくして同じコマンドを打ち直す"
        )
