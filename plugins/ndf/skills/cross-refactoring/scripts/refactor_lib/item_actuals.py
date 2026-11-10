"""項目ごとの実績の行（#2457 決定 10・不変条件 I12）。

改善項目を採った実行は、終わり方（最終ゲートの通過・安全網・中断）を問わず、採った項目ごとに 1 行（`kind: item`）を
配分の履歴（`<metrics>/<owner>--<repo>/cross-refactoring-allocation.jsonl`）へ追記する。配分テーブルの集計は実行の行
（`kind: run`）だけを読み、項目の行を読まない。1 回の実行で 2 度書かない（`state["item_actuals"]`）。
"""

from __future__ import annotations

from typing import Any

import run_metrics
import statefile

from . import allocation, budget, info

ITEM_KIND = "item"
ENDS = ("final_gate", "safety_net", "aborted")


def coding_seconds(item: dict[str, Any]) -> dict[str, float]:
    """項目の実装時間（秒）。テストの追加と実装はコミットの時刻で測った所要、修正は起動の秒の等分（決定 5）。"""
    seconds = item.get("seconds") or {}
    fix = (item.get("coding_seconds") or {}).get("fix")
    return {
        "test": round(float(seconds.get("test") or 0.0), 1),
        "implement": round(float(seconds.get("implement") or 0.0), 1),
        "fix": round(float(fix or 0.0), 1),
    }


def rows(state: dict[str, Any], end: str, status_of: Any = None) -> list[dict[str, Any]]:
    """採った項目ごとの行。`status_of(item)` は表示の状態（無ければ項目の `status`）。"""
    plan_budget = state.get("budget") or {"minutes": state.get("budget_minutes")}
    out = []
    for item in state.get("items") or []:
        estimate = item.get("estimate") or {}
        out.append(
            {
                "kind": ITEM_KIND,
                "run_id": str(state.get("run_id") or state.get("started_at") or ""),
                "repo": state.get("repo"),
                "pr": state.get("current_pr") or state.get("id"),
                "at": statefile.now(),
                "item_id": item.get("id"),
                "target": f"{item.get('path')}#{item.get('symbol')}",
                "technique": item.get("technique"),
                "tier": item.get("tier"),
                "estimate_minutes": {"test": float(estimate.get("test") or 0.0), "implement": float(estimate.get("implement") or 0.0)},
                "coding_seconds": coding_seconds(item),
                "status": status_of(item) if status_of else item.get("status"),
                "end": end,
                "budget_minutes": plan_budget.get("minutes"),
                "effective_budget_minutes": budget.effective_minutes(plan_budget) if plan_budget.get("minutes") is not None else None,
            }
        )
    return out


def write(path: Any, state: dict[str, Any], end: str, status_of: Any = None) -> int:
    """項目の行を追記し、書いた行の数を返す。書けなければ知らせて 0（進行は止めない）。"""
    if state.get("item_actuals") or not state.get("items"):
        return 0
    found = rows(state, end, status_of)
    try:
        target = allocation.history_path(run_metrics.metrics_dir(), str(state["repo"]))
        for row in found:
            allocation.append_row(target, row)
    except OSError as exc:
        info(f"⚠ 項目ごとの実績を配分の履歴へ追記できませんでした（{exc}）")
        state["item_actuals"] = {"end": end, "written": 0, "error": str(exc), "at": statefile.now()}
        statefile.save(path, state)
        return 0
    state["item_actuals"] = {"end": end, "written": len(found), "at": statefile.now()}
    statefile.save(path, state)
    return len(found)
