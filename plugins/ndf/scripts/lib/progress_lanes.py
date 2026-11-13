"""計画の進行表とレーンの進行表の規則（#3092 の I12a・I13・I16・I18〜I21・決定 7。#3097 から取り込んだ）。

純粋な処理で、DB も DBOS も import しない。レーンの進行表の事象から順番表の行を、計画の進行表の事象からレーン・上限・
依頼を畳み込み、順番表の変更をレーンをまたぐ決まりで判定する。書くのは `progress_db.lane_write` だけで、書く直前に
`check_lane_change` を同じトランザクションの中で呼ぶ。
"""

from __future__ import annotations

import graphlib
from typing import Any, Optional

from progress_model import CANCELLED, COMPLETED, PENDING, RUNNING, LABELS, EventDraft


def lane_order_rows(events: list) -> list[dict]:
    """レーンの進行表の事象から、順番表の行を位置の順に作る。"""
    order: list[int] = []
    data: dict[int, dict] = {}
    for e in events:
        p = e.payload or {}
        if e.kind == "row_added" or (e.kind == "created" and (e.row_key or "").isdigit()):  # 最初の順番表の行は created
            n = int(p["row"])
            data[n] = {
                "row": n,
                "issues": list(p.get("issues") or []),
                "touches": list(p.get("touches") or []),
                "after": list(p.get("after") or []),
                "args": list(p.get("args") or []),
                "state": PENDING,
                "sprint": None,
            }
            pos = p.get("position")
            order.insert(len(order) if pos is None else int(pos), n)
        elif e.kind == "row_moved":
            n = int(p["row"])
            order.remove(n)
            order.insert(int(p["to"]), n)
        elif e.row_key is not None and e.row_key.isdigit() and int(e.row_key) in data:
            row = data[int(e.row_key)]
            st = e.resolved_state()
            if st:
                row["state"] = st
            if e.kind == "row_started":
                row["sprint"] = p.get("sprint")
            if e.kind == "adopted" and p.get("sprint"):
                row["sprint"] = p.get("sprint")
    return [data[n] for n in order]


def plan_view(events: list) -> dict:
    """計画の進行表の事象から、レーン・上限・依頼・状態を作る。"""
    lanes: dict[str, dict] = {}
    requests: dict[str, dict] = {}
    plan: dict[str, Any] = {"lanes": lanes, "requests": requests, "max_lanes": None, "state": PENDING}
    for e in events:
        p = e.payload or {}
        if e.kind == "created" and e.row_key is None:
            plan["max_lanes"] = p.get("max_lanes")
            plan["rules"] = p.get("rules")
        elif e.kind in ("lane_added", "lane_changed"):
            lanes[p["lane"]] = {**lanes.get(p["lane"], {}), **{k: v for k, v in p.items() if k != "lane"}}
        elif e.kind == "request_opened":
            requests[str(e.seq)] = {"id": str(e.seq), "body": p, "reason": e.reason, "open": True}
        elif e.kind == "request_closed" and p.get("request") in requests:
            requests[p["request"]]["open"] = False
        st = e.resolved_state()
        if e.row_key is None and st:
            plan["state"] = st
    return plan


def _rows_index(lanes: dict) -> dict[str, dict]:
    return {f"{ln}#{r['row']}": {**r, "lane": ln} for ln, rows in lanes.items() for r in rows}


def _after_target(ref: str, lanes: dict) -> Optional[str]:
    """`after` の 1 つ（`<レーン>` か `<レーン>#<行>`）が指す行の鍵。レーンだけなら最初の行。"""
    if "#" in ref:
        return ref
    rows = lanes.get(ref) or []
    return f"{ref}#{rows[0]['row']}" if rows else None


def _row_graph(lanes: dict) -> dict[str, set]:
    """行の前後の有向グラフ（レーンの中の並びと `after`）。辺は前 → 後ろ。"""
    preds: dict[str, set] = {}
    for ln, rows in lanes.items():
        live = [r for r in rows if r["state"] != CANCELLED]
        for i, r in enumerate(live):
            k = f"{ln}#{r['row']}"
            preds.setdefault(k, set())
            if i:
                preds[k].add(f"{ln}#{live[i - 1]['row']}")
            for ref in r.get("after") or []:
                t = _after_target(ref, lanes)
                if t:
                    preds[k].add(t)
    return preds


def _reach(preds: dict[str, set]) -> dict[str, set]:
    """行ごとに、前にある行の集合（到達の集合）。"""
    memo: dict[str, set] = {}

    def up(k: str, seen: frozenset) -> set:
        if k not in memo:
            memo[k] = set().union(*({p} | up(p, seen | {p}) for p in preds.get(k, ()) if p not in seen))
        return memo[k]

    return {k: up(k, frozenset({k})) for k in preds}


def _violations(plan: dict, lanes: dict) -> set[tuple]:
    from parallel_plan import _touch_hit  # 触る場所の重なりの判定は計画の検査と同じ関数を使う

    out: set[tuple] = set()
    idx = _rows_index(lanes)
    live = {k: r for k, r in idx.items() if r["state"] not in (CANCELLED, COMPLETED)}
    preds = _row_graph(lanes)
    try:
        graphlib.TopologicalSorter(preds).prepare()
    except graphlib.CycleError as e:
        out.add(("I21", f"after が輪になる（{' → '.join(map(str, e.args[1]))}）"))
        return out
    reach = _reach(preds)
    keys = sorted(live)
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            ra, rb = live[a], live[b]
            if ra["lane"] == rb["lane"]:
                continue
            if set(ra["issues"]) & set(rb["issues"]):
                out.add(("I19", f"{a} と {b} で課題が重なる（{sorted(set(ra['issues']) & set(rb['issues']))}）"))
            if a in reach.get(b, set()) or b in reach.get(a, set()):
                continue
            ta = ra["touches"] or (plan["lanes"].get(ra["lane"]) or {}).get("touches") or []
            tb = rb["touches"] or (plan["lanes"].get(rb["lane"]) or {}).get("touches") or []
            hits = sorted({f"{x}・{y}" for x in ta for y in tb if _touch_hit(x, y)})
            if hits:
                out.add(("I13", f"同時に動き得る {a} と {b} で触る場所が重なる（{', '.join(hits)}）"))
    cap = plan.get("max_lanes")
    if cap is not None and len(plan["lanes"]) > cap:
        out.add(("I20", f"レーンの数 {len(plan['lanes'])} が上限 {cap} を超える"))
    return out


def apply_lane_change(plan: dict, lanes: dict, change: EventDraft) -> tuple[dict, dict]:
    """変更を写した計画とレーンの写しを返す（判定のため。書き込みはしない）。"""
    p = change.payload or {}
    plan2 = {**plan, "lanes": {k: dict(v) for k, v in plan["lanes"].items()}}
    lanes2 = {k: [dict(r) for r in v] for k, v in lanes.items()}
    lane = p.get("lane")
    if change.kind in ("lane_added", "lane_changed"):
        plan2["lanes"][lane] = {**plan2["lanes"].get(lane, {}), **{k: v for k, v in p.items() if k != "lane"}}
        lanes2.setdefault(lane, [])
    elif change.kind == "row_added":
        rows = lanes2.setdefault(lane, [])
        new = {
            "row": int(p["row"]),
            "issues": list(p.get("issues") or []),
            "touches": list(p.get("touches") or []),
            "after": list(p.get("after") or []),
            "state": PENDING,
            "sprint": None,
        }
        rows.insert(len(rows) if p.get("position") is None else int(p["position"]), new)
    elif change.kind == "row_moved":
        rows = lanes2[lane]
        r = next(x for x in rows if x["row"] == int(p["row"]))
        rows.remove(r)
        rows.insert(int(p["to"]), r)
    elif change.kind == "row_cancelled":
        for r in lanes2[lane]:
            if r["row"] == int(p["row"]):
                r["state"] = CANCELLED
    elif change.kind == "row_ended":
        for r in lanes2[lane]:
            if r["row"] == int(p["row"]):
                r["state"] = p.get("state", COMPLETED)
    return plan2, lanes2


def check_lane_change(plan: dict, lanes: dict, change: EventDraft) -> list[str]:
    """レーンの順番表の変更を計画の決まりで判定する（I12a・I13・I18〜I21）。断る理由の一覧を返す。"""
    p = change.payload or {}
    lane = p.get("lane")
    errs: list[str] = []
    rows = lanes.get(lane) or []
    if change.kind in ("row_moved", "row_cancelled", "row_added") and lane not in plan["lanes"]:
        return [f"計画に無いレーン {lane}"]
    if change.kind in ("row_moved", "row_cancelled"):
        target = next((r for r in rows if r["row"] == int(p["row"])), None)
        if target is None:
            return [f"{lane} に行 {p['row']} が無い"]
        if target["state"] in (RUNNING, COMPLETED):
            errs.append(f"{lane}#{target['row']} は{LABELS[target['state']]}で、位置と課題を変えられない（I12a）")
        key = f"{lane}#{target['row']}"
        refd = [
            f"{ln}#{r['row']}"
            for ln, rs in lanes.items()
            if ln != lane
            for r in rs
            if key in {_after_target(ref, lanes) for ref in r.get("after") or []}
        ]
        moving_back = change.kind == "row_moved" and int(p["to"]) > rows.index(target)
        if refd and (change.kind == "row_cancelled" or moving_back):
            errs.append(f"{key} は {', '.join(refd)} の after が指すため、消せず後ろへも回せない（I18）")
    if change.kind == "row_added" and p.get("position") is not None:
        fixed = [i for i, r in enumerate(rows) if r["state"] in (RUNNING, COMPLETED)]
        if fixed and int(p["position"]) <= max(fixed):
            errs.append(f"{lane} の実行中か完了の行より前へは足せない（I12a）")
    if errs:
        return errs
    before = _violations(plan, lanes)
    plan2, lanes2 = apply_lane_change(plan, lanes, change)
    return [f"{why}（{rule}）" for rule, why in sorted(_violations(plan2, lanes2) - before)]


def _after_met(row: dict, lanes: dict) -> bool:
    idx = _rows_index(lanes)
    for ref in row.get("after") or []:
        t = _after_target(ref, lanes)
        if t is None or idx.get(t, {}).get("state") != COMPLETED:
            return False
    return True


def next_row(lane: str, plan: dict, lanes: dict) -> Optional[Any]:
    """終えた行の次の行と、その `after` が満たされているかを返す（決定 7）。

    次の未着手の行が無ければ None、`after` が満たされなければ `"waiting_after"`、満たされれば
    `progress.py new` の引数（`{"lane": ..., "row": ..., "issues": ..., "args": ...}`）を返す。"""
    nxt = next((r for r in lanes.get(lane) or [] if r["state"] == PENDING), None)
    if nxt is None:
        return None
    if not _after_met(nxt, lanes):
        return "waiting_after"
    return {"lane": lane, "row": nxt["row"], "issues": nxt["issues"], "args": nxt.get("args") or []}


def wakeable_lanes(plan: dict, lanes: dict) -> list[tuple[str, int]]:
    """実行中の行が無く、`after` を満たす未着手の行があるレーンを、計画の並びの順で返す。"""
    out = []
    for ln in plan["lanes"]:
        rows = lanes.get(ln) or []
        if any(r["state"] == RUNNING for r in rows):
            continue
        nxt = next((r for r in rows if r["state"] == PENDING), None)
        if nxt is not None and _after_met(nxt, lanes):
            out.append((ln, nxt["row"]))
    return out


def plan_complete(lanes: dict, requests: list) -> bool:
    """全レーンの行が完了か中止で、答えていない依頼が無いときだけ真（I16）。"""
    done = all(r["state"] in (COMPLETED, CANCELLED) for rows in lanes.values() for r in rows)
    return done and not any(q.get("open") for q in requests)
