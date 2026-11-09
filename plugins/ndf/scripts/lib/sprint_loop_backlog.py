"""sprint_loop_backlog.py: ループの 1 回の記録へ写す棚卸しの深さの行と調査の費用・秒を組む（#2315）。LLM を呼ばない。

読み込みとループの 1 回への反映は `sprint_loop_gate.backlog_summary` が持ち、ここは読んだ行から値を組むだけである。"""

from __future__ import annotations


def lap_depths(rows: list, since: str) -> list:
    """棚卸しの深さの行のうち、ループの 1 回の始まり以後のものをループの 1 回の記録に残す項目だけへ絞る。"""
    return [{k: r.get(k) for k in ("depth", "event", "trigger", "cost", "seconds")} for r in rows if (r.get("at") or "") >= since]


def lap_research(calls: list, loop: str | None, n) -> dict | None:
    """このループのこのループの 1 回の調査の呼び出しから、項目と費用と秒を組む。該当が無ければ None。

    結果を捨てた呼び出し（ok が偽）も支払った費用として数え、項目には ok の呼び出しだけを残す。"""
    mine = [r for r in calls if r.get("lap") == n and (r.get("by") or {}).get("loop") == loop]
    if not mine:
        return None
    return {
        "items": [r.get("id") for r in mine if r.get("ok")],
        "cost": round(sum(float(r.get("cost") or 0) for r in mine), 6),
        "seconds": round(sum(float(r.get("seconds") or 0) for r in mine), 3),
    }
