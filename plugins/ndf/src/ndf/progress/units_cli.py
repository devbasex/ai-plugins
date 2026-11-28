"""`ndf progress units`: レビューの単位を扱うサブコマンド（#3702 の決定 12）。

ndf progress units run <ステートマシンの ID> [--cwd <作業場所>]

`run` は工程「実装」だけを、`ndf progress next` と同じ処理（`run.decide`・`run.play_move`）で流し、同じ工程のイベントを書く。
別の流し方を持つと、失敗や回答の待ちが記録に残らず、開始前の検査も抜ける。`next` との違いは、別のプロセスを起動せず
その場で流すことと、工程「実装」の外へ進まないことの 2 つである。承認した HEAD や、承認が要らないという指定は引数で受けない。

サブコマンドの引数は `add_parsers` が `ndf progress` の解析器へ足し、エントリポイント（`ndf.progress.gate`）が `dispatch` へ渡す。
"""

from __future__ import annotations

import argparse
import contextlib
from pathlib import Path
from typing import Any

from ndf.contracts import result
from ndf.progress import command, design_record, model, run, store
from ndf.progress.run_view import completion_warnings

_PLAYABLE = (run.Action.START, run.Action.RESUME)


def add_parsers(sub: Any) -> None:
    """`ndf progress` のサブコマンドの並び `sub` へ `units` とその下のサブコマンドを足す。"""
    units = sub.add_parser("units", help="レビューの単位を扱う").add_subparsers(dest="units_action", required=True)
    units.add_parser("run", help="工程「実装」だけを next と同じ処理で流す").add_argument("machine_id")
    command.add_cwd(units.choices.values())


def dispatch(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    """`a.env` は `ndf progress next` と同じ外との接点。今のサブコマンドは `run` だけである。"""
    return run_implement(st, a.machine_id, cwd, a.env)


def run_implement(st: store.Store, machine_id: str, cwd: Path, env: run.Env) -> dict[str, Any]:
    """工程「実装」を流す。ほかが排他を持つか、`next` が起動した作業がまだ排他を取っていなければ、何も流さず待つ結果を返す。"""
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(store.run_lock(st, machine_id))
        except model.Refused:
            return run.working_report(machine_id)
        if run.launching(st, machine_id):
            return run.working_report(machine_id)
        run.migrate_definition(st, machine_id)
        drive = run.Drive(st, machine_id, cwd, env, inline=True)
        return command.with_warnings(_flow(drive), before=drive.warnings)
    raise AssertionError("到達しない")


def _implement_move(d: run.Drive) -> tuple[run.View, run.Move, bool]:
    """今のステートマシンと次の 1 手と、その手が工程「実装」のものか。"""
    v = run.read(d.st, d.machine_id)
    m = run.decide(v)
    return v, m, m.stage is not None and m.stage.id == design_record.IMPLEMENT_DEFINITION.stem


def _flow(d: run.Drive) -> dict[str, Any]:
    """次の 1 手が工程「実装」の開始か続きの間だけ手を打ち、抜けた時点の状態の結果を返す。"""
    played = None
    while True:
        v, m, ours = _implement_move(d)
        if not ours or m.action not in _PLAYABLE:
            break
        played = m.stage
        out = run.play_move(d, v, m)
        if out is not None:
            return out
    if ours and m.action in (run.Action.FAILED, run.Action.GATE):
        return run.report(v, m)
    if played is not None:
        return _done(v, played.name)
    ended = _ended_stage(v)
    if ended is None:
        return _not_next(v, m)
    # 打ち直しは記録を読むだけで、進捗記録（課題と PR の本文）を読み書きしない。今の状態は `ndf progress resync` で揃える
    d.warnings.extend(completion_warnings(v, ended))
    return _done(v, ended)


def _end_event(v: run.View, stage: str) -> model.EventDraft | None:
    """工程の行の状態を最後に変えたイベント。"""
    changed = [e.draft for e in v.row_events(stage) if e.draft.state is not None]
    return changed[-1] if changed else None


def _ended_stage(v: run.View) -> str | None:
    """前の実行が工程「実装」を流し終えた（完了か、開始の条件が偽で飛ばした）なら工程の名前。打ち直しに同じ結果を返すために読む。"""
    for s in v.snap.stages:
        if s.id == design_record.IMPLEMENT_DEFINITION.stem:
            last = _end_event(v, s.name)
            return s.name if last is not None and last.kind in (run.FINISHED, "skipped") else None
    return None


def _not_next(v: run.View, m: run.Move) -> dict[str, Any]:
    where = f"工程 {m.stage.name} の {m.action}" if m.stage is not None else str(m.action)
    detail = f"次の 1 手が工程「実装」の開始か続きでないので流さない（次の 1 手は{where}）"
    return result.stopped(run.TOOL, "precondition", detail, metrics={"machine_id": v.machine_id})


def _done(v: run.View, stage: str) -> dict[str, Any]:
    """流し終えた結果。開始の条件が偽で飛ばしたときは、その条件を書く。"""
    last = _end_event(v, stage)
    assert last is not None
    summary = f"工程 {stage} を流した"
    if last.kind == "skipped":
        summary = f"工程 {stage} は{last.reason}なので飛ばした"
    nxt = result.command(run.TOOL, "next", v.machine_id)
    return result.result(run.TOOL, result.OK, summary, metrics={"machine_id": v.machine_id}, next=nxt)
