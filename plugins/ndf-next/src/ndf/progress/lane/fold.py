"""進行表の事象の畳み込み（#3097）: 計画の進行表とレーンの進行表の事象から、計画の配列とレーンの順番表を求める。

どれも純粋な関数で、DB を読まない。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any, Optional

from ndf.progress import model
from ndf.progress.lane.types import PENDING, PLAN_ROW, LaneRow, LaneSpec, PlanView, Request


def _spec(name: str, body: Mapping[str, Any]) -> LaneSpec:
    return LaneSpec(
        name=name,
        table_id=body["table_id"],
        kind=body["kind"],
        plans=int(body["plans"]),
        touches=tuple(body["touches"]),
        after=tuple(body["after"]),
        repo=body.get("repo"),
    )


def _changed_spec(spec: LaneSpec, body: Mapping[str, Any]) -> LaneSpec:
    changes = {k: (tuple(v) if isinstance(v, list) else v) for k, v in body.items() if k in ("plans", "touches", "after")}
    return replace(spec, **changes)


def _plan_base(table_id: str, body: Mapping[str, Any], state: str) -> PlanView:
    return PlanView(
        table_id=table_id,
        name=body["name"],
        state=state,
        rules=tuple(body["rules"]),
        max_lanes=int(body["max_lanes"]),
        max_plans=int(body["max_plans"]),
        formats=dict(body["formats"]),
        ndf_version=body.get("ndf_version"),
    )


def _plan_limits(view: PlanView, body: Mapping[str, Any]) -> PlanView:
    changes = {k: int(v) for k, v in body.items() if k in ("max_lanes", "max_plans")}
    return replace(view, **changes)


def _fold_plan_event(view: PlanView, d: model.EventDraft) -> PlanView:
    body, key = d.payload, d.row_key
    if d.kind == "lane_added":
        return replace(view, lanes={**view.lanes, key: _spec(key, body)})
    if d.kind == "lane_changed" and key == PLAN_ROW:
        return _plan_limits(view, body)
    if d.kind == "lane_changed":
        return replace(view, lanes={**view.lanes, key: _changed_spec(view.lanes[key], body)})
    if d.kind == "request_opened":
        req = Request(key, body.get("lane"), dict(body["body"]), d.reason)
        return replace(view, requests={**view.requests, key: req})
    if d.kind == "request_closed":
        return replace(view, requests={**view.requests, key: replace(view.requests[key], closed=d.reason)})
    return view


def fold_plan(table_id: str, events: list[model.Event], repo: Optional[str] = None) -> PlanView:
    """計画の進行表の事象から、レーンの配列・規則・上限・依頼・計画の状態を求める。`repo` は計画のリポジトリ。"""
    created = [e for e in events if e.draft.kind == "created" and e.draft.row_key == PLAN_ROW]
    if not created:
        raise model.Refused(f"計画の進行表 {table_id} に計画の行が無い")
    state = model.table_state("plan", events)
    view = replace(_plan_base(table_id, created[0].draft.payload, state), repo=repo)
    for ev in events:
        view = _fold_plan_event(view, ev.draft)
    return view


def _row_from(rows: dict[str, LaneRow], ev: model.Event) -> Optional[LaneRow]:
    """1 件の事象を当てた後の行。行の位置を変えない事象だけを扱う（位置は `fold_lane` が持つ）。"""
    d = ev.draft
    row = rows.get(d.row_key)
    if d.kind == "row_added":
        return LaneRow(d.row_key, dict(d.payload["input"]), PENDING, reason=d.reason)
    if row is None:
        return None
    if d.kind == "row_answered":
        return replace(row, answered_seq=ev.seq, answered_at=ev.at)
    if d.kind == "row_resumed":
        return replace(row, resumed_seq=ev.seq)
    sprint = d.payload.get("sprint_id") if d.payload else None
    if d.state is None:
        return row
    return replace(row, state=d.state, sprint_id=sprint if sprint is not None else row.sprint_id, reason=d.reason)


def _placed(order: list[str], key: str, index: Optional[int]) -> list[str]:
    rest = [k for k in order if k != key]
    at = len(rest) if index is None else max(0, min(index, len(rest)))
    return [*rest[:at], key, *rest[at:]]


def fold_lane(events: Iterable[model.Event]) -> list[LaneRow]:
    """レーンの進行表の事象から、順番表の行を並びの順に求める。"""
    rows: dict[str, LaneRow] = {}
    order: list[str] = []
    for ev in events:
        d = ev.draft
        if d.kind in ("row_added", "row_moved"):
            order = _placed(order, d.row_key, d.payload.get("index"))
        got = _row_from(rows, ev)
        if got is not None:
            rows[d.row_key] = got
    return [rows[k] for k in order]
