"""Burr（SQLitePersister の永続化）でプランを流す。

ステップ 1 つをアクション 1 つにし、行き先は state の cur を条件（when）にした遷移で表す。
承認ゲートは gate のアクションの前で halt_before で止め、承認で halt_before を外して続ける。
状態はアクションの終わりごとに SQLite へ書く（persister）。
"""
from __future__ import annotations

from burr.core import ApplicationBuilder, State, default, when
from burr.core.action import action
from burr.core.persistence import SQLitePersister

from rt_common import FIRST, LIMIT, PLAN_STEPS, STEP, Ctx, route

GATE, FINISH = "gate", "finish"


def build_app(ctx: Ctx):
    def make(sid: str):
        @action(reads=["visits", "count", "path"], writes=["visits", "count", "path", "cur", "status", "gate_to"])
        def step_action(state: State) -> State:
            visits = dict(state["visits"])
            v = visits.get(sid, 0)
            code, decision = ctx.exec_fake(sid, v)
            visits[sid] = v + 1
            count = state["count"] + 1
            kind, target = route(sid, code, decision)
            upd = {"visits": visits, "count": count, "path": [*state["path"], sid], "gate_to": ""}
            if kind == "gate":
                upd.update(cur=GATE, gate_to=target, status="gate")
            elif kind in ("end", "stop"):
                upd.update(cur=FINISH, status="done" if kind == "end" else "stopped")
            elif count >= LIMIT:
                upd.update(cur=FINISH, status="limit")
            else:
                upd.update(cur=target, status="running")
            return state.update(**upd)
        return step_action

    @action(reads=["gate_to"], writes=["cur", "status"])
    def gate(state: State) -> State:
        return state.update(cur=state["gate_to"], status="running")

    @action(reads=[], writes=[])
    def finish(state: State) -> State:
        return state

    actions = {sid: make(sid) for sid in STEP} | {GATE: gate, FINISH: finish}
    transitions = []
    for src in [*STEP, GATE]:
        for dst in [st["id"] for st in PLAN_STEPS] + [GATE, FINISH]:
            transitions.append((src, dst, when(cur=dst)))
    transitions.append((FINISH, FINISH, default))
    persister = SQLitePersister(db_path=str(ctx.state / "burr.sqlite"), table_name="burr_state",
                                connect_kwargs={"check_same_thread": False})
    persister.initialize()
    default_state = {"cur": FIRST, "visits": {}, "count": 0, "path": [], "status": "running", "gate_to": ""}
    return (ApplicationBuilder().with_actions(**actions).with_transitions(*transitions)
            .initialize_from(persister, resume_at_next_action=True, default_state=default_state,
                             default_entrypoint=FIRST)
            .with_state_persister(persister).with_identifiers(app_id=ctx.plan).build())


def run_burr(ctx: Ctx, approve: bool) -> dict:
    app = build_app(ctx)
    st = app.state
    if st["cur"] == FINISH and st["status"] in ("done", "stopped", "limit"):
        return {"plan": ctx.plan, "status": st["status"], "count": st["count"], "path": st["path"]}
    if st["path"]:
        ctx.event("resume", step=st["cur"])
    if st["cur"] == GATE and not approve:
        return ctx.finish("gate", st["path"][-1], st["count"], st["path"])
    last, _, state = app.run(halt_before=[] if approve else [GATE], halt_after=[FINISH])
    status = state["status"] if last is not None and last.name == FINISH else "gate"
    return ctx.finish(status, state["path"][-1], state["count"], state["path"])
