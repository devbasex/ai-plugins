"""成立の判定（修正が停滞か振動した項目を、修正担当と別のランタイムに判定させる）。純粋な関数だけを置く。

| 場面 | 次の手 |
| --- | --- |
| 停滞・振動（判定の前） | 判定を依頼する（`judge`）。判定の担い手が居なければ項目を戻す（`revert`） |
| 判定の答えが「成立する」 | 失敗の履歴を区切り、修正をもう一巡続ける（`continue`。1 項目に 1 回） |
| 判定の答えが「成立しない」 | 項目を戻す（`revert`。理由 `infeasible`） |
| 答えが無い・形が違う | 判定できないとして項目を戻す（`revert`） |
| 停滞・振動（判定で続けた後） | 項目を戻す（`revert`。理由 `stalled_after_judge`） |

答えの形は `{"items": [{"item": "<項目の番号>", "feasible": <真偽>, "reason": "<理由>", "evidence": "<根拠>"}]}`。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

JUDGE = "ask_judge"
CONTINUE = "fix_again"
REVERT = "undo_item"
INFEASIBLE = "infeasible"
STALLED_AFTER_JUDGE = "stalled_after_judge"
UNDECIDED = "undecided"
NO_JUDGE = "no_judge"
REASONS = {
    INFEASIBLE: "成立しないと判定した",
    STALLED_AFTER_JUDGE: "成立すると判定して続けた後も修正が停滞か振動した",
    UNDECIDED: "判定の担い手が答えを残さなかった（判定できない）",
    NO_JUDGE: "修正担当と別の判定の担い手が居ない",
}


@dataclass(frozen=True)
class Move:
    kind: str
    reason: str = ""

    @property
    def detail(self) -> str:
        return REASONS[self.reason] if self.reason in REASONS else ""


def after_stall(item: Mapping[str, Any], judges: Sequence[str]) -> Move:
    """修正が停滞か振動したときの次の手。"""
    if item.get("feasibility"):
        return Move(REVERT, STALLED_AFTER_JUDGE)
    if not judges:
        return Move(REVERT, NO_JUDGE)
    return Move(JUDGE)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def answer_of(payload: Any, item_id: str) -> Optional[Mapping[str, Any]]:
    """答えから項目の行を取り出す。無い・形が違えば None。"""
    rows = payload.get("items") if isinstance(payload, Mapping) else None
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, Mapping) and _text(row.get("item")) == item_id and isinstance(row.get("feasible"), bool):
            return row
    return None


def record(by: str, row: Optional[Mapping[str, Any]], at: str) -> dict[str, Any]:
    """項目の `feasibility[]` へ積む 1 件。答えが無ければ `feasible` は None（判定できない）。"""
    if row is None:
        return {"by": by, "feasible": None, "reason": REASONS[UNDECIDED], "evidence": "", "at": at}
    return {
        "by": by,
        "feasible": bool(row["feasible"]),
        "reason": _text(row.get("reason")),
        "evidence": _text(row.get("evidence")),
        "at": at,
    }


def on_answer(row: Optional[Mapping[str, Any]]) -> Move:
    """判定の答えからの次の手。"""
    if row is None:
        return Move(REVERT, UNDECIDED)
    return Move(CONTINUE) if row["feasible"] else Move(REVERT, INFEASIBLE)
