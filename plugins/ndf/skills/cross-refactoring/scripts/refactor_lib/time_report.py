"""報告の時間の配分の行（#1743 の F4・F5・F7、#1491）。`commands/report.py` が使う。

バッファのうち使わずに残った時間・バッファを越えた全体テスト・止まっていた時間・採り直しの行を、状態ファイルの値だけから組む。
"""

from __future__ import annotations

from typing import Any, Optional

from . import pause
from .allocation import RESERVE_KEYS, reserve_usage
from .vocabulary import DEFER_BUDGET

RESERVE_LABELS = {
    "danger_whole_test": "危険フラグの全体テスト",
    "final_whole_test": "最終ゲートの全体テスト",
    "fix": "修正",
    "final_fix": "最終ゲート修正",
}
WHOLE_KEYS = ("danger_whole_test", "final_whole_test")


def _minutes(seconds: float) -> str:
    return f"{float(seconds) / 60:.1f}"


def reserve_line(state: dict[str, Any]) -> Optional[str]:
    """バッファのうち使わずに残った時間を区分ごとに 1 行。バッファが無ければ `None`。"""
    usage = reserve_usage(state)
    if not usage:
        return None
    parts = " / ".join(f"{RESERVE_LABELS[k]} {_minutes(usage[k]['unused_seconds'])}" for k in RESERVE_KEYS)
    total = sum(usage[k]["unused_seconds"] for k in RESERVE_KEYS)
    return f"- バッファのうち使わずに残った時間: {parts} 分（計 {_minutes(total)} 分）"


def over_line(state: dict[str, Any]) -> Optional[str]:
    """バッファを越えた全体テストの 1 行。越えた区分が無ければ `None`。"""
    usage = reserve_usage(state)
    over = [
        f"{RESERVE_LABELS[k]} {_minutes(usage[k]['used_seconds'] - usage[k]['reserved_seconds'])} 分"
        for k in WHOLE_KEYS
        if k in usage and usage[k]["used_seconds"] > usage[k]["reserved_seconds"]
    ]
    if not over:
        return None
    skipped = "直しの試行" if (state.get("fix_stats") or {}).get("cut_off") else "無し"
    return f"- バッファを越えた全体テスト: {' / '.join(over)}をバッファの外で走らせた（省いたもの: {skipped}）"


def readopt_line(state: dict[str, Any]) -> Optional[str]:
    """採り直しの 1 行。`readopt.events[]` が無ければ `None`。"""
    events = (state.get("readopt") or {}).get("events") or []
    if not events:
        return None
    picked = [e for e in events if e.get("reason") == "selected"]
    ids = [i for e in picked for i in e.get("selected") or []]
    left = sum(1 for d in state.get("deferred_items") or [] if d.get("defer_reason") == DEFER_BUDGET)
    last = events[-1]
    what = f"{len(picked)} 回で {len(ids)} 件（{'・'.join(ids)}）" if ids else "0 件"
    return f"- 採り直し: {what}。最後の判定の残った時間 {float(last.get('available_minutes') or 0.0):.1f} 分。採らずに残った `budget` の候補 {left} 件"


def lines(state: dict[str, Any]) -> list[str]:
    """報告へ出す行（無いものは出さない）。"""
    pause_line = pause.report_line(state)
    found = [reserve_line(state), over_line(state), f"- {pause_line}" if pause_line else None, readopt_line(state)]
    return [line for line in found if line]
