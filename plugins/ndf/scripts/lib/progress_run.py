"""進行表のワークフロー（#3092 の決定 6・7・17・20・21）。DBOS は `lib/durable.py` の包みを通して使う。

`progress.py next` が前景で `run_next` を打つ。`run_next` は実行の鍵の排他（I14）を取り、進行表の DB を記録の
置き場にして DBOS を起動し、途中の実行を回復するか新しい実行を始めて、次のどれかまで流す。

- 有効な最後の工程が終わる: `completed` を書き、同じトランザクションでレーンの行を終える（決定 17）。`ok`
- 工程が失敗する・計画が中止された: `stopped`
- 承認ゲートかリリースの結果の待ちに入る: 実行を途中のまま残してプロセスを抜ける（`gate`。終了コード 10）

次の工程の選択は進行表のモデル（`progress_model.next_action`）が持ち、ここは事象を読む・書く・アクティビティを
流す殻だけを持つ。終わった行は事象から読んで飛ばし（決定 6）、NDF の版が上がって回復できない途中の実行は
取り消して、実行中の行を失敗にする（決定 21）。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import asdict
from typing import Any, Optional

import durable
import progress_activities as acts
import progress_db as pdb
import progress_model as pm
import workflow_def as wd

DURABLE_KIND = "progress"
TOOL = "progress"
_CTX: dict[str, Any] = {"url": None, "store": None, "gate": None}
_GATE = threading.Event()


def _store() -> pdb.Store:
    if _CTX["store"] is None:
        _CTX["store"] = pdb.connect(_CTX["url"])
    return _CTX["store"]


def _rows_dump(rows: dict) -> dict:
    return {k: {"state": r.state, "reason": r.reason, "payload": r.payload, "kind": r.kind, "at": r.at, "seq": r.seq} for k, r in rows.items()}


def _rows_load(d: dict) -> dict:
    return {k: pm.RowState(v["state"], v.get("seq", 0), v.get("at", ""), v.get("reason"), v.get("payload") or {}, v.get("kind", "")) for k, v in d.items()}


def plan_cancelled(conn, table_id: str) -> bool:
    link = pdb.sprint_link(conn, table_id)
    lane = pdb.table_row(conn, link[0]) if link else None
    if lane is None or not lane.parent_id:
        return False
    return pm.plan_view(pdb.read_events(conn, lane.parent_id))["state"] == pm.CANCELLED


def answered(conn, table_id: str, rev: int, row: str) -> Optional[dict]:
    """今の改訂のその行の最新の `answered` の事象の値（答え直した後は新しい答え）。"""
    got = [e for e in pdb.read_events(conn, table_id) if e.kind == "answered" and e.rev == rev and e.row_key == row]
    return dict(got[-1].payload or {}) if got else None


# --- 耐久ステップ ---------------------------------------------------------------


@durable.step(name="ndf.progress.definition")
def step_definition(table_id: str, rev: int) -> dict:
    with pdb.transaction(_store()) as conn:
        return pdb.definition(conn, table_id, rev)[1]


@durable.step(name="ndf.progress.rows")
def step_rows(table_id: str, rev: int) -> dict:
    """今の改訂の行の状態と、計画の中止を 1 つの耐久ステップで読む。"""
    with pdb.transaction(_store()) as conn:
        rows = pm.fold(pdb.read_events(conn, table_id), rev)
        return {"rows": _rows_dump(rows), "plan_cancelled": plan_cancelled(conn, table_id)}


@durable.step(name="ndf.progress.append")
def step_append(table_id: str, drafts: list[dict]) -> dict:
    """事象を追記し、進行表の `completed` があれば同じトランザクションでレーンの行を終える（決定 17）。"""
    for attempt in range(3):
        try:
            with pdb.transaction(_store()) as conn:
                ds = [pm.EventDraft(**d) for d in drafts]
                seqs = pdb.append_events(conn, table_id, ds)
                nr = None
                if any(d.kind == "completed" for d in ds):
                    got = pdb.end_lane_row(conn, table_id, "completed", "workflow")
                    nr = got["next_row"] if got else None
                return {"seqs": seqs, "next_row": nr}
        except pdb.Conflict:
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


@durable.step(name="ndf.progress.answered")
def step_answered(table_id: str, rev: int, row: str) -> Optional[dict]:
    with pdb.transaction(_store()) as conn:
        return answered(conn, table_id, rev, row)


@durable.step(name="ndf.progress.activity")
def step_activity(table_id: str, rev: int, act: dict, inp: dict, answer: Optional[dict]) -> dict:
    """アクティビティを流し、出力を規約で検査する（I3）。承認と判断のゲートの `gate` は検査しない。"""
    a = wd.ActivityDef(**act)
    out = acts.run_activity(_store(), table_id, rev, a, inp, answer)
    with pdb.transaction(_store()) as conn:
        snap = wd.Snapshot.from_dict(pdb.definition(conn, table_id, rev)[1]["snapshot"])
    errs = [] if out.get("status") == "gate" else wd.validate_io(snap, a.output_ref, out)
    return {"output": out, "errors": errs}


@durable.step(name="ndf.progress.record")
def step_record(table_id: str, row: str, phase: str) -> dict:
    return acts.act_record(_store(), table_id, row, phase)


# --- 待ち --------------------------------------------------------------------


def _answer_now(table_id: str, rev: int, row: str) -> bool:
    """待ちに入る直前に DB を読む（記録に残さない。主のスレッドへ知らせるかだけを決める）。"""
    with pdb.transaction(_store()) as conn:
        return answered(conn, table_id, rev, row) is not None


def _wait_answer(table_id: str, rev: int, row: str, info: dict) -> Optional[dict]:
    """答えを受信で待つ。答えがまだ無ければ主のスレッドへ待ちを知らせる（決定 7）。期限を過ぎたら None。"""
    if not _answer_now(table_id, rev, row):
        _CTX["gate"] = {"row": row, **info}
        _GATE.set()
    got = durable.recv(topic_of(row), durable.PAUSE_SECONDS)
    return got if isinstance(got, dict) else None


def topic_of(row: str) -> str:
    return f"answer:{row}"


# --- ワークフロー --------------------------------------------------------------


def _draft(wid: str, rev: int, kind: str, row: Optional[str] = None, **kw) -> dict:
    return {"kind": kind, "actor": "workflow", "row_key": row, "rev": rev, "dedupe_key": f"{wid}:{row or ''}:{kind}", **kw}


def _run_activities(ctx: dict, stage, child: Optional[str], rows: dict) -> tuple[str, Any]:
    tid, rev, wid, raw = ctx["table"], ctx["rev"], ctx["wid"], ctx["raw"]
    outputs: dict[str, dict] = {}
    for act in stage.activities:
        key = f"{child or stage.name}/{act.id}"
        r = rows.get(key)
        if r is not None and r.state == pm.FINISHED:
            outputs[act.id] = (r.payload or {}).get("output") or {}
            continue
        if r is None or r.state != pm.RUNNING:
            step_append(tid, [_draft(wid, rev, "started", key)])
        inp = {"table": tid, "stage": stage.name, "issues": raw.get("issues") or [], "mode": raw["snapshot"]["mode"],
               "pace": raw.get("pace", "normal"), "cwd": raw.get("cwd"), "work": raw.get("work"), "previous": outputs,
               "reshuffle": raw.get("reshuffle") or {}}
        if child:
            inp["issue"] = int(child.split("#", 1)[1])
        answer = step_answered(tid, rev, key)
        if act.call == wd.LISTEN_CALL and answer is None:
            answer = _wait_answer(tid, rev, key, {"stage": stage.name, "kind": act.kind})
        res = step_activity(tid, rev, asdict(act), inp, answer)
        out = res["output"]
        if out.get("status") == "gate":  # プランの中の承認と判断のゲート（詳細設計の act_plan の行）
            step_ = (out.get("metrics") or {}).get("gate_step")
            got = answer or _wait_answer(tid, rev, key, {"stage": stage.name, "kind": "plan-gate", "step": step_})
            if got is None or got.get("answer") == "rejected":
                return "failed", f"{key}: プランの承認ゲートの答えが無いか却下（{(got or {}).get('answer')}）"
            inp["answered"] = {**got, "from": {"plan": (out.get("metrics") or {}).get("plan"), "step": step_}}
            res = step_activity(tid, rev, asdict(act), inp, got)
            out = res["output"]
        if res["errors"] or out.get("status") != "ok":
            why = "; ".join(res["errors"]) if res["errors"] else out.get("summary", "")
            step_append(tid, [_draft(wid, rev, "failed", key, reason=why[:500], payload={"output": out})])
            return "failed", f"{key}: {why}"[:500]
        step_append(tid, [_draft(wid, rev, "finished", key, payload={"output": out})])
        outputs[act.id] = out
    return "ok", outputs


def _run_stage(ctx: dict, stage, children: tuple, rows: dict) -> tuple[str, Any]:
    if not children:
        return _run_activities(ctx, stage, None, rows)
    tid, rev, wid = ctx["table"], ctx["rev"], ctx["wid"]
    merged: dict[str, dict] = {}
    for child in children:
        r = rows.get(child)
        if r is not None and r.state == pm.FINISHED:
            continue
        if r is None or r.state != pm.RUNNING:
            step_append(tid, [_draft(wid, rev, "started", child)])
        status, got = _run_activities(ctx, stage, child, rows)
        if status != "ok":
            step_append(tid, [_draft(wid, rev, "failed", child, reason=got)])
            return status, got
        step_append(tid, [_draft(wid, rev, "finished", child)])
        merged.update({f"{child}/{k}": v for k, v in got.items()})
    return "ok", merged


def _stage_output(outputs: dict) -> dict:
    metrics: dict = {}
    for out in outputs.values():
        metrics.update((out or {}).get("metrics") or {})
    summary = " / ".join(f"{k}: {(v or {}).get('summary', '')}" for k, v in outputs.items())[:500]
    return {"activities": outputs, "summary": summary, "metrics": metrics}


@durable.workflow(name="ndf.progress.sprint")
def sprint_workflow(table_id: str, rev: int, stale: Optional[dict]) -> dict:
    """定義のスナップショットに沿って工程を 1 つずつ流す。終わった行は流し直さない。"""
    wid = durable.current_id()
    step_append(table_id, [_draft(wid, rev, "execution", payload={"wid": wid})])
    raw = step_definition(table_id, rev)
    snap = wd.Snapshot.from_dict(raw["snapshot"])
    enabled = {k: wd.Enablement(**v) for k, v in (raw.get("enabled") or {}).items()}
    ctx = {"table": table_id, "rev": rev, "wid": wid, "raw": raw}
    if stale:  # 決定 21: 版が替わって回復できない途中の実行の行を失敗にする
        rows = _rows_load(step_rows(table_id, rev)["rows"])
        running = sorted(k for k, r in rows.items() if r.state == pm.RUNNING)
        why = f"版が替わり、途中の実行を回復できない（古い実行 {stale['wid']}・版 {stale['old']} → {stale['new']}）"
        step_append(table_id, [_draft(wid, rev, "failed", k, reason=why) for k in running])
        return {"status": "stopped", "failed": running, "reason": why}
    issues = tuple(raw.get("issues") or ())
    pace = raw.get("pace") or "normal"
    for _ in range(len(snap.stages) * 3 + 5):  # 1 周で行を 1 つ終えるか飛ばす
        st = step_rows(table_id, rev)
        rows = _rows_load(st["rows"])
        en = {**enabled, **{k: wd.Enablement(True, None, "sprint") for k, r in rows.items() if r.kind == "enabled"}}
        a = pm.next_action(snap, rows, en, st["plan_cancelled"], pace, issues)
        if a.kind == "complete":
            got = step_append(table_id, [_draft(wid, rev, "completed")])
            return {"status": "ok", "next_row": got["next_row"]}
        if a.kind == "stop_plan_cancelled":
            return {"status": "stopped", "plan_cancelled": True, "reason": a.reason}
        if a.kind == "stop_failed":
            return {"status": "stopped", "failed": [a.row], "reason": a.reason}
        if a.kind == "skip":
            step_append(table_id, [_draft(wid, rev, "skipped", a.row, reason=a.reason)])
            continue
        stage = snap.stage(a.row)
        children = tuple(f"{stage.name}#{n}" for n in issues) if stage.per_issue and pace == "fast" and issues else ()
        if a.kind != "resume":
            step_append(table_id, [_draft(wid, rev, "started", a.row)])
        step_record(table_id, a.row, "start")
        status, got = _run_stage(ctx, stage, children, rows)
        if status != "ok":
            step_append(table_id, [_draft(wid, rev, "failed", a.row, reason=str(got)[:500])])
            return {"status": "stopped", "failed": [a.row], "reason": got}
        out = _stage_output(got)
        errs = wd.validate_io(snap, stage.output_ref, {"activities": out["activities"], "summary": out["summary"]})
        if errs:
            step_append(table_id, [_draft(wid, rev, "failed", a.row, reason="; ".join(errs)[:500])])
            return {"status": "stopped", "failed": [a.row], "reason": "; ".join(errs)}
        step_append(table_id, [_draft(wid, rev, "finished", a.row, payload={"summary": out["summary"], "metrics": out["metrics"]})])
        step_record(table_id, a.row, "finish")
    return {"status": "stopped", "reason": "繰り返しの上限に当たった"}


# --- 前景の起動 ------------------------------------------------------------------


def prefix_of(table_id: str) -> str:
    return f"progress-{durable.key_hash(table_id)}"


def stale_execution(store: pdb.Store, wids: list[str], version: str) -> Optional[dict]:
    """最新の実行が、版が違う途中の実行なら（ID・古い版・新しい版）を返す（決定 21）。"""
    if not wids:
        return None
    got = durable.workflow_version_from(store.url, wids[-1])
    if got and got[0] in durable.RUNNING and got[1] != version:
        return {"wid": wids[-1], "old": got[1], "new": version}
    return None


def report(status: str, summary: str, items: Optional[list] = None, **metrics) -> dict:
    return {"tool": TOOL, "status": status, "summary": summary, "items": items or [], "metrics": metrics}


def _finish(table_id: str, out: dict) -> tuple[dict, int]:
    if out.get("status") == "ok":
        return report("ok", "スプリントの進行表が完了した", table=table_id, next_row=out.get("next_row")), 0
    if out.get("plan_cancelled"):
        return report("stopped", "計画が中止された。progress.py cancel を打つ", table=table_id, plan_cancelled=True), 3
    items = [{"row": r, "kind": "failed"} for r in out.get("failed") or []]
    return report("stopped", f"工程が失敗した: {out.get('reason')}", items, table=table_id), 1


def _gate_exit(table_id: str) -> None:
    info = _CTX["gate"] or {}
    rep = report("gate", f"{info.get('row')} が答えを待っている（progress.py answer の後に next を打つ）", [{"kind": "gate", **info}], table=table_id)
    print(json.dumps(rep, ensure_ascii=False), flush=True)
    durable.exit_leaving_pending(10)


def run_next(store: pdb.Store, table_id: str, poll: float = 0.2) -> tuple[dict, int]:
    """排他を取り、古い実行を始末し、進行表のワークフローを回復か新しく始めて流す（決定 6・7・21）。"""
    with pdb.run_lock(store, table_id):
        with pdb.transaction(store) as conn:
            row = pdb.table_row(conn, table_id)
            if row is None or row.level != "sprint":
                raise pdb.Unreadable(f"スプリントの進行表 {table_id} が無い")
            rev, _ = pdb.definition(conn, table_id)
            state = pm.table_state(pm.fold(pdb.read_events(conn, table_id), rev))
        if state == pm.COMPLETED:
            return report("ok", "スプリントの進行表はもう完了している", table=table_id, next_row=None), 0
        if state == pm.CANCELLED:
            return report("stopped", "スプリントの進行表は中止されている", table=table_id), 3
        _CTX.update(url=store.url, store=store, gate=None)
        _GATE.clear()
        launched = durable.launch(DURABLE_KIND, table_id, url=store.url, app_version=None)
        try:
            prefix = prefix_of(table_id)
            wids = durable.workflow_ids(prefix + "-")
            stale = stale_execution(store, wids, launched.version)
            if stale:
                durable.cancel_from(store.url, stale["wid"])
            last = durable.status(wids[-1]) if wids else None
            if wids and last in durable.RUNNING and not stale:
                wid = wids[-1]  # 起動の回復に任せる（決定 7）
            else:
                n = len(wids) + 1
                wid = durable.start(durable.WorkflowRef(f"{prefix}-{n}", n, "start"), sprint_workflow, table_id, rev, stale)
            while True:
                if _GATE.is_set():
                    _gate_exit(table_id)
                if durable.status(wid) in durable.FINISHED:
                    break
                time.sleep(poll)
            try:
                out = durable.output_of(wid)
            except Exception as e:  # noqa: BLE001  耐久ワークフローの失敗を結果の文にする
                code = 3 if "Refused" in type(e).__name__ or "I12" in str(e) or "I18" in str(e) else 1
                return report("stopped", f"進行表のワークフローが落ちた: {e}", table=table_id), code
            return _finish(table_id, out)
        finally:
            durable.close()


def send_answer(store: pdb.Store, table_id: str, row: str, value: dict) -> bool:
    """途中の実行があれば答えを送る。送れなければ偽（`answered` の事象が残るため失われない）。"""
    try:
        wids = []
        with pdb.transaction(store) as conn:
            wids = [e.payload.get("wid") for e in pdb.read_events(conn, table_id) if e.kind == "execution" and e.payload]
        if not wids:
            return False
        st = durable.status_from("", "", wids[-1], url=store.url)
        if st not in durable.RUNNING:
            return False
        durable.send_to("", "", wids[-1], value, topic_of(row), f"{wids[-1]}:{row}:{json.dumps(value, sort_keys=True)}", url=store.url)
        return True
    except durable.DurableError as e:
        print(f"[ndf progress] 答えを送れない（answered だけ書いた）: {e}", file=sys.stderr)
        return False
