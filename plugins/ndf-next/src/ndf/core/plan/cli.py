"""副命令 `plan`: プランの実行の回を流す・待つ・待ちの一覧を出す。

ndf plan next <plan.json> [--from <ステップ>] [--state-dir <置き場>] [--answer <答え>] [--set <名前>=<値>]...
    回を始めるか続け、終わるか承認の答えを待つ所まで進める。答えを待つ所では承認ゲートの結果（routes）を出して抜ける
ndf plan queue <plan.json>... [--then <plan.json>...]... [--from <プラン>=<ステップ>]... [--resume] [--queue-dir <置き場>]
    段（並びと `--then` の区切り）ごとに、触るファイルの重ならない組でプランを並べて流す
ndf plan wait <plan.json>... [--state-dir <置き場>] [--timeout <秒>]
    DBOS を開かずに回の控えと待ちの目印を見回り、止まり・承認ゲート・終わりを種類つきで返す（next は続きの待ち）。
    `--state-dir` は `next` に渡した置き場（プラン 1 本のときだけ）
ndf plan pending
    チェックアウトの置き場から、待ちを抱えた（PENDING・ENQUEUED・DELAYED）実行を並べる（B5）
ndf plan check <plan.json>...
    プランの形と、キューの組（触るファイルの重なり・前提）を確かめる

結果 JSON を標準出力へ 1 行で出す。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, NoReturn, Optional

from ndf import identity
from ndf.contracts import causes, exit_codes, limits, result
from ndf.contracts.exit_codes import GATE, OK, STOPPED
from ndf.core import durable
from ndf.core.plan import files, model, queue_decide, queue_run, runner, wait_decide

TOOL = runner.TOOL
# 待ちの上限の既定（秒）と、見回りの間隔の宣言
WAIT_DEFAULT_SECONDS = 600.0
WAIT_POLL = "durable.wait-from-poll"


def _root() -> Path:
    return durable.store_root(identity.load().name, Path.cwd())


def _url() -> Optional[str]:
    """進行表と同じ DB を使うときの URL（`NDF_PROGRESS_DB`。無ければチェックアウトの置き場の SQLite）。値は秘密として伏せる。"""
    value = os.environ.get("NDF_PROGRESS_DB")
    return value if value else None


def _store_stopped(exc: durable.StoreStopped) -> dict[str, Any]:
    """置き場の止まり。種類は殻の語（止まった理由の表の `store_in_use`・`store_broken`・`format_newer`・`config_broken`）。"""
    return result.stopped(TOOL, exc.kind, str(exc), items=[{"kind": "path", "path": str(p)} for p in exc.paths])


def _exit_with(out: dict[str, Any]) -> NoReturn:
    """結果を出し、DBOS を閉じずにプロセスを抜ける（承認の答えを待つ耐久ワークフローを鍵のファイルに残す）。"""
    code = result.emit(out)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def _cmd_next(a: argparse.Namespace) -> int:
    root = _root()
    cwd = Path.cwd()
    try:
        with runner.plan_lock(runner.state_dir_of(Path(a.plan), a.state_dir, cwd)):
            inv = runner.prepare(root, Path(a.plan), a.start, a.state_dir, cwd, _pairs(a.sets, "--set"))
            return result.emit(runner.advance(root, inv, a.answer, _exit_with, _url()))
    except model.PlanError as exc:
        return result.emit(result.stopped(TOOL, "invalid", str(exc)))
    except durable.StoreStopped as exc:
        return result.emit(_store_stopped(exc))


def _pairs(values: Optional[list[str]], flag: str) -> dict[str, str]:
    """`<名前>=<値>` の並びを辞書にする。形が違えば `model.PlanError`。"""
    out: dict[str, str] = {}
    for value in values if values is not None else []:
        name, sep, rest = value.partition("=")
        if not sep or not name or not rest:
            raise model.PlanError(f"{flag} は <名前>=<値> の形にする: {value}")
        out[name] = rest
    return out


def _stages(a: argparse.Namespace, cwd: Path) -> list[list[queue_run.Entry]]:
    """引数からキューの段を作る。プランは絶対のパスにし、`--from` の開始点を付ける。"""
    starts = _pairs(a.starts, "--from")
    groups = [a.plans, *(a.then if a.then is not None else [])]
    stages = [[queue_run.Entry((cwd / p).resolve(), model.load(cwd / p)) for p in group] for group in groups]
    names = [e.plan.name for stage in stages for e in stage]
    unknown = sorted(set(starts) - set(names))
    if unknown:
        raise model.PlanError(f"--from のプランがキューに無い: {', '.join(unknown)}")
    if len(set(names)) != len(names):
        raise model.PlanError("キューに同じ名前のプランが 2 本ある")
    return [[queue_run.Entry(e.path, e.plan, starts.get(e.plan.name)) for e in stage] for stage in stages]


def _cmd_queue(a: argparse.Namespace) -> int:
    cwd = Path.cwd()
    try:
        stages = _stages(a, cwd)
        qdir = queue_run.queue_dir(stages[0][0].path, a.queue_dir, cwd)
        stable = queue_run.move_to_stable(cwd)
    except model.PlanError as exc:
        return result.emit(result.stopped(TOOL, "invalid", str(exc)))
    except subprocess.CalledProcessError as exc:
        return result.emit(result.stopped(TOOL, "precondition", f"チェックアウトの根を引けない: {exc}"))
    outcomes = queue_run.run(stages, qdir, stable, a.resume)
    return result.emit(queue_run.queue_result(TOOL, outcomes, stages, qdir))


def _watched(path: Path, state_dir: Optional[str] = None) -> wait_decide.Watched:
    """1 本のプランの回の今を、DBOS を開かずに控えと目印から読む。"""
    name = path.stem
    try:
        plan = model.load(path)
    except model.PlanError as exc:
        return wait_decide.Watched(name, STOPPED, "invalid", str(exc))
    sdir = runner.resolve_state_dir(path, state_dir, plan, Path.cwd())
    record = files.read_json(sdir / runner.RUN_FILE)
    if record is None:
        return wait_decide.Watched(plan.name, None)
    run = durable.run_name(record["key"])
    done = files.read_json(runner.result_path(sdir, run))
    if done is not None:
        cause = done.get("cause") if isinstance(done.get("cause"), dict) else {}
        return wait_decide.Watched(plan.name, done["status"], cause.get("kind"), cause.get("detail"), done.get("routes"))
    marker = files.read_json(runner.waiting_path(sdir, run))
    if marker is not None:
        return wait_decide.Watched(plan.name, GATE, routes=marker["routes"])
    return wait_decide.Watched(plan.name, None)


def _cmd_wait(a: argparse.Namespace) -> int:
    paths = [Path(p) for p in a.plans]
    if a.state_dir is not None and len(paths) != 1:
        return result.emit(result.stopped(TOOL, "invalid", "--state-dir はプラン 1 本のときだけ渡せる"))
    given = ["--state-dir", a.state_dir] if a.state_dir is not None else []
    again = result.command(TOOL, "wait", *a.plans, *given, "--timeout", str(a.timeout))
    interval = limits.value(WAIT_POLL, "interval")
    began = time.monotonic()
    while True:
        found = [_watched(p, a.state_dir) for p in paths]
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
        {
            "kind": "run",
            "run": r.run_name,
            "workflow": r.workflow_id,
            "status": r.status,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
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
    return result.emit(
        result.result(TOOL, OK, f"{len(plans)} 本のプランを {len(waves)} 組で流せる", items, {"plans": len(plans), "waves": len(waves)})
    )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=TOOL, description="プランの実行の回を流す・待つ")
    sub = p.add_subparsers(dest="cmd", required=True)
    nx = sub.add_parser("next", help="回を始めるか続ける")
    nx.add_argument("plan")
    nx.add_argument("--from", dest="start")
    nx.add_argument("--state-dir")
    nx.add_argument("--answer")
    nx.add_argument("--set", dest="sets", action="append")
    nx.set_defaults(fn=_cmd_next)
    qu = sub.add_parser("queue", help="段ごとにプランを並べて流す")
    qu.add_argument("plans", nargs="+")
    qu.add_argument("--then", action="append", nargs="+")
    qu.add_argument("--from", dest="starts", action="append")
    qu.add_argument("--resume", action="store_true")
    qu.add_argument("--queue-dir")
    qu.set_defaults(fn=_cmd_queue)
    wt = sub.add_parser("wait", help="流れている回を見回る")
    wt.add_argument("plans", nargs="+")
    wt.add_argument("--state-dir")
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
        return causes.exit_code("invalid") if exc.code else exit_codes.code_of("ok")
    return a.fn(a)
