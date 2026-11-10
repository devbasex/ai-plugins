"""報告の採り直しの行（#1743 の F7・#2457）。`commands/report.py` が使う。

予算から締め切りを導かないため、バッファと止まっていた時間の行は持たない（#2457 の原則 1）。
"""

from __future__ import annotations

from typing import Any, Optional

from . import ledger


def readopt_line(state: dict[str, Any]) -> Optional[str]:
    """採り直しの 1 行。`readopt.events[]` が無ければ `None`。"""
    events = (state.get("readopt") or {}).get("events") or []
    if not events:
        return None
    picked = [e for e in events if e.get("reason") == "selected"]
    ids = [i for e in picked for i in e.get("selected") or []]
    left = ledger.budget_deferred(state)
    last = events[-1]
    what = f"{len(picked)} 回で {len(ids)} 件（{'・'.join(ids)}）" if ids else "0 件"
    return (
        f"- 採り直し: {what}。最後の判定の実装の予算の残り {float(last.get('available_minutes') or 0.0):.1f} 分。"
        f"採らずに残った `budget` の候補 {left} 件"
    )


def lines(state: dict[str, Any]) -> list[str]:
    """報告へ出す行（無いものは出さない）。"""
    return [line for line in (readopt_line(state),) if line]
