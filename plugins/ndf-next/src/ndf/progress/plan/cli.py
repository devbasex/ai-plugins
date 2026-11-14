"""副命令 `ndf lanes`: 計画の進行表とレーンの進行表を読み書きする（#3097）。

ndf lanes init <plan.json>                       計画の検査を通し、計画の進行表と各レーンの進行表を未着手で作る
ndf lanes start <plan.json>                      init と同じに作り（作ってあれば読み）、計画を始めて耐久ワークフローを切り離して起こす
ndf lanes run <計画>                             計画を進める耐久ワークフローを前で流す（start が切り離して打つ）
ndf lanes wait <計画> [--after <番号>]           計画の知らせ（依頼・終わり・止まり）を、渡した番号より後からすべて待って返す
ndf lanes status <計画>                          計画とレーンの進行表から計画の状態を出す
ndf lanes lane add <計画> <lane.json> [--request <依頼>]
ndf lanes set <計画> <レーン> [--plans <本数>] [--touches <場所>...] [--after <先>...] --reason <理由> [--request <依頼>]
ndf lanes limits <計画> [--max-lanes <数>] [--max-plans <本数>] --reason <理由>
ndf lanes move <計画> <元のレーン> <行> <先のレーン> --reason <理由>
ndf lanes stop <計画> --reason <理由>
ndf lanes request close <計画> <依頼> --reason <理由>
ndf lanes answered <スプリントの進行表の ID> --answer <答え>
ndf lanes row add|move|cancel|carry|start|end|resumed ...   （レーンの conductor が打つ。レーンの進行表）

書き手は段ごとに 1 つに寄る。計画の進行表の副命令の書き手はオーガナイザー（作成・レーンをまたぐ変更・中止・依頼を閉じる・
答えの記録）で、完了は耐久ワークフロー（`run`）が書く。`row` の副命令はレーンの進行表（`ndf.progress.lane`）を書き、
書き手はレーンの conductor になる。結果は結果 JSON の 1 行で返す。
"""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from ndf import contracts, identity
from ndf.adapters.git import GitError
from ndf.contracts import causes, limits, result
from ndf.core import durable
from ndf.progress import command, lane, model, store
from ndf.progress.plan import (
    LANE_SCHEMA,
    PLAN_SCHEMA,
    TOOL,
    add_lane,
    change_lane,
    close_request,
    create_plan,
    move_row,
    plan_id,
    read_doc,
    start_plan,
    status,
    stop_plan,
)
from ndf.progress.plan import run, wait

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
    summary = f"計画 {view.name} の進行表を作った（{model.label(view.state)}・レーン {len(view.lanes)}）"
    return _ok(summary, plan_id=pid, plan=view.name, state=view.state)


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
    sub.add_parser("run").add_argument("plan")
    wt = sub.add_parser("wait")
    wt.add_argument("plan")
    wt.add_argument("--after", type=int, default=0, help="計画の進行表の事象の番号（前の wait の next が渡す）")
    wt.add_argument("--known-stalls", nargs="*", default=[], help="知らせ済みの止まり（<レーン>/<行>）")
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


def _where(cwd: Path) -> run.Where:
    """計画の置き場を求め、チェックアウトの置き場を用意する。"""
    root = command._root(cwd)
    url = store.default_url(root)
    store.sqlite_path(url)  # 受けない URL の形は、置き場を用意する前に断る
    ready = durable.prepare_store(root, datetime.now(UTC))
    with store.open_store(url, ready.root) as st:
        return run.Where(root=ready.root, url=url, repo=command._repo(cwd, st.root), cwd=cwd)


def _dispatch(a: argparse.Namespace, cwd: Path) -> dict[str, Any]:
    if a.action in SESSIONS:
        return SESSIONS[a.action](a, _where(cwd))
    where = _where(cwd)
    with store.open_store(where.url, where.root) as st:
        with store.transaction(st) as conn:
            out = COMMANDS[a.action](a, conn, where.repo)
        if st.warnings:
            out.setdefault("warnings", []).extend(st.warnings)
    if a.action == "start":
        out["metrics"]["run_pid"] = run.detach(out["metrics"]["plan"], cwd)
    return out


def _cmd_run(a: argparse.Namespace, where: run.Where) -> dict[str, Any]:
    final = run.advance(where, a.plan, run.policy())
    summary = f"計画 {a.plan} の耐久ワークフローが終わった（{final['outcome']}・起こした {len(final['launched'])}・起こせなかった {len(final['failed'])}）"
    return result.result(TOOL, result.OK, summary, metrics=final, warnings=final["errors"])


def _cmd_wait(a: argparse.Namespace, where: run.Where) -> dict[str, Any]:
    with store.open_store(where.url, where.root) as st:
        pid = plan_id(where.repo, a.plan)

        def read() -> wait.Seen:
            with store.transaction(st) as conn:
                return wait.read_seen(conn, pid, a.after, wait.now())

        seen = wait.wait(read, a.known_stalls, limits.seconds(wait.POLL))
    got = wait.report(a.plan, seen, a.known_stalls)
    return result.result(TOOL, result.OK, got["summary"], items=got["items"], metrics=got["metrics"], next=got["next"])


SESSIONS: dict[str, Callable[[argparse.Namespace, run.Where], dict[str, Any]]] = {"run": _cmd_run, "wait": _cmd_wait}


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
