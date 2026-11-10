"""成立の判定の取り込み（`judge`、#2457 決定 3・受け入れ条件 6・17）。

`verify` が `VERIFY=judge` で依頼した項目（`feasibility_request`）について、判定の担い手（実装担当と別のランタイム）の
答え `{"items": [{"item", "feasible", "reason", "evidence"}]}` を読み、項目の `feasibility[]` へ積む。答えが無い・読めない
項目は「判定できない」（`feasible: null`）として積む。次の手（修正を続ける・修正担当を替える・打ち消す・止める）は
次の `verify` が `feasibility[]` と前進の判定から決める。

出力: `JUDGE=continue|infeasible|no_judge`（成立しない項目があれば `infeasible`、答えを 1 件も読めなければ `no_judge`）。
"""

from __future__ import annotations

import argparse
from typing import Any

import statefile

from .. import fixloop, info
from ..items import find_item
from ..paths import load_state
from ..results import read_result


def _answers(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return {}
    out = {}
    for row in payload["items"]:
        if isinstance(row, dict) and isinstance(row.get("item"), str) and isinstance(row.get("feasible"), bool):
            out[row["item"].strip()] = row
    return out


def cmd_judge(args: argparse.Namespace) -> None:
    """判定の答えを取り込む。依頼が無ければ何もしない（`JUDGE=continue`）。"""
    path, state = load_state(args.id)
    request = state.get("feasibility_request") or {}
    if not request.get("items"):
        statefile.emit(JUDGE="continue")
        return
    by = str(request.get("by") or "")
    answers = _answers(read_result(state, by, "judge").payload)
    infeasible = []
    for item_id in request["items"]:
        item = find_item(state, item_id, required=False)
        if item is None:
            continue
        rejudge = bool((request.get("rejudge") or {}).get(item_id))
        row = answers.get(item_id)
        if row is None:
            fixloop.note_feasibility(item, by, None, "判定の担い手が答えを残さなかった（判定できない）", "", rejudge)
            continue
        fixloop.note_feasibility(item, by, bool(row["feasible"]), str(row.get("reason") or ""), str(row.get("evidence") or ""), rejudge)
        if not row["feasible"]:
            infeasible.append(item_id)
    state["feasibility_request"] = None
    statefile.save(path, state)
    if not answers:
        info(f"⚠ 判定の担い手 {by} の答えを読めませんでした（判定できない）")
        statefile.emit(JUDGE="no_judge")
        return
    if infeasible:
        info(f"⚖ 成立しないと判定した項目: {', '.join(infeasible)}")
    statefile.emit(JUDGE="infeasible" if infeasible else "continue")
