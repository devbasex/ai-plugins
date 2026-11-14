"""計画の決まり（#3097）: レーンをまたぐ制約（I2・I13・I18・I19・I20・I21・I12a）と、次に始める行・レーンの状態の判定。

どれも計画の進行表とレーンの進行表の畳み込み（`ndf.progress.lane.types`）を受ける純粋な関数で、DB を読まない。
"""

from __future__ import annotations

import fnmatch
import graphlib
import itertools
from collections.abc import Mapping
from typing import Any, Optional

from ndf.progress import model
from ndf.progress.lane.types import CANCELLED, COMPLETED, DONE_STATES, PENDING, RUNNING, WAITING_AFTER, Lanes, LaneRow, LaneSpec, PlanView


def _rows_of(lanes: Lanes, name: str) -> list[LaneRow]:
    """レーンの行（計画に無いレーンなら空）。"""
    return list(lanes[name]) if name in lanes else []


def _next_of(edges: Mapping[tuple[str, str], set[tuple[str, str]]], node: tuple[str, str]) -> set[tuple[str, str]]:
    return edges[node] if node in edges else set()


def _target(ref: str) -> tuple[str, Optional[str]]:
    lane, _, row = ref.partition("/")
    return lane, (row if row else None)


def after_met(ref: str, lanes: Lanes) -> bool:
    """`after` の 1 つが満たされたか。行なら完了か中止、レーンなら全行が完了か中止。"""
    lane, row = _target(ref)
    rows = [r for r in _rows_of(lanes, lane) if row is None or r.key == row]
    return bool(rows) and all(r.state in DONE_STATES for r in rows)


def _after_errors(view: PlanView, lanes: Lanes) -> list[str]:
    errs = []
    for spec in view.lanes.values():
        for lane, row in map(_target, spec.after):
            if lane not in view.lanes or lane == spec.name:
                errs.append(f"レーン {spec.name} の after の先 {lane} が計画に無い")
            elif row is not None and row not in {r.key for r in _rows_of(lanes, lane)}:
                errs.append(f"レーン {spec.name} の after の先 {lane}/{row} がレーンの順番表に無い")
    graph = {s.name: {_target(a)[0] for a in s.after} for s in view.lanes.values()}
    try:
        graphlib.TopologicalSorter(graph).prepare()
    except graphlib.CycleError as exc:
        errs.append(f"after が輪になる（I21）: {' → '.join(exc.args[1])}")
    return errs


def _live_lanes(view: PlanView, lanes: Lanes) -> list[str]:
    """終わっていないレーン（未着手か実行中の行を持つ）。終わったレーンは上限にも重なりにも数えない（#3021）。"""
    return [name for name in view.lanes if any(r.live for r in _rows_of(lanes, name))]


def _limit_errors(view: PlanView, lanes: Lanes) -> list[str]:
    live = _live_lanes(view, lanes)
    errs = [f"終わっていないレーンの数 {len(live)} が上限 {view.max_lanes} を超える（I20）"] if len(live) > view.max_lanes else []
    # プランの本数はレーンごとに見る。after で直列のレーンを足し合わせない（#3145）
    return errs + [
        f"レーン {n} のプランの本数 {view.lanes[n].plans} が上限 {view.max_plans} を超える（I2）"
        for n in live
        if view.lanes[n].plans > view.max_plans
    ]


def _same_repo(a: LaneSpec, b: LaneSpec) -> bool:
    return a.repo == b.repo


def _issue_errors(view: PlanView, lanes: Lanes) -> list[str]:
    errs = []
    for a, b in itertools.combinations(_live_lanes(view, lanes), 2):
        if not _same_repo(view.lanes[a], view.lanes[b]):
            continue
        hit = set().union(*(r.issues() for r in lanes[a] if r.live)) & set().union(*(r.issues() for r in lanes[b] if r.live))
        if hit:
            errs.append(f"レーン {a} と {b} の間で課題が重なる（I19）: {', '.join(f'#{n}' for n in sorted(hit))}")
    return errs


def touch_hit(x: str, y: str) -> bool:
    """触る場所の 2 つが重なるか。接頭辞か glob が当たれば重なる。"""
    px, py = x.rstrip("/"), y.rstrip("/")
    if px == py or px.startswith(py + "/") or py.startswith(px + "/"):
        return True
    return fnmatch.fnmatch(px, py) or fnmatch.fnmatch(py, px)


def _after_source(ref: str, lanes: Lanes) -> Optional[tuple[str, str]]:
    """`after` の 1 つが指す起点の行（行の指定ならその行、レーンならその末尾の行）。レーンに行が無ければ None。"""
    lane, row = _target(ref)
    if row is not None:
        return lane, row
    keys = [r.key for r in _rows_of(lanes, lane)]
    return (lane, keys[-1]) if keys else None


def _order_graph(view: PlanView, lanes: Lanes) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """行の前後の鎖（レーンの中の並びと after）。辺は前の行 → 後の行。"""
    edges: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for name, rows in lanes.items():
        nodes = [(name, r.key) for r in rows]
        for a, b in itertools.pairwise(nodes):
            edges.setdefault(a, set()).add(b)
        spec = view.lanes.get(name)
        if not nodes or spec is None:
            continue
        for ref in spec.after:
            src = _after_source(ref, lanes)
            if src is not None:
                edges.setdefault(src, set()).add(nodes[0])
    return edges


def _reach(edges: Mapping[tuple[str, str], set[tuple[str, str]]], start: tuple[str, str]) -> set[tuple[str, str]]:
    seen, todo = set(), [start]
    while todo:
        for nxt in _next_of(edges, todo.pop()) - seen:
            seen.add(nxt)
            todo.append(nxt)
    return seen


def _concurrent(edges: Mapping[tuple[str, str], set[tuple[str, str]]], x: tuple[str, str], y: tuple[str, str]) -> bool:
    """2 つの行の間に前後の鎖が無いか（同時に動き得るか）。"""
    return y not in _reach(edges, x) and x not in _reach(edges, y)


def _concurrent_pairs(edges: Mapping[tuple[str, str], set[tuple[str, str]]], lanes: Lanes, a: str, b: str) -> list[str]:
    """レーン a と b の終わっていない行のうち、同時に動き得る組。"""
    return [
        f"{a}/{x.key}・{b}/{y.key}"
        for x, y in itertools.product([r for r in lanes[a] if r.live], [r for r in lanes[b] if r.live])
        if _concurrent(edges, (a, x.key), (b, y.key))
    ]


def _touch_hits(la: LaneSpec, lb: LaneSpec) -> list[str]:
    """2 つのレーンの触る場所の重なる組。"""
    return sorted({f"{x}・{y}" for x, y in itertools.product(la.touches, lb.touches) if touch_hit(x, y)})


def _touch_errors(view: PlanView, lanes: Lanes) -> list[str]:
    """同時に動き得る行の組（前後の鎖が無い別のレーンの行どうし）の間で、触る場所が重なる組（I13）。"""
    edges, errs = _order_graph(view, lanes), []
    for a, b in itertools.combinations(_live_lanes(view, lanes), 2):
        la, lb = view.lanes[a], view.lanes[b]
        hits = _touch_hits(la, lb)
        if not hits or not _same_repo(la, lb):
            continue
        pairs = _concurrent_pairs(edges, lanes, a, b)
        if pairs:
            errs.append(f"同時に動き得る行 {', '.join(pairs)} の触る場所が重なる（I13）: {', '.join(hits)}")
    return errs


def plan_errors(view: PlanView, lanes: Lanes) -> list[str]:
    """計画の決まりに当たる箇所の一覧（I2・I13・I19・I20・I21 と after の先）。空なら通る。"""
    return _after_errors(view, lanes) + _limit_errors(view, lanes) + _issue_errors(view, lanes) + _touch_errors(view, lanes)


def _referenced(view: PlanView, lane: str) -> set[str]:
    """別のレーンの after が指す、このレーンの行の鍵。"""
    refs = (_target(a) for s in view.lanes.values() if s.name != lane for a in s.after)
    return {row for name, row in refs if name == lane and row is not None}


def _first_open(rows: list[LaneRow]) -> int:
    """書き換えてよい位置の先頭（実行中と完了と中止の行の後ろ）。"""
    done = [i for i, r in enumerate(rows) if r.state != PENDING]
    return done[-1] + 1 if done else 0


def change_errors(view: PlanView, rows: list[LaneRow], lane: str, draft: model.EventDraft) -> list[str]:
    """レーンの中の組み換え 1 件が、行の位置（I12a）と別のレーンの after が指す行（I18）を守るか。"""
    index = draft.payload.get("index") if draft.payload else None
    keys = [r.key for r in rows]
    errs = []
    if draft.kind in ("row_added", "row_moved") and index is not None and index < _first_open(rows):
        errs.append(f"レーン {lane} の位置 {index} は実行中か終わった行より前で、行の位置を書き換える（I12a）")
    if draft.row_key in _referenced(view, lane):
        moved_back = draft.kind == "row_moved" and (index is None or index > keys.index(draft.row_key))
        if draft.kind == "row_cancelled" or moved_back:
            errs.append(f"行 {lane}/{draft.row_key} は別のレーンの after が指していて、消せず後ろへも回せない（I18）")
    return errs


def next_row(view: PlanView, lanes: Lanes, lane: str) -> Optional[dict[str, Any]]:
    """レーンの次に始める行（最初の未着手の行）と、満たされていない after。無ければ None。"""
    rows = _rows_of(lanes, lane)
    if any(r.state == RUNNING for r in rows):
        return None
    pending = [r for r in rows if r.state == PENDING]
    if not pending:
        return None
    row = pending[0]
    waiting = [a for a in view.lanes[lane].after if not after_met(a, lanes)]
    return {"lane": lane, "row": row.key, "input": row.input, "waiting_after": waiting}


def wakeable_lanes(view: PlanView, lanes: Lanes) -> list[tuple[str, str]]:
    """実行中の行が無く、after を満たす未着手の行があるレーンを（レーン, 行の鍵）で、計画の並びの順に返す。"""
    if view.state != RUNNING:
        return []
    found = (next_row(view, lanes, name) for name in view.lanes)
    return [(n["lane"], n["row"]) for n in found if n is not None and not n["waiting_after"]]


def plan_complete(view: PlanView, lanes: Lanes) -> bool:
    """計画を完了にしてよいか（I16）: 全レーンの行が完了か中止で、答えていない依頼が無い。"""
    rows = [r for name in view.lanes for r in _rows_of(lanes, name)]
    return all(r.state in DONE_STATES for r in rows) and not view.open_requests()


def lane_state(view: PlanView, lanes: Lanes, lane: str) -> str:
    """レーンの状態（持たずに行から出す）: 実行中・未着手・after 待ち・完了・中止。"""
    rows = _rows_of(lanes, lane)
    if any(r.state == RUNNING for r in rows):
        return RUNNING
    nxt = next_row(view, lanes, lane)
    if nxt is not None:
        return WAITING_AFTER if nxt["waiting_after"] else PENDING
    return COMPLETED if any(r.state == COMPLETED for r in rows) else CANCELLED
