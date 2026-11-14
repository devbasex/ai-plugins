"""計画の進行表（#3097）と副命令 `ndf lanes`: 開発計画のレーンの配列とレーンをまたぐ決まりを持つ。

ndf lanes init <plan.json>                       計画の検査を通し、計画の進行表と各レーンの進行表を未着手で作る
ndf lanes start <plan.json>                      init と同じに作り（作ってあれば読み）、計画を始める
ndf lanes status <計画>                          計画とレーンの進行表から計画の状態を出す
ndf lanes lane add <計画> <lane.json> [--request <依頼>]
ndf lanes set <計画> <レーン> [--plans <本数>] [--touches <場所>...] [--after <先>...] --reason <理由> [--request <依頼>]
ndf lanes limits <計画> [--max-lanes <数>] [--max-plans <本数>] --reason <理由>
ndf lanes move <計画> <元のレーン> <行> <先のレーン> --reason <理由>
ndf lanes stop <計画> --reason <理由>
ndf lanes request close <計画> <依頼> --reason <理由>
ndf lanes answered <スプリントの進行表の ID> --answer <答え>
ndf lanes row add|move|cancel|carry|start|end|resumed ...   （レーンの conductor が打つ。レーンの進行表）

書き手は段ごとに 1 つに寄る。計画の進行表へ書くのはこのモジュールの関数だけで、副命令の書き手はオーガナイザー
（作成・レーンをまたぐ変更・中止・依頼を閉じる・答えの記録）になる。完了（`complete`）は耐久ワークフローが呼ぶ。
`row` の副命令はレーンの進行表（`ndf.progress.lane`）を書き、書き手はレーンの conductor になる。
`plan.json` は入力として読むだけで、計画の状態は書かない。結果は結果 JSON の 1 行で返す。
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

import sqlalchemy as sa

from ndf import cli, contracts, identity
from ndf.adapters.git import GitError
from ndf.contracts import causes, limits, result
from ndf.core import durable
from ndf.progress import command, lane, model, store

TOOL = cli.tool_of(__name__)
# 計画の宣言とレーンの宣言の形（レーンは同じファイルの $defs）
PLAN_SCHEMA = "workflow/parallel-plan.schema.json"
LANE_SCHEMA = PLAN_SCHEMA + "#/$defs/lane"
# 同時に動くレーンの上限とレーンごとのプランの本数の上限の既定（計画の宣言の max_lanes・max_plans で変える）
LIMIT_POLICY = ("relay.max-named", "count")
# 答えが載ってから conductor が next を打たないまま、止まりとして知らせるまでの秒
ANSWER_NEXT_GRACE_SECONDS = 600


class PlanInputError(contracts.ContractError):
    """計画の宣言（plan.json）かレーンの宣言が読めない・形が違う。"""


# --- 宣言の読み込み ------------------------------------------------------------------------------


def read_doc(path: Path, schema: str, label: str) -> dict[str, Any]:
    """宣言の JSON を読み、形を確かめて返す。"""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PlanInputError(f"{label} {path} を JSON として読めない: {exc}") from exc
    errs = contracts.schema_errors(doc, schema)
    if errs:
        raise PlanInputError(f"{label} {path} の形が違う: {'; '.join(errs)}")
    return doc


def lane_inputs(decl: dict[str, Any]) -> list[dict[str, Any]]:
    """レーンの宣言の最初の順番表（`rows`、`input` だけなら 1 行）。"""
    return list(decl["rows"]) if "rows" in decl else [decl["input"]]


def _lane_kind(decl: dict[str, Any]) -> str:
    return lane_inputs(decl)[0]["kind"]


def lane_payload(decl: dict[str, Any], table_id: str) -> dict[str, Any]:
    """計画の進行表のレーンの行（`lane_added` の値）。"""
    body = {"table_id": table_id, "kind": _lane_kind(decl), "plans": decl["plans"], "touches": decl["touches"]}
    body["after"] = list(decl["after"]) if "after" in decl else []
    if "repo" in decl:
        body["repo"] = decl["repo"]
    return body


def _default_limit() -> int:
    return int(limits.value(*LIMIT_POLICY))


def plan_payload(doc: dict[str, Any]) -> dict[str, Any]:
    """計画そのものの行（規則・上限・形の番号・NDF の版）。"""
    default = _default_limit()
    return {
        "name": doc["name"],
        "rules": doc["rules"],
        "max_lanes": doc["max_lanes"] if "max_lanes" in doc else default,
        "max_plans": doc["max_plans"] if "max_plans" in doc else default,
        "formats": lane.current_formats(),
        "ndf_version": lane.ndf_version(),
    }


# --- 計画の進行表の読み書き（オーガナイザーと耐久ワークフロー） -----------------------------------------


def plan_id(repo: str, name: str) -> str:
    return model.table_id("plan", repo, name)


def _append(conn: sa.Connection, pid: str, drafts: list[model.EventDraft]) -> list[int]:
    events = store.read_events(conn, pid)
    return store.append_events(conn, pid, drafts, events[-1].seq if events else 0)


def _refuse_if(errs: list[str]) -> None:
    if errs:
        raise model.Refused("; ".join(errs))


def _new_errors(conn: sa.Connection, pid: str, before: set[str]) -> list[str]:
    view, _ = lane.read_plan(conn, pid)
    return [e for e in lane.plan_errors(view, lane.folded(lane.read_lanes(conn, view))) if e not in before]


def _errors_now(conn: sa.Connection, pid: str) -> set[str]:
    view, _ = lane.read_plan(conn, pid)
    return set(lane.plan_errors(view, lane.folded(lane.read_lanes(conn, view))))


def create_plan(conn: sa.Connection, repo: str, doc: dict[str, Any], start: bool) -> str:
    """計画の進行表と各レーンの進行表（最初の順番表）を 1 つのトランザクションで作る。決まりに当たれば何も残さない。"""
    name = doc["name"]
    pid = plan_id(repo, name)
    names = [d["name"] for d in doc["lanes"]]
    dup = sorted({n for n in names if names.count(n) > 1})
    _refuse_if([f"レーン名が重なる: {', '.join(dup)}"] if dup else [])
    lane_ids = {d["name"]: model.table_id("lane", repo, lane.lane_table_name(name, d["name"])) for d in doc["lanes"]}
    drafts = [model.EventDraft("created", "organizer", lane.PLAN_ROW, payload=plan_payload(doc))]
    drafts += [model.EventDraft("lane_added", "organizer", d["name"], payload=lane_payload(d, lane_ids[d["name"]])) for d in doc["lanes"]]
    drafts += [model.EventDraft("plan_started", "organizer")] if start else []
    store.create_table(conn, ("plan", repo, name), None, None, drafts)
    for d in doc["lanes"]:
        lane.create_lane(conn, (repo, name, d["name"]), pid, lane_inputs(d))
    _refuse_if(_new_errors(conn, pid, set()))
    return pid


def start_plan(conn: sa.Connection, pid: str) -> None:
    view, _ = lane.read_plan(conn, pid)
    if view.state != lane.PENDING:
        raise model.Refused(f"計画 {view.name} は{model.label(view.state)}で、始められない", current_id=pid)
    _append(conn, pid, [model.EventDraft("plan_started", "organizer")])


def _close_request(request: Optional[str], reason: str) -> list[model.EventDraft]:
    return [model.EventDraft("request_closed", "organizer", request, reason=reason)] if request else []


def _check_request(conn: sa.Connection, pid: str, request: Optional[str]) -> None:
    if request is None:
        return
    view, _ = lane.read_plan(conn, pid)
    found = view.requests.get(request)
    if found is None or found.closed is not None:
        raise model.Refused(f"計画 {view.name} に開いている依頼 {request} は無い")


def add_lane(conn: sa.Connection, pid: str, decl: dict[str, Any], request: Optional[str] = None) -> str:
    """動いている計画へレーンを足す（オーガナイザー）。応えた依頼があれば同じトランザクションで閉じる。"""
    view, _ = lane.read_plan(conn, pid)
    meta = store.read_table(conn, pid)
    if decl["name"] in view.lanes:
        raise model.Refused(f"計画 {view.name} にレーン {decl['name']} は既にある")
    _check_request(conn, pid, request)
    before = _errors_now(conn, pid)
    tid = model.table_id("lane", meta["repo"], lane.lane_table_name(view.name, decl["name"]))
    drafts = [model.EventDraft("lane_added", "organizer", decl["name"], payload=lane_payload(decl, tid))]
    _append(conn, pid, drafts + _close_request(request, f"レーン {decl['name']} を足した"))
    lane.create_lane(conn, (meta["repo"], view.name, decl["name"]), pid, lane_inputs(decl))
    _refuse_if(_new_errors(conn, pid, before))
    return tid


def change_lane(conn: sa.Connection, pid: str, name: str, changes: dict[str, Any], reason: str, request: Optional[str] = None) -> None:
    """レーンの触る場所・プランの本数・after を変える（name が計画の行なら上限を変える）。決まりで新しく当たれば断る。"""
    view, _ = lane.read_plan(conn, pid)
    if name != lane.PLAN_ROW and name not in view.lanes:
        raise model.Refused(f"計画 {view.name} にレーン {name} は無い")
    if not changes:
        raise model.Refused("変える値が無い")
    _check_request(conn, pid, request)
    before = _errors_now(conn, pid)
    drafts = [model.EventDraft("lane_changed", "organizer", name, reason=reason, payload=changes)]
    _append(conn, pid, drafts + _close_request(request, reason))
    _refuse_if(_new_errors(conn, pid, before))


def move_row(conn: sa.Connection, pid: str, src: tuple[str, str], dest: str, reason: str) -> str:
    """課題の行を別のレーンへ移す（オーガナイザー）。移す先へはレーンの進行表の関数で行を足す。足した行の鍵を返す。"""
    from_lane, key = src
    view, _ = lane.read_plan(conn, pid)
    rows = lane.folded(lane.read_lanes(conn, view))
    if from_lane not in rows or dest not in rows:
        raise model.Refused(f"計画 {view.name} にレーン {from_lane} か {dest} は無い")
    row = next((r for r in rows[from_lane] if r.key == key), None)
    if row is None:
        raise model.Refused(f"レーン {from_lane} に行 {key} は無い")
    lane.cancel_row(conn, pid, from_lane, key, lane.RowChange("organizer", f"レーン {dest} へ移す: {reason}"))
    return lane.add_row(conn, pid, dest, row.input, lane.RowChange("organizer", f"レーン {from_lane} の行 {key} から移した: {reason}"))


def stop_plan(conn: sa.Connection, pid: str, reason: str) -> list[str]:
    """計画を中止する（オーガナイザー）。未着手の行を中止にし、実行中の行のスプリントの ID を返す。

    実行中のスプリントはオーガナイザーが書かず、レーンの conductor が `ndf progress cancel` で中止にする。
    """
    view, _ = lane.read_plan(conn, pid)
    if model.is_terminal(view.state):
        raise model.Refused(f"計画 {view.name} は{model.label(view.state)}で、止められない")
    running = lane.cancel_pending(conn, view, reason)
    _append(conn, pid, [model.EventDraft("plan_cancelled", "organizer", reason=reason)])
    return running


def open_request(conn: sa.Connection, pid: str, body: dict[str, Any], reason: str, actor: str) -> str:
    """計画の進行表へ依頼を開く（`request_opened`）。依頼の鍵を返す。"""
    view, _ = lane.read_plan(conn, pid)
    key = f"{lane.REQUEST_PREFIX}{len(view.requests) + 1}"
    payload = {"lane": body.get("lane"), "body": body}
    _append(conn, pid, [model.EventDraft("request_opened", actor, key, reason=reason, payload=payload)])
    return key


def close_request(conn: sa.Connection, pid: str, key: str, reason: str) -> None:
    """答えていない依頼を採らずに閉じる（オーガナイザー）。"""
    _check_request(conn, pid, key)
    _append(conn, pid, [model.EventDraft("request_closed", "organizer", key, reason=reason)])


def complete_plan(conn: sa.Connection, pid: str) -> bool:
    """計画を完了にする（耐久ワークフロー）。全レーンの行が終わり、答えていない依頼が無いときだけ書く（I16）。"""
    view, _ = lane.read_plan(conn, pid)
    if view.state != lane.RUNNING or not lane.plan_complete(view, lane.folded(lane.read_lanes(conn, view))):
        return False
    _append(conn, pid, [model.EventDraft("plan_completed", "durable")])
    return True


# --- 状態の表示 -----------------------------------------------------------------------------------


def _row_item(row: lane.LaneRow, now: datetime) -> dict[str, Any]:
    item = {"row": row.key, "state": row.state, "label": model.label(row.state), "input": row.input, "sprint_id": row.sprint_id}
    if row.awaiting_next:
        waited = (now - row.answered_at).total_seconds() if row.answered_at else 0.0
        item["awaiting_next"] = {
            "since": row.answered_at.isoformat() if row.answered_at else None,
            "stalled": waited >= ANSWER_NEXT_GRACE_SECONDS,
            "actor": "conductor",
            "next": result.command("progress", "next", row.sprint_id) if row.sprint_id else None,
        }
    return item


def status(conn: sa.Connection, pid: str, now: datetime) -> dict[str, Any]:
    """計画の状態を、計画の進行表とレーンの進行表の 2 つから作る（受け入れ条件 4）。"""
    view, _ = lane.read_plan(conn, pid)
    rows = lane.folded(lane.read_lanes(conn, view))
    items = []
    for name, spec in view.lanes.items():
        lane_rows = [_row_item(r, now) for r in rows[name]]
        items.append({"lane": name, "state": lane.lane_state(view, rows, name), "after": list(spec.after), "rows": lane_rows})
    stalls = [
        f"レーン {i['lane']} の行 {r['row']}: 答えが載った後に conductor が next を打っていない（{r['awaiting_next']['since']} から）"
        for i in items
        for r in i["rows"]
        if "awaiting_next" in r and r["awaiting_next"]["stalled"]
    ]
    metrics = {
        "plan_id": pid,
        "plan": view.name,
        "state": view.state,
        "max_lanes": view.max_lanes,
        "max_plans": view.max_plans,
        "wakeable": [list(w) for w in lane.wakeable_lanes(view, rows)],
        "open_requests": [{"request": r.key, "lane": r.lane, "reason": r.reason} for r in view.open_requests()],
        "complete": lane.plan_complete(view, rows),
        "stalled": len(stalls),
    }
    summary = f"計画 {view.name} は{model.label(view.state)}（レーン {len(items)}・止まり {len(stalls)}）"
    return result.result(TOOL, result.OK, summary, items=items, metrics=metrics, warnings=stalls)


# --- 副命令 ---------------------------------------------------------------------------------------


Command = Callable[[argparse.Namespace, sa.Connection, str], dict[str, Any]]


def _ok(summary: str, **metrics: Any) -> dict[str, Any]:
    return result.result(TOOL, result.OK, summary, metrics=metrics)


def _pid(conn: sa.Connection, repo: str, name: str) -> str:
    pid = plan_id(repo, name)
    lane.read_plan(conn, pid)
    return pid


def _cmd_create(a: argparse.Namespace, conn: sa.Connection, repo: str) -> dict[str, Any]:
    doc = read_doc(Path(a.plan_file), PLAN_SCHEMA, "計画の宣言")
    start = a.action == "start"
    existing = store.read_table(conn, plan_id(repo, doc["name"]))
    if existing is None:
        pid = create_plan(conn, repo, doc, start)
    elif start:
        pid = existing["id"]
        start_plan(conn, pid)
    else:
        raise model.Refused(f"計画 {doc['name']} の進行表は既にある", current_id=existing["id"])
    view, _ = lane.read_plan(conn, pid)
    return _ok(f"計画 {view.name} の進行表を作った（{model.label(view.state)}・レーン {len(view.lanes)}）", plan_id=pid, state=view.state)


def _cmd_status(a: argparse.Namespace, conn: sa.Connection, repo: str) -> dict[str, Any]:
    return status(conn, _pid(conn, repo, a.plan), datetime.now(UTC))


def _cmd_lane_add(a: argparse.Namespace, conn: sa.Connection, repo: str) -> dict[str, Any]:
    decl = read_doc(Path(a.lane_file), LANE_SCHEMA, "レーンの宣言")
    tid = add_lane(conn, _pid(conn, repo, a.plan), decl, a.request)
    return _ok(f"計画 {a.plan} にレーン {decl['name']} を足した", lane_table_id=tid)


def _cmd_set(a: argparse.Namespace, conn: sa.Connection, repo: str) -> dict[str, Any]:
    changes = {k: v for k, v in (("plans", a.plans), ("touches", a.touches), ("after", a.after)) if v is not None}
    change_lane(conn, _pid(conn, repo, a.plan), a.lane, changes, a.reason, a.request)
    return _ok(f"計画 {a.plan} のレーン {a.lane} を変えた", changes=changes)


def _cmd_limits(a: argparse.Namespace, conn: sa.Connection, repo: str) -> dict[str, Any]:
    changes = {k: v for k, v in (("max_lanes", a.max_lanes), ("max_plans", a.max_plans)) if v is not None}
    change_lane(conn, _pid(conn, repo, a.plan), lane.PLAN_ROW, changes, a.reason)
    return _ok(f"計画 {a.plan} の上限を変えた", changes=changes)


def _cmd_move(a: argparse.Namespace, conn: sa.Connection, repo: str) -> dict[str, Any]:
    key = move_row(conn, _pid(conn, repo, a.plan), (a.from_lane, a.row), a.to_lane, a.reason)
    return _ok(f"行 {a.from_lane}/{a.row} をレーン {a.to_lane} の行 {key} へ移した", row=key)


def _cmd_stop(a: argparse.Namespace, conn: sa.Connection, repo: str) -> dict[str, Any]:
    running = stop_plan(conn, _pid(conn, repo, a.plan), a.reason)
    items = [{"sprint_id": s, "next": result.command("progress", "cancel", s, "--reason", a.reason)} for s in running]
    out = _ok(f"計画 {a.plan} を中止した（実行中のスプリント {len(running)}）", running=running)
    out["items"] = items
    return out


def _cmd_request_close(a: argparse.Namespace, conn: sa.Connection, repo: str) -> dict[str, Any]:
    close_request(conn, _pid(conn, repo, a.plan), a.request, a.reason)
    return _ok(f"計画 {a.plan} の依頼 {a.request} を閉じた", request=a.request)


def _cmd_answered(a: argparse.Namespace, conn: sa.Connection, _repo: str) -> dict[str, Any]:
    seq = lane.record_answer(conn, a.sprint_id, {"answer": a.answer})
    if seq is None:
        raise model.Refused(f"スプリント {a.sprint_id} を持つレーンの行が無い")
    return _ok(f"スプリント {a.sprint_id} の答えをレーンの行へ載せた（次はそのレーンの conductor が next を打つ）", seq=seq)


def _row_change(a: argparse.Namespace) -> lane.RowChange:
    return lane.RowChange("conductor", getattr(a, "reason", None), getattr(a, "index", None))


def _cmd_row(a: argparse.Namespace, conn: sa.Connection, repo: str) -> dict[str, Any]:
    """レーンの conductor の副命令（レーンの進行表を書く）。"""
    if a.row_action in ("end", "resumed"):
        return _ROW_BY_SPRINT[a.row_action](a, conn)
    pid = _pid(conn, repo, a.plan)
    return _ROW_ACTIONS[a.row_action](a, conn, pid)


def _row_add(a: argparse.Namespace, conn: sa.Connection, pid: str) -> dict[str, Any]:
    key = lane.add_row(conn, pid, a.lane, {"kind": "sprint", "issues": a.issues, "args": a.args}, _row_change(a))
    return _ok(f"レーン {a.lane} に行 {key} を足した", row=key)


def _row_move(a: argparse.Namespace, conn: sa.Connection, pid: str) -> dict[str, Any]:
    lane.move_row(conn, pid, a.lane, a.row, _row_change(a))
    return _ok(f"レーン {a.lane} の行 {a.row} を位置 {a.index} へ動かした", row=a.row)


def _row_cancel(a: argparse.Namespace, conn: sa.Connection, pid: str) -> dict[str, Any]:
    lane.cancel_row(conn, pid, a.lane, a.row, _row_change(a))
    return _ok(f"レーン {a.lane} の行 {a.row} を中止にした", row=a.row)


def _row_carry(a: argparse.Namespace, conn: sa.Connection, pid: str) -> dict[str, Any]:
    key = lane.carry_issues(conn, pid, a.lane, a.row, a.issues, a.reason)
    return _ok(f"外した課題をレーン {a.lane} の次の行 {key} へ入れた", row=key)


def _row_start(a: argparse.Namespace, conn: sa.Connection, pid: str) -> dict[str, Any]:
    warnings = lane.start_row(conn, pid, a.lane, a.row, a.sprint)
    out = _ok(f"レーン {a.lane} の行 {a.row} を実行中にした", row=a.row, sprint_id=a.sprint)
    if warnings:
        out["warnings"] = warnings
    return out


def _row_end(a: argparse.Namespace, conn: sa.Connection) -> dict[str, Any]:
    nxt = lane.end_row(conn, a.sprint_id, a.outcome, "conductor")
    return _ok(f"スプリント {a.sprint_id} の行を{model.label(a.outcome)}にした", next_row=nxt)


def _row_resumed(a: argparse.Namespace, conn: sa.Connection) -> dict[str, Any]:
    seq = lane.record_resumed(conn, a.sprint_id, "conductor")
    return _ok(f"スプリント {a.sprint_id} の答えの後の next を記録した", seq=seq)


_ROW_ACTIONS: dict[str, Callable[[argparse.Namespace, sa.Connection, str], dict[str, Any]]] = {
    "add": _row_add,
    "move": _row_move,
    "cancel": _row_cancel,
    "carry": _row_carry,
    "start": _row_start,
}
_ROW_BY_SPRINT: dict[str, Callable[[argparse.Namespace, sa.Connection], dict[str, Any]]] = {
    "end": _row_end,
    "resumed": _row_resumed,
}
COMMANDS: dict[str, Command] = {
    "init": _cmd_create,
    "start": _cmd_create,
    "status": _cmd_status,
    "lane": _cmd_lane_add,
    "set": _cmd_set,
    "limits": _cmd_limits,
    "move": _cmd_move,
    "stop": _cmd_stop,
    "request": _cmd_request_close,
    "answered": _cmd_answered,
    "row": _cmd_row,
}


def _row_parser(sub: Any) -> None:
    row = sub.add_parser("row", help="レーンの進行表を書く（レーンの conductor）").add_subparsers(dest="row_action", required=True)
    for name in ("add", "move", "cancel", "carry", "start"):
        p = row.add_parser(name)
        p.add_argument("plan")
        p.add_argument("lane")
        if name != "add":
            p.add_argument("row", help="行の鍵（r1 など）")
        if name in ("add", "carry"):
            p.add_argument("--issues", type=int, nargs="+", required=True)
        if name in ("add", "move"):
            p.add_argument("--index", type=int, required=name == "move", help="並びの位置（0 が先頭）")
        if name == "add":
            p.add_argument("--args", nargs="*", default=[])
        if name == "start":
            p.add_argument("--sprint", required=True, help="スプリントの進行表の ID")
        else:
            p.add_argument("--reason", required=True)
    end = row.add_parser("end")
    end.add_argument("sprint_id")
    end.add_argument("--outcome", choices=lane.DONE_STATES, required=True)
    row.add_parser("resumed").add_argument("sprint_id")


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"ndf {TOOL}", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)
    for name in ("init", "start"):
        sub.add_parser(name).add_argument("plan_file")
    sub.add_parser("status").add_argument("plan")
    add = sub.add_parser("lane").add_subparsers(dest="lane_action", required=True).add_parser("add")
    add.add_argument("plan")
    add.add_argument("lane_file")
    add.add_argument("--request")
    st = sub.add_parser("set")
    st.add_argument("plan")
    st.add_argument("lane")
    st.add_argument("--plans", type=int)
    st.add_argument("--touches", nargs="+")
    st.add_argument("--after", nargs="*")
    st.add_argument("--request")
    st.add_argument("--reason", required=True)
    lim = sub.add_parser("limits")
    lim.add_argument("plan")
    lim.add_argument("--max-lanes", type=int)
    lim.add_argument("--max-plans", type=int)
    lim.add_argument("--reason", required=True)
    mv = sub.add_parser("move")
    for arg in ("plan", "from_lane", "row", "to_lane"):
        mv.add_argument(arg)
    mv.add_argument("--reason", required=True)
    stop = sub.add_parser("stop")
    stop.add_argument("plan")
    stop.add_argument("--reason", required=True)
    req = sub.add_parser("request").add_subparsers(dest="request_action", required=True).add_parser("close")
    for arg in ("plan", "request"):
        req.add_argument(arg)
    req.add_argument("--reason", required=True)
    ans = sub.add_parser("answered")
    ans.add_argument("sprint_id")
    ans.add_argument("--answer", required=True)
    _row_parser(sub)
    return ap


def _dispatch(a: argparse.Namespace, cwd: Path) -> dict[str, Any]:
    root = command._root(cwd)
    url = store.default_url(root)
    store.sqlite_path(url)  # 受けない URL の形は、置き場を用意する前に断る
    ready = durable.prepare_store(root, datetime.now(UTC))
    with store.open_store(url, ready.root) as st:
        repo = command._repo(cwd, st.root)
        with store.transaction(st) as conn:
            out = COMMANDS[a.action](a, conn, repo)
        if st.warnings:
            out.setdefault("warnings", []).extend(st.warnings)
        return out


def _stopped(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, model.Refused):
        metrics = {"current_id": exc.current_id} if exc.current_id else {}
        return result.stopped(TOOL, "refused", exc.reason, metrics=metrics)
    if isinstance(exc, lane.FormatMismatch):
        return result.stopped(TOOL, lane.FormatMismatch.kind, str(exc))
    if isinstance(exc, durable.StoreStopped):
        return result.stopped(TOOL, exc.kind, str(exc), items=[{"path": str(p)} for p in exc.paths])
    if isinstance(exc, sa.exc.SQLAlchemyError):
        # 本文は SQL・値・接続先を含みうるので種類の名前だけを出す
        return result.stopped(TOOL, store.StoreUnreachable.kind, f"進行表の DB を使えない: {type(exc).__name__}")
    if isinstance(exc, identity.IdentityError):
        return result.stopped(TOOL, "config_broken", str(exc))
    if isinstance(exc, subprocess.CalledProcessError):
        return result.stopped(TOOL, "precondition", f"git の中で打つ（チェックアウトの置き場を求められない）: {exc}")
    return result.stopped(TOOL, "invalid", str(exc))


STOPPABLE = (
    model.Refused,
    lane.FormatMismatch,
    durable.StoreStopped,
    sa.exc.SQLAlchemyError,
    contracts.ContractError,
    GitError,
    identity.IdentityError,
    subprocess.CalledProcessError,
)


def main(argv: list[str]) -> int:
    try:
        a = parser().parse_args(argv)
    except SystemExit as exc:
        # 使い方の誤りは argparse が標準エラーへ書いた。--help は 0
        return causes.exit_code("invalid") if exc.code else 0
    try:
        out = _dispatch(a, Path.cwd())
    except STOPPABLE as exc:
        out = _stopped(exc)
    return result.emit(out)
