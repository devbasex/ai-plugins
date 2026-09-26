"""LangGraph（SqliteSaver のチェックポイント）でプランを流す。

ステップ 1 つをノード 1 つにし、行き先は rt_common.route を条件付きの辺にする。承認ゲートは
interrupt() で止め、Command(resume=...) で続ける。チェックポイントはノードの終わりごとに SQLite へ書く。
"""
from __future__ import annotations

import operator
import sqlite3
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from rt_common import FIRST, LIMIT, PLAN_STEPS, Ctx, route

GATE = "__gate__"


class PlanState(TypedDict, total=False):
    cur: str
    status: str
    gate_to: str
    count: int
    visits: dict
    path: Annotated[list, operator.add]


def graph_for(ctx: Ctx):
    def step_node(sid: str):
        def node(s: PlanState) -> dict:
            visits = dict(s.get("visits") or {})
            v = visits.get(sid, 0)
            code, decision = ctx.exec_fake(sid, v)
            visits[sid] = v + 1
            count = s.get("count", 0) + 1
            kind, target = route(sid, code, decision)
            upd = {"visits": visits, "count": count, "path": [sid]}
            if kind == "gate":
                return {**upd, "cur": GATE, "gate_to": target}
            if kind in ("end", "stop"):
                return {**upd, "cur": END, "status": "done" if kind == "end" else "stopped"}
            if count >= LIMIT:
                return {**upd, "cur": END, "status": "limit"}
            return {**upd, "cur": target}
        return node

    def gate_node(s: PlanState) -> dict:
        interrupt({"step": s["path"][-1]})  # 承認で Command(resume=True) が来るまで止まる（続けるとノードの頭から）
        return {"cur": s["gate_to"]}

    g = StateGraph(PlanState)
    targets = {st["id"]: st["id"] for st in PLAN_STEPS} | {GATE: GATE, END: END}
    for st in PLAN_STEPS:
        g.add_node(st["id"], step_node(st["id"]))
        g.add_conditional_edges(st["id"], lambda s: s["cur"], targets)
    g.add_node(GATE, gate_node)
    g.add_conditional_edges(GATE, lambda s: s["cur"], targets)
    g.add_edge(START, FIRST)
    conn = sqlite3.connect(str(ctx.state / "langgraph.sqlite"), check_same_thread=False)
    return g.compile(checkpointer=SqliteSaver(conn))


def run_langgraph(ctx: Ctx, approve: bool) -> dict:
    app = graph_for(ctx)
    cfg = {"configurable": {"thread_id": ctx.plan}, "recursion_limit": 200}
    snap = app.get_state(cfg)
    if not snap.values:
        arg = {"cur": FIRST, "count": 0, "visits": {}, "path": []}
    elif snap.next:
        pending = snap.next[0]
        if pending == GATE and not approve:
            return ctx.finish("gate", snap.values["path"][-1], snap.values.get("count", 0), snap.values["path"])
        arg = Command(resume=True) if pending == GATE else None  # None: 最後のチェックポイントから続ける
        ctx.event("resume", step=pending)
    else:
        v = snap.values
        return {"plan": ctx.plan, "status": v.get("status"), "count": v.get("count"), "path": v["path"]}
    out = app.invoke(arg, cfg)
    if "__interrupt__" in out:
        return ctx.finish("gate", out["path"][-1], out.get("count", 0), out["path"])
    return ctx.finish(out.get("status", "done"), out["path"][-1], out.get("count", 0), out["path"])
