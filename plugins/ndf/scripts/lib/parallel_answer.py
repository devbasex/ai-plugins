"""並列の計画の問いへ答えた後に、だれを起こすか（#2436 の 6c・6f）。`parallel.py answer` だけが使う。

`parallel_run`（耐久ワークフローの本体）を読むため、レーンの側の `parallel_lane` から分けて置く（レーンの側の入口が
`parallel_run` の環境変数を読まないように）。
"""

from __future__ import annotations

from pathlib import Path

import parallel_lane as pl


def owner_restart(main: Path, name: str, rid: str, choice: list[str] | None = None) -> str | None:
    """答えの後の 1 行。自分で立てた問い（stall・helper・spawn）は選んだ答えの commands、ほかは答えを待って止まった持ち主の
    restart。起こす要否はレーンの側の待ちの記録で決める（`parallel_lane.lane_waits`）。同じ原因としてまとめた問い（`merged`）は、
    代表だけでなく全持ち主をそれぞれの依頼の id で調べ、起こす行を `; ` でつなぐ（異常系 5）。"""
    import durable
    import parallel_run as pr_run

    items = (durable.event_from(pl.KIND, pl.record_identity(main, name), pl.workflow_id(name), "questions", 0) or {}).get("items") or []
    q = next((q for q in items if q.get("id") == rid), {})
    if q.get("kind") in ("stall", "helper", "spawn") or not q.get("lane"):
        return next((q["commands"][c] for c in choice or [] if (q.get("commands") or {}).get(c)), None)
    ls = lambda: pr_run.relay(main, "ls", "--json")  # noqa: E731
    owners = [(q["lane"], rid), *((m.get("lane"), m.get("id")) for m in q.get("merged") or [] if m.get("lane"))]
    lanes = [lane for lane, each in owners if pl.lane_waits(main, name, lane, str(each or rid), ls)]
    lanes = list(dict.fromkeys(lanes))
    return "; ".join(f"python3 {pr_run.RELAY} restart {lane}" for lane in lanes) or None
