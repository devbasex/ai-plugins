"""範囲テストのテンプレートの試走の上限（#2158）。

着手前のテストとテンプレートの試走は同じ上限（`--timeout`）を分け合う。着手前のテストが上限の近くで通ると、残りの秒が数秒になり
試走が必ず上限に当たって init が止まる。試走の上限は、残りの秒と下限（上限の宣言 `PROBE_POLICY` の秒）の大きい方にする。
"""

from __future__ import annotations

from ndf.contracts import limits

PROBE_POLICY = "cross-refactoring.template-probe"


def probe_budget(timeout: float, baseline_seconds: float, floor: float) -> float:
    """テンプレートの試走の上限（秒）。返す値は `floor` 以上。"""
    return max(timeout - baseline_seconds, floor)


def probe_floor() -> float:
    """試走の上限の下限（上限の宣言から読む）。"""
    return limits.seconds(PROBE_POLICY)
