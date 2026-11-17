"""担当の起動の費用（#3747）。

- 費用は担当の CLI が出力で報告した USD を使う。claude は `--output-format json` の外側の JSON の `total_cost_usd` を報告する
- 費用を報告しない CLI（codex・kiro）と、報告の無い出力（途中で切れた・JSON でない）は不明として数え、0 と書かない
- 状態の `phase_cost` は工程ごとに、報告された費用の合計（`usd`）・報告のあった起動の数（`known`）・不明だった起動の数（`unknown`）を持つ
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Optional

from ndf.domains.crossrefactor.allocation_decide import is_number

# claude の `--output-format json` の外側の JSON で費用（USD）を持つ鍵
REPORTED = "total_cost_usd"


def reported(output: str) -> Optional[float]:
    """出力が報告した費用（USD）。報告が無ければ None（不明）。"""
    try:
        payload = json.loads(output)
    except ValueError:  # noqa: NDF401 外部コマンド: 担当の出力が JSON でなければ費用の報告が無いので不明（None）にする
        return None
    spent = payload.get(REPORTED) if isinstance(payload, dict) else None
    if not is_number(spent):
        return None
    return float(spent)


def add(phase_cost: Mapping[str, Any], phase: str, cost: Optional[float]) -> dict[str, Any]:
    """工程 `phase` の起動 1 回の費用を足した `phase_cost`。`cost` が None なら不明の数を 1 つ増やす。"""
    out = {k: dict(v) for k, v in phase_cost.items()}
    entry = out.setdefault(phase, {"usd": 0.0, "known": 0, "unknown": 0})
    if cost is None:
        entry["unknown"] += 1
    else:
        entry["usd"] = round(entry["usd"] + cost, 6)
        entry["known"] += 1
    return out


def totals(phase_cost: Mapping[str, Any]) -> dict[str, Any]:
    """全工程の費用。`cost` は報告された費用の合計で、報告が 1 つも無ければ None（不明）。`cost_unknown` は不明だった起動の数。"""
    known = sum(int(v["known"]) for v in phase_cost.values())
    unknown = sum(int(v["unknown"]) for v in phase_cost.values())
    usd = round(sum(float(v["usd"]) for v in phase_cost.values()), 6)
    return {"cost": usd if known else None, "cost_unknown": unknown}
