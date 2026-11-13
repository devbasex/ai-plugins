"""上限の宣言（`contracts/limits.json`）の読み手。

時間・回数・間隔・量の上限は宣言だけが値を持ち、コードは方針の名前で引く（値を写さない）。
形は `contracts/schemas/limits.schema.json` にある。
"""

from __future__ import annotations

from typing import NamedTuple, Optional

from ndf import contracts

DECLARATION = "limits.json"
SCHEMA = contracts.schema_path("limits")


class LimitsError(contracts.ContractError):
    """宣言が無い・形が違う・引いた方針や値が無い。"""


class Policy(NamedTuple):
    """宣言の 1 行。方針に無い値は None。"""

    name: str
    summary: str
    seconds: Optional[float] = None
    interval: Optional[float] = None
    factor: Optional[float] = None
    max_interval: Optional[float] = None
    per_call: Optional[float] = None
    attempts: Optional[int] = None
    same_cause: Optional[int] = None
    count: Optional[int] = None


# 方針の値の名前（名前と説明を除いた Policy の項目）
VALUE_KEYS = tuple(f for f in Policy._fields if f not in ("name", "summary"))


def validate(raw: object) -> dict[str, Policy]:
    """宣言の中身を確かめて方針の表にする。形の誤りは `LimitsError`。"""
    contracts.require_schema(raw, SCHEMA, LimitsError, f"上限の宣言 {DECLARATION}")
    return {name: Policy(name=name, **row) for name, row in raw["policies"].items()}  # type: ignore[index]


@contracts.cached_reader
def table() -> dict[str, Policy]:
    """宣言を 1 プロセスに 1 回読む。"""
    return validate(contracts.load_as(DECLARATION, LimitsError))


def policy(name: str) -> Policy:
    """方針の名前で 1 行を引く。無ければ `LimitsError`。"""
    found = table().get(name)
    if found is None:
        raise LimitsError(f"上限の宣言に方針 {name} が無い")
    return found


def value(name: str, key: str) -> float:
    """方針の 1 つの値。方針にその値が無ければ `LimitsError`。"""
    got = getattr(policy(name), key) if key in VALUE_KEYS else None
    if got is None:
        raise LimitsError(f"上限の宣言の方針 {name} に {key} が無い")
    return got


def seconds(name: str) -> float:
    """方針の上限の秒。"""
    return value(name, "seconds")


def scaled(name: str, measured: Optional[float]) -> float:
    """実測から決める上限 = max(seconds, factor × 実測)。実測が無ければ seconds。

    seconds は下限で、実測の記録が少なく短くても上限はこれを下回らない。
    """
    floor = seconds(name)
    if measured is None:
        return floor
    return max(floor, value(name, "factor") * float(measured))
