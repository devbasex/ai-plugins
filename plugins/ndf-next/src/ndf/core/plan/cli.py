"""副命令 `plan`: プランの実行の回を流す・待つ・待ちの一覧を出す。

ndf plan next <plan.json> [--from <ステップ>] [--state-dir <置き場>] [--answer <答え>]
    回を始めるか続け、終わるか承認の答えを待つ所まで進める。答えを待つ所では承認ゲートの結果（routes）を出して抜ける
ndf plan wait <plan.json>... [--timeout <秒>]
    DBOS を開かずに回の控えと待ちの目印を見回り、止まり・承認ゲート・終わりを種類つきで返す（next は続きの待ち）
ndf plan pending
    チェックアウトの置き場から、待ちを抱えた（PENDING・ENQUEUED・DELAYED）実行を並べる（B5）
ndf plan check <plan.json>...
    プランの形と、キューの組（触るファイルの重なり・前提）を確かめる

結果 JSON を標準出力へ 1 行で出す。
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Any, NoReturn, Optional

from ndf import identity
from ndf.contracts import causes, limits, result
from ndf.contracts.exit_codes import GATE, OK, STOPPED
from ndf.core import durable
from ndf.core.plan import assemble_decide, model, queue_decide, runner, wait_decide

TOOL = runner.TOOL
# 待ちの上限の既定（秒）と、見回りの間隔の宣言
WAIT_DEFAULT_SECONDS = 600.0
WAIT_POLL = "durable.wait-from-poll"
# 進行表と同じ DB を使うときの URL（無ければチェックアウトの置き場の SQLite）
ENV_DB = "NDF_PROGRESS_DB"


def _root() -> Path:
    return durable.store_root(identity.load().name, Path.cwd())


def _url() -> Optional[str]:
    value = os.environ.get(ENV_DB)
    return value if value else None


def _store_stopped(exc: durable.StoreStopped) -> dict[str, Any]:
    """置き場の止まり。種類が止まった理由の表に無ければ precondition で返し、殻の語を instance に載せる。"""
    kind = exc.kind if exc.kind in causes.table() else "precondition"
    return result.stopped(TOOL, kind, str(exc), instance=exc.kind, items=[{"kind": "path", "path": str(p)} for p in exc.paths])


def _exit_with(out: dict[str, Any]) -> NoReturn:
    """結果を出し、DBOS を閉じずにプロセスを抜ける（承認の答えを待つ耐久ワークフローを鍵のファイルに残す）。"""
    code = result.emit(out)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def _cmd_next(a: argparse.Namespace) -> int:
    root = _root()
    try:
        inv = runner.prepare(root, Path(a.plan), a.start, a.state_dir, Path.cwd())
        return result.emit(runner.advance(root, inv, a.answer, _exit_with, _url()))
    except model.PlanError as exc:
        return result.emit(result.stopped(TOOL, "invalid", str(exc)))
    except durable.StoreStopped as exc:
        return result.emit(_store_stopped(exc))


def _watched(path: Path) -> wait_decide.Watched:
    """1 本のプランの回の今を、DBOS を開かずに控えと目印から読む。"""
    name = path.stem
    try:
        plan = model.load(path)
    except model.PlanError as exc:
        return wait_decide.Watched(name, STOPPED, "invalid", str(exc))
    sdir = Path(assemble_decide.state_dir_for(str(path), plan.state_dir, str(Path.cwd())))
    record = runner.read_json(sdir / runner.RUN_FILE)
    if record is None:
        return wait_decide.Watched(plan.name, None)
    run = durable.run_name(record["key"])
    done = runner.read_json(sdir / runner.RESULTS_DIR / f"{run}.json")
    if done is not None:
        cause = done.get("cause") if isinstance(done.get("cause"), dict) else {}
        return wait_decide.Watched(plan.name, done["status"], cause.get("kind"), cause.get("detail"), done.get("routes"))
    marker = runner.read_json(sdir / f"{runner.WAITING_PREFIX}{run}.json")
    if marker is not None:
        return wait_decide.Watched(plan.name, GATE, routes=marker["routes"])
    return wait_decide.Watched(plan.name, None)


def _cmd_wait(a: argparse.Namespace) -> int:
    paths = [Path(p) for p in a.plans]
    again = shlex.join([runner.CLI_NAME, TOOL, "wait", *a.plans, "--timeout", str(a.timeout)])
    interval = limits.value(WAIT_POLL, "interval")
    began = time.monotonic()
    while True:
        found = [_watched(p) for p in paths]
        if wait_decide.settled(found):
            return result.emit(wait_decide.attention(TOOL, found, again))
        waited = time.monotonic() - began
        if waited >= a.timeout:
            return result.emit(wait_decide.timed_out(TOOL, found, waited, again))
        time.sleep(interval)


def _cmd_pending(_: argparse.Namespace) -> int:
    url = _url()
    try:
        found = durable.pending(url if url is not None else _root())
    except durable.StoreStopped as exc:
        return result.emit(_store_stopped(exc))
    items = [
        {"kind": "run", "run": r.run_name, "workflow": r.workflow_id, "status": r.status, "updated_at": r.updated_at.isoformat() if r.updated_at else None}
        for r in found
    ]
    return result.emit(result.result(TOOL, OK, f"待ちを抱えた実行が {len(items)} 件ある", items, {"pending": len(items)}))


def _cmd_check(a: argparse.Namespace) -> int:
    plans, errs = [], []
    for p in a.plans:
        try:
            plans.append(model.load(Path(p)))
        except model.PlanError as exc:
            errs.append({"kind": "violation", "reason": str(exc)})
    if errs:
        return result.emit(result.stopped(TOOL, "invalid", f"プランの誤りが {len(errs)} 件ある", items=errs))
    waves = queue_decide.waves([queue_decide.Queued(p.name, p.touches, p.needs) for p in plans])
    items = [{"kind": "wave", "plans": w} for w in waves]
    return result.emit(result.result(TOOL, OK, f"{len(plans)} 本のプランを {len(waves)} 組で流せる", items, {"plans": len(plans), "waves": len(waves)}))


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=TOOL, description="プランの実行の回を流す・待つ")
    sub = p.add_subparsers(dest="cmd", required=True)
    nx = sub.add_parser("next", help="回を始めるか続ける")
    nx.add_argument("plan")
    nx.add_argument("--from", dest="start")
    nx.add_argument("--state-dir")
    nx.add_argument("--answer")
    nx.set_defaults(fn=_cmd_next)
    wt = sub.add_parser("wait", help="流れている回を見回る")
    wt.add_argument("plans", nargs="+")
    wt.add_argument("--timeout", type=float, default=WAIT_DEFAULT_SECONDS)
    wt.set_defaults(fn=_cmd_wait)
    pd = sub.add_parser("pending", help="待ちを抱えた実行を並べる")
    pd.set_defaults(fn=_cmd_pending)
    ck = sub.add_parser("check", help="プランの形とキューの組を確かめる")
    ck.add_argument("plans", nargs="+")
    ck.set_defaults(fn=_cmd_check)
    return p


def main(argv: list[str]) -> int:
    try:
        a = _parser().parse_args(argv)
    except SystemExit as exc:
        return causes.exit_code("invalid") if exc.code else 0
    return a.fn(a)
