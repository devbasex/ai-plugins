"""並列の計画の問いへ答えた後に、だれを起こすか（#2436 の 6c・6f）と、答えの後に残る依頼の一覧（#2507 の答えの後の整理）。
`parallel.py answer` だけが使う。

`parallel_run`（耐久ワークフローの本体）を読むため、レーンの側の `parallel_lane` から分けて置く（レーンの側の入口が
`parallel_run` の環境変数を読まないように）。
"""

from __future__ import annotations

from pathlib import Path

import parallel_lane as pl


def questions(main: Path, name: str) -> tuple[int, list[dict]]:
    """耐久ワークフローの今の依頼の一覧 (seq, items)。読めなければ (0, [])。"""
    import durable

    got = durable.event_from(pl.KIND, pl.record_identity(main, name), pl.workflow_id(name), "questions", 0) or {}
    return int(got.get("seq") or 0), list(got.get("items") or [])


MVV_VERDICTS = ("unknown", "unreadable")  # レーンの判定が決まらなかった（判定できない・判定を読めない）


def mvv_mark(q: dict) -> dict:
    """MVV 判定でオーガナイザーが自分で答えてよい依頼か（#2507 の決定 8）。設計の承認で、レーンの判定が決まらず、
    レッドラインが空で、判定を打ち直す材料（レーンの周回のスプリント状態）が残るときだけ true。"""
    if q.get("kind") != "gate1":
        return {"eligible": False, "why": "設計の承認の依頼でない"}
    if q.get("boundary"):
        return {"eligible": False, "why": "レッドラインに当たる"}
    if q.get("verdict") not in MVV_VERDICTS:
        return {"eligible": False, "why": f"レーンの判定が決まっている（{q.get('verdict')}）"}
    if not q.get("sprint_state") or not Path(q["sprint_state"]).is_file():
        return {"eligible": False, "why": "判定を打ち直す材料（スプリント状態）が無い"}
    return {"eligible": True, "why": f"レーンの判定が決まらなかった（{q.get('verdict')}）"}


def after_items(items: list[dict], answered: dict) -> list[dict]:
    """答えの後に残る依頼の一覧（`metrics.after`）。今答えた依頼を除き、機械で決まる除外の印（drop）と MVV 判定の印（mvv）を付ける。
    drop: settled（今送った答えと同じ設計 PR の依頼）/ resend（同じレーンの同じ設計 PR に、より新しい先端の依頼がある）/ None。"""
    rid = answered.get("id")
    rest = [q for q in items if q.get("id") != rid and rid not in (q.get("also") or [])]
    key = lambda q: (q.get("lane"), q.get("pr"))  # noqa: E731

    def drop(q: dict) -> str | None:
        if q.get("kind") != "gate1":
            return None
        if answered.get("kind") == "gate1" and key(q) == key(answered):
            return "settled"
        if any(o is not q and o.get("kind") == "gate1" and key(o) == key(q) and str(o.get("at") or "") > str(q.get("at") or "") for o in rest):
            return "resend"
        return None

    return [{**q, "drop": drop(q), "mvv": mvv_mark(q)} for q in rest]


def owner_restart(main: Path, name: str, rid: str, choice: list[str] | None = None, items: list[dict] | None = None) -> str | None:
    """答えの後の 1 行。自分で立てた問い（stall・helper・spawn）は選んだ答えの commands、ほかは答えを待って止まった持ち主の
    restart。起こす要否はレーンの側の待ちの記録で決める（`parallel_lane.lane_waits`）。同じ原因としてまとめた問い（`merged`）は、
    代表だけでなく全持ち主をそれぞれの依頼の id で調べ、起こす行を `; ` でつなぐ（異常系 5）。"""
    import parallel_run as pr_run

    items = questions(main, name)[1] if items is None else items
    q = next((q for q in items if q.get("id") == rid), {})
    if q.get("kind") in ("stall", "helper", "spawn") or not q.get("lane"):
        return next((q["commands"][c] for c in choice or [] if (q.get("commands") or {}).get(c)), None)
    ls = lambda: pr_run.relay(main, "ls", "--json")  # noqa: E731
    owners = [(q["lane"], rid), *((m.get("lane"), m.get("id")) for m in q.get("merged") or [] if m.get("lane"))]
    lanes = [lane for lane, each in owners if pl.lane_waits(main, name, lane, str(each or rid), ls)]
    lanes = list(dict.fromkeys(lanes))
    return "; ".join(f"python3 {pr_run.RELAY} restart {lane}" for lane in lanes) or None
