"""終了コードの区分（事実の表 `contracts/facts/exit-codes.json`）の読み手。

区分の 1 行は status と終了コードの範囲（`code` から `last` まで。`last` が無ければ `code` だけ）を持つ。
区分の読み方はここだけが持ち、ほかの読み手（`causes`・`result`・`contracts/cli`）はここを通して引く。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ndf.contracts import facts

TABLE = "exit-codes"
# 結果の status の語彙（result.schema.json の enum とこの表の status 列と同じ）
OK, GATE, STOPPED = "ok", "gate", "stopped"
STATUSES = (OK, GATE, STOPPED)


def bounds(row: Mapping[str, Any]) -> tuple[int, int]:
    """区分の 1 行の終了コードの範囲（始点と終点。last が無ければ code だけ）。"""
    return row["code"], row.get("last", row["code"])


def ranges(status: str) -> tuple[tuple[int, int], ...]:
    """`status` の終了コードの範囲（始点と終点）の並び。"""
    return tuple(bounds(r) for r in facts.rows(TABLE, status=status))


def codes(status: str) -> frozenset[int]:
    """`status` の結果が返してよい終了コード。"""
    return frozenset(c for first, last in ranges(status) for c in range(first, last + 1))


def matches(status: str, code: int) -> bool:
    """status と終了コードが区分で食い違わないか（範囲の端で比べる）。"""
    return any(first <= code <= last for first, last in ranges(status))


def first_code(status: str) -> int:
    """`status` の最初の区分の始点。"""
    return facts.rows(TABLE, status=status)[0]["code"]


def code_of(id: str) -> int:
    """区分 `id` の終了コード。"""
    return int(facts.row(TABLE, id)["code"])
