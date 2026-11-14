"""見直し（review）・大整理（overhaul）を起動するかの判定（純粋）。

| 深さ | 条件 | 起動する |
| --- | --- | --- |
| 両方 | `manual` | 利用者が求めた |
| 両方 | `timed_out` | その深さの最後の実行を秒の上限で打ち切り、残りがある |
| review | `carry` | 前回の候補の収集の持ち越しの数（`metrics.carry`）が 候補の上限 × `carry_factor` を超えた |
| overhaul | `count` | 最後の大整理の後に見直しを `every` 回以上走らせた |

持ち越しの数は見積の欠け（`unscored`）だけで入った課題を数えない（`selection_decide`）。見積の欠けは採点で解け、
持ち越しで次の見直しがすぐに起動しない（#2540）。条件の値は `.ndf/backlog.json` の `depths.<深さ>` で上書きできる。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional

from ndf.domains.backlog.model import OVERHAUL, RAN, REVIEW, TIMED_OUT

DEFAULTS: dict[str, dict[str, int]] = {REVIEW: {"carry_factor": 10}, OVERHAUL: {"every": 4}}


def settings(depth: str, declared: Mapping[str, Any]) -> dict[str, int]:
    """既定に宣言（`depths.<深さ>` のうち既定にある鍵）を重ねた条件の値。正の整数でなければ `ValueError`。"""
    out = dict(DEFAULTS[depth])
    for key in out:
        if key in declared:
            v = declared[key]
            if type(v) is not int or v <= 0:
                raise ValueError(f"depths.{depth}.{key} は正の整数で書く")
            out[key] = v
    return out


def _last(rows: Sequence[Mapping[str, Any]], depth: str) -> Optional[Mapping[str, Any]]:
    return next((r for r in reversed(rows) if r.get("depth") == depth), None)


def ran_since(rows: Sequence[Mapping[str, Any]], depth: str, since_depth: str) -> int:
    """最後の `since_depth` の実行より後に `depth` を走らせた回数（打ち切りも 1 回に数える）。"""
    last = max((k for k, r in enumerate(rows) if r.get("depth") == since_depth and r.get("event") in (RAN, TIMED_OUT)), default=-1)
    return sum(1 for r in rows[last + 1 :] if r.get("depth") == depth and r.get("event") in (RAN, TIMED_OUT))


def due(depth: str, rows: Sequence[Mapping[str, Any]], candidates: Mapping[str, Any], cfg: Mapping[str, int],
        manual: bool) -> tuple[list[str], dict[str, Any]]:  # fmt: skip
    """(起動する条件, 判定に使った値)。条件が空なら起動しない。`candidates` は前回の候補の収集の metrics。"""
    trig = ["manual"] if manual else []
    values: dict[str, Any] = {}
    last = _last(rows, depth)
    if last is not None and last.get("event") == TIMED_OUT and last.get("left"):
        trig.append(TIMED_OUT)
        values["left"] = list(last["left"])
    if depth == REVIEW:
        carry, limit = candidates.get("carry"), candidates.get("limit")
        if isinstance(carry, int) and isinstance(limit, int):
            values.update({"carry": carry, "carry_over": limit * cfg["carry_factor"]})
            if carry > limit * cfg["carry_factor"]:
                trig.append("carry")
    else:
        values["count"] = ran_since(rows, REVIEW, OVERHAUL)
        if values["count"] >= cfg["every"]:
            trig.append("count")
    return trig, values
