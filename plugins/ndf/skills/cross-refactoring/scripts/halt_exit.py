"""止めて人へ渡す出口と、結果 JSON の予算・項目ごとの状態（drive.py の一部。#2457 決定 5・10）。

`Drive` に `HaltExit` を混ぜる。`halted` は安全網の上限・打ち消しの衝突・判定の担い手が居ないまま停滞したときの出口で、
項目を取り消さず HEAD をそのまま残し、`gate`・終了コード 10 の結果を組む。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import drive_pause as dp
import step_result as sr
from drive_pause import Stop

from assignee_launch import Halt


class HaltExit:
    """`Drive` に混ぜる。`self.call`・`self.refresh_plan_comment`・`self.counts`・`self.state` を使う。"""

    def item_actuals(self, end: str) -> None:
        """止まった出口で、採った項目ごとの実績の行を書く（#2457 決定 10）。書けなくても結果は変えない。"""
        if self.has_state():
            refactor = str(Path(__file__).resolve().parent / "refactor.py")
            self.call([sys.executable, refactor, "item-actuals", self.v["ID"], "--end", end])

    def skipped(self, e: Any, tool: str) -> dict:
        """時間の枠の不足で飛ばした結果（欠けの残る結果・終了コード 6）。状態ファイルが無ければ（打ち切りは保存の前）件数は持たない。"""
        if not self.comment_done:
            self.refresh_plan_comment()
        skipped = {"kind": e.kind, "reason": e.detail}
        metrics = {**(self.counts() if self.has_state() else {}), "skipped": skipped, "exit": e.code}
        gap = {"kind": "refactor", "name": e.kind, "reason": f"時間の枠に収まらず飛ばした: {e.detail}"}
        return dp.incomplete(tool, str(e), metrics, gap)

    def stopped(self, e: Stop, tool: str) -> dict:
        self.item_actuals("aborted")
        if not self.comment_done:
            self.refresh_plan_comment()
        return dp.stopped(tool, str(e), self.counts() if self.has_state() else {}, e.code)

    def halted(self, e: Halt, tool: str) -> dict:
        """止めて人へ渡す（gate・終了コード 10）。項目は取り消さず、HEAD をそのまま残す（#2457 決定 5・6）。"""
        self.item_actuals("safety_net")
        if not self.comment_done:
            self.refresh_plan_comment()
        metrics = {**(self.counts() if self.has_state() else {}), "exit": sr.EXIT_GATE, "halt": e.reason}
        items = [{"refactor_items": refactor_items(self.state())}] if self.has_state() else []
        nxt = (
            f"{e.reason}。続けるなら（安全網なら --safety-net-minutes を大きくして）同じコマンドを打ち直す。"
            "やめるなら項目のコミットを git revert して refactor.py plan-comment --scan-reverts を打つ"
        )
        return sr.result(tool, "gate", f"止めて人へ渡した: {e.reason}", items, metrics, next=nxt)


def budget_metrics(s: dict) -> dict:
    """結果 JSON の予算・安全網・判定の件数（#2457）。"""
    from refactor_lib import budget as budget_lib, progress, safety_net

    plan_budget = s.get("budget") or {"minutes": s.get("budget_minutes")}
    items = s.get("items") or []
    if plan_budget.get("minutes") is None:
        return {}
    ext = plan_budget.get("extension")
    return {
        "budget_minutes": plan_budget.get("minutes"),
        "effective_budget_minutes": budget_lib.effective_minutes(plan_budget),
        "budget_extension": {k: ext[k] for k in ("from", "to", "reason")} if ext else None,
        "safety_net_minutes": round(safety_net.limit_minutes(s), 1),
        "coding_minutes": round(safety_net.used_seconds(s) / 60, 1),
        "judged": sum(len(i.get("feasibility") or []) for i in items),
        "stalled": sum(1 for i in items for h in i.get("fix_history") or [] if h.get("progress") in progress.STOPPING),
        "flaky_fixed": len(s.get("flaky_fixes") or []),
    }


def refactor_items(s: dict) -> list[dict]:
    """結果 JSON の項目ごとの状態（`items[0].refactor_items`。#2457）。"""
    from refactor_lib import fixloop, item_actuals, items as items_lib

    out = []
    for i in s.get("items") or []:
        out.append(
            {
                "id": i.get("id"),
                "target": f"{i.get('path')}#{i.get('symbol')}",
                "status": i.get("status"),
                "feasibility": list(i.get("feasibility") or []),
                "failing": fixloop.failing_set(i) if i.get("status") == items_lib.FAILING else [],
                "fix_history": list(i.get("fix_history") or []),
                "outside_scope": list(i.get("outside_scope") or []),
                "estimate_minutes": {k: (i.get("estimate") or {}).get(k) for k in ("test", "implement")},
                "coding_seconds": item_actuals.coding_seconds(i),
            }
        )
    return out


def emit_if_halted(out: dict) -> dict:
    """止めて人へ渡した結果（gate・`metrics.exit` 10）は、判断待ちの表（`drive_pause.exit_code`）を通さず承認ゲートの
    終了コードで出す（#2457 決定 5）。それ以外はそのまま返す。"""
    if (
        out.get("status") == "gate"
        and (out.get("metrics") or {}).get("exit") == sr.EXIT_GATE
        and not (out.get("items") or [{}])[0].get("result_file")
    ):
        sr.emit(out, sr.EXIT_GATE)
    return out
