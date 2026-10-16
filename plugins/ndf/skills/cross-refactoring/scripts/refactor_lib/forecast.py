"""配分テーブルの読み出しと、計画の後に要る時間の見込み（#933 決定 7・#1743 の F2）。

`merge-plan`（採る件数）と `init`（提案の前に止まる判定）が同じ表と同じバッファの式を使う。履歴の根は
`run_metrics.metrics_dir()` で決める（`allocation` は根を引数で受けるため、ここで決める）。
"""

from __future__ import annotations

from typing import Any

import run_metrics

from . import allocation, budget, info


def allocation_table(state: dict[str, Any], notify: bool = True) -> dict[str, Any]:
    """配分テーブルを履歴から集計する（決定 7）。読めなければ初期値で、`notify` なら 1 行知らせる（AC20）。

    `init` の見込み（#1743 の F2）と `merge-plan` が同じ表を読む。
    """
    base = run_metrics.metrics_dir()
    rows = allocation.read_history(allocation.history_path(base, str(state["repo"])))
    table = allocation.build_table(rows, allocation.load_defaults())
    if notify and table.get("source") != "history":
        info("ℹ 配分の履歴が無いか読めないため、初期値（#917 の実測）で改修計画します")
    return table


def after_plan(state: dict[str, Any]) -> dict[str, Any]:
    """計画の後に要る時間の見込み（バッファの見込み R と 1 件の長さ L）。`merge-plan` と同じ式で出す（決定 2）。"""
    table = allocation_table(state, notify=False)
    reserve = budget.plan_reserve(state, table)
    item = budget.shortest_item_minutes(table, budget.measured_verify_minutes(state))
    return {
        "reserve_minutes": round(budget.reserve_total(reserve), 2),
        "item_minutes": round(item, 2),
        "table_source": table.get("source"),
    }
