"""DBOS Transact（SQLite のシステムデータベース）でプランとキューを流す。

プラン 1 本をワークフロー 1 つにし、ステップの遷移は Python の while と rt_common.route で書く。
ステップの結果は @DBOS.step が SQLite へ記録し、落ちた後の DBOS.launch() が記録を読み直して
（記録のあるステップは流し直さずに）続ける。承認ゲートは DBOS.recv で待ち、DBOS.send で続ける。
キューは DBOS の Queue（同時の本数は worker_concurrency、資源のタグは別のキューの concurrency）。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dbos import DBOS, SetWorkflowID

from rt_common import FIRST, LIMIT, STEP, Ctx, route

DONE = ("SUCCESS", "ERROR", "CANCELLED")
Q: dict = {}  # launch() が登録する: plans（同時 2 本）・res-graphql（資源のタグ graphql の同時 1 本）
_CTX: dict[str, Ctx] = {}
_TAGGED = {"on": False}  # キューの試しのときだけ、資源のタグのあるステップを Q の資源のキューへ回す


def ctx_of(plan: str, state: str, scenario: str) -> Ctx:
    if plan not in _CTX:
        _CTX[plan] = Ctx(Path(state), plan, scenario)
    return _CTX[plan]


def launch(db: Path) -> None:
    DBOS(config={"name": "runner-trial", "system_database_url": f"sqlite:///{db}", "log_level": "WARNING"})
    DBOS.launch()
    # DBOS 3 は Queue(...) を直に作れない。登録すると設定がシステムデータベースに残る
    Q["plans"] = DBOS.register_queue("plans", worker_concurrency=2, polling_interval_sec=0.1)
    Q["graphql"] = DBOS.register_queue("res-graphql", concurrency=1, polling_interval_sec=0.1)


@DBOS.step()
def fake_step(plan: str, state: str, scenario: str, sid: str, visit: int) -> list:
    return list(ctx_of(plan, state, scenario).exec_fake(sid, visit, use_slots=False))


@DBOS.workflow()
def tagged_step(plan: str, state: str, scenario: str, sid: str, visit: int) -> list:
    return fake_step(plan, state, scenario, sid, visit)


@DBOS.step()
def gate_note(plan: str, state: str, scenario: str, sid: str, count: int, path: list) -> None:
    ctx_of(plan, state, scenario).finish("gate", sid, count, path)


@DBOS.step()
def finish_note(plan: str, state: str, scenario: str, status: str, sid: str, count: int, path: list) -> dict:
    return ctx_of(plan, state, scenario).finish(status, sid, count, path)


@DBOS.workflow()
def plan_wf(plan: str, state: str, scenario: str) -> dict:
    visits: dict[str, int] = {}
    count, path, cur, gates = 0, [], FIRST, 0
    while True:
        v = visits.get(cur, 0)
        tags = STEP[cur].get("resources") or []
        if _TAGGED["on"] and tags:
            code, decision = Q[tags[0]].enqueue(tagged_step, plan, state, scenario, cur, v).get_result()
        else:
            code, decision = fake_step(plan, state, scenario, cur, v)
        visits[cur] = v + 1
        count += 1
        path.append(cur)
        kind, target = route(cur, code, decision)
        if kind == "gate":
            gates += 1
            gate_note(plan, state, scenario, cur, count, path)
            DBOS.set_event("gate_seq", gates)
            DBOS.recv("approve", timeout_seconds=7 * 86400)
            cur = target
            continue
        if kind in ("end", "stop") or count >= LIMIT:
            status = {"end": "done", "stop": "stopped"}.get(kind, "limit")
            return finish_note(plan, state, scenario, status, cur, count, path)
        cur = target


def wait_handle(wid: str, handle, gate_seq: int) -> tuple[dict, bool]:
    """ワークフローの終わりか、次の承認ゲートまで待つ。(結果, 承認ゲートで止まったか)。"""
    while True:
        st = handle.get_status().status
        if st in DONE:
            return handle.get_result(), False
        seq = DBOS.get_event(wid, "gate_seq", timeout_seconds=0) or 0
        if seq > gate_seq:
            return {"status": "gate"}, True
        time.sleep(0.05)


def run_dbos(ctx: Ctx, approve: bool) -> dict:
    """同じ入口で打ち直すと、DBOS.launch() が落ちたワークフローを記録から続ける。承認ゲートでは
    ワークフローを recv の待ちのまま残してプロセスを抜ける（os._exit。呼ぶ側が出力を済ませてから）。"""
    launch(ctx.state / "dbos.sqlite")
    wid = ctx.plan
    status = DBOS.get_workflow_status(wid)
    if status is None:
        with SetWorkflowID(wid):
            handle = DBOS.start_workflow(plan_wf, ctx.plan, str(ctx.state), ctx.scenario)
    else:
        ctx.event("resume", step=status.status)
        handle = DBOS.retrieve_workflow(wid)
    seq = DBOS.get_event(wid, "gate_seq", timeout_seconds=0) or 0
    if approve and seq:
        DBOS.send(wid, True, "approve")
    elif seq and handle.get_status().status not in DONE:  # 落ちた後の打ち直しでは ENQUEUED に見える
        return {"plan": ctx.plan, "status": "gate", "exit_now": True}
    out, gated = wait_handle(wid, handle, seq)
    if gated:
        return {"plan": ctx.plan, "status": "gate", "exit_now": True}
    return out


@DBOS.workflow()
def queue_wf(stages: list, root: str) -> list:
    """ステージの順にプランを Q["plans"] へ入れる。前のステージがすべて done のときだけ次を入れる。"""
    results = []
    for i, stage in enumerate(stages):
        handles = [Q["plans"].enqueue(plan_wf, p["plan"], str(Path(root) / f"{p['plan']}-state"), p["scenario"])
                   for p in stage]
        outs = [h.get_result() for h in handles]
        results.append({"stage": i, "outcomes": outs})
        if any(o.get("status") != "done" for o in outs):
            results.append({"stage": i + 1, "skipped": [p["plan"] for s in stages[i + 1:] for p in s]})
            break
    return results


def run_queue(root: Path, stages: list) -> list:
    _TAGGED["on"] = True
    launch(root / "dbos.sqlite")
    with SetWorkflowID("queue-" + root.name):
        handle = DBOS.start_workflow(queue_wf, stages, str(root))
    out = handle.get_result()
    sys.stdout.flush()
    return out


def hard_exit(code: int) -> None:
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
