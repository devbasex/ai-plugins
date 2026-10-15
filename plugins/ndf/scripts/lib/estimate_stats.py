"""実績の集計（直近 N 件・中央値・分位・最大・Σ秒÷Σ件数）を 1 か所に持つ（#1856 の決定 4）。

見積もりの部品（`estimate_predict`）と、遅れの見張り（`slow_step`）・テストの所要（`project_lib/measure_ci`）・
cross-refactoring の配分（`refactor_lib/allocation`）が呼ぶ。窓の既定 `WINDOW` はここだけが持ち、
各系統は自分の設定を引数で渡す。純粋な関数だけで、ファイルも終了コードも持たない。
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence

WINDOW = 10


def recent(values: Sequence, window: int | None = WINDOW) -> list:
    """古い順の並びの直近 `window` 件（古い順のまま）。window が None なら全部。"""
    values = list(values)
    if window is None:
        return values
    return values[-window:] if window > 0 else []


def median(values: Iterable[float]) -> float | None:
    """中央値。空なら None。"""
    values = list(values)
    return statistics.median(values) if values else None


def quantile(values: Iterable[float], q: float) -> float | None:
    """nearest-rank の分位（小さい順で ceil(q × n) 番目）。空なら None。"""
    xs = sorted(values)
    if not xs:
        return None
    k = max(1, math.ceil(q * len(xs)))
    return xs[min(k, len(xs)) - 1]


def maximum(values: Iterable[float]) -> float | None:
    """最大。空なら None。"""
    values = list(values)
    return max(values) if values else None


def ratio_of_sums(samples: Iterable[tuple[float, float]]) -> float | None:
    """`(件数, 量)` の並びの Σ量 ÷ Σ件数。件数の和が正でなければ None。"""
    samples = list(samples)
    total = sum(c for c, _ in samples)
    if total <= 0:
        return None
    return sum(v for _, v in samples) / total
