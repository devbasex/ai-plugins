"""修正の前進・停滞・振動の判定（#2457 決定 4・不変条件 I6）。**純粋な処理だけを置く。**

修正 1 回ごとの落ちたテストの集合 F(n)（無関係な失敗とフレーキーを除いた ID の集合）を、同じ項目の履歴と比べる。

| 判定 | 条件 |
| --- | --- |
| `passed` | F(n) が空 |
| `stalled` | 直近 k 回（k は `limits.json` の `cross-refactoring.fix-progress` の `same_cause`。既定 2）の集合がすべて同じ |
| `oscillating` | F(n) が F(0)〜F(n−2) のどれかと等しい |
| `progress` | それ以外（件数が減った・別のテストへ移った）。回数の上限なく修正を続ける |
"""

from __future__ import annotations

from typing import Iterable, Optional

import limits as limit_decl

PASSED = "passed"
PROGRESS = "progress"
STALLED = "stalled"
OSCILLATING = "oscillating"
FIRST = "first"
STOPPING = (STALLED, OSCILLATING)
WHOLE = "<whole>"  # ID をどこからも取れないときの 1 要素（決定 12）


def same_sets() -> int:
    """停滞とみなす、同じ集合が続く回数。"""
    return int(limit_decl.value("cross-refactoring.fix-progress", "same_cause"))


def as_set(ids: Optional[Iterable[str]]) -> frozenset:
    return frozenset(str(i) for i in (ids or []))


def compare(history: list[Iterable[str]], same: Optional[int] = None) -> str:
    """集合の履歴（古い順。最後が F(n)）から判定を返す。履歴が 1 件なら `first`（空なら `passed`）。"""
    sets = [as_set(h) for h in history]
    if not sets:
        return FIRST
    last = sets[-1]
    if not last:
        return PASSED
    if len(sets) == 1:
        return FIRST
    k = max(2, int(same if same is not None else same_sets()))
    if len(sets) >= k and all(s == last for s in sets[-k:]):
        return STALLED
    if last == sets[-2]:
        # k > 2 のとき、2 回続いただけでは停滞にしない（前進でもない）。同じ修正を繰り返す前に 1 度だけ様子を見る
        return PROGRESS
    if any(s == last for s in sets[:-2]):
        return OSCILLATING
    return PROGRESS
