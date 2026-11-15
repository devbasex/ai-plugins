"""cross-refactoring の配分の値（1 件あたりの分）を、初期値のファイルと宣言から決める。純粋な関数だけを置く。

- 初期値は `skills/cross-refactoring/data/allocation-defaults.json` の `values`（種類ごとの `minutes`）で、`initial` が真
- 宣言 `.ndf/estimate.json` の `allocation_minutes` は種類ごとの分で、書いた種類だけ初期値より先に効く
- 初期値に無い種類を宣言に書いたとき・分が 0 以下のときは止める（黙って捨てない）
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

DECLARED_KEY = "allocation_minutes"


class AllocationError(ValueError):
    """初期値のファイルか宣言の形が違う。"""


def _positive(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise AllocationError(f"{where} が 0 より大きい数でない: {value!r}")
    return float(value)


def decide(defaults: Mapping[str, Any], declared: Optional[Mapping[str, Any]]) -> dict[str, float]:
    """初期値の `values` に宣言の `allocation_minutes` を種類ごとに重ねた分を返す。"""
    if defaults.get("initial") is not True:
        raise AllocationError("配分の初期値のファイルに initial: true が無い")
    values = defaults.get("values")
    if not isinstance(values, Mapping) or not values:
        raise AllocationError("配分の初期値のファイルに values が無い")
    out = {kind: _positive(row.get("minutes") if isinstance(row, Mapping) else None, f"初期値の {kind}") for kind, row in values.items()}
    override = declared.get(DECLARED_KEY) if declared is not None else None
    if override is None:
        return out
    if not isinstance(override, Mapping):
        raise AllocationError(f"宣言の {DECLARED_KEY} が object でない")
    unknown = sorted(set(override) - set(out))
    if unknown:
        raise AllocationError(f"宣言の {DECLARED_KEY} に初期値に無い種類がある: {', '.join(unknown)}（使える種類: {', '.join(sorted(out))}）")
    for kind, minutes in override.items():
        out[kind] = _positive(minutes, f"宣言の {DECLARED_KEY}.{kind}")
    return out
