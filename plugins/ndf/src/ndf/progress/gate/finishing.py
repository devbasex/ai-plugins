"""サブコマンド `ndf progress` の、工程を手で扱う `finish`・`check`・`retry`（`ndf.progress.command` から分けた）。

引数の形は `command.parser` が持つ。`main` はこの 3 つを受け、ほかのサブコマンドは `command` へ渡す。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Optional

from ndf.contracts import result
from ndf.progress import command, definition, model, stages, store


def _cmd_retry(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    with store.run_lock(st, a.machine_id):
        return command._changed(a, st, "retry", None)


def _read_stage_output(a: argparse.Namespace, st: store.Store) -> tuple[Any, stages.StageDef, list[str], list[tuple[str, Optional[str]]]]:
    """`finish` と `check` の共通の前半。出力を読み、工程の定義を引き、規約と突き合わせた誤りと欠けたキー・取り場所の組を返す。"""
    output = command.read_json(Path(a.output))
    with store.transaction(st) as conn:
        snap = command.read_current(conn, a.machine_id).snapshot
    stage = snap.stage(a.stage)
    errs, missing = definition.check_stage_output(stage, output)
    return output, stage, errs, missing


def _cmd_finish(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    output, stage, errs, _missing = _read_stage_output(a, st)
    if errs:
        raise model.Refused(f"{a.stage} の出力が規約に合わない: {'; '.join(errs)}")
    dropped: list[str] = []

    def build(rev: int, _snap: stages.Snapshot, _events: list[model.Event]) -> list[model.EventDraft]:
        """終了のマーカーと、工程が流れたときと同じ形で出力を文脈へ書く事象（後の工程の入力になる）。"""
        draft, dropped[:] = stages.outputs_draft(stage, rev, output["outputs"] if "outputs" in output else {}, command.ACTOR)
        done = model.EventDraft("manual_finished", command.ACTOR, a.stage, reason=a.reason, payload={"output": a.output}, rev=rev)
        return [done, *([draft] if draft is not None else [])]

    seqs = command._append(st, a.machine_id, build)
    st.warnings.extend(dropped)
    return result.result(
        command.TOOL,
        result.OK,
        f"ステートマシン {a.machine_id} の {a.stage} に manual_finished を書いた",
        metrics={"machine_id": a.machine_id, "seqs": seqs},
    )


def _cmd_check(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    """工程の出力を `finish` と同じ規則で確かめ、書かずに返す。`metrics.outputs` は工程の定義のキー、`warnings` は `finish` が捨てるキー。"""
    output, stage, errs, missing_keys = _read_stage_output(a, st)
    metrics = {"machine_id": a.machine_id, "stage": a.stage, "outputs": list(stage.outputs)}
    if errs:
        missing = [{"kind": "missing", "key": k, "source": source} for k, source in missing_keys]
        return result.stopped(
            command.TOOL, "refused", f"{a.stage} の出力が規約に合わない: {'; '.join(errs)}", metrics=metrics, items=missing
        )
    _, dropped = stages.keep_declared(stage.outputs, output["outputs"] if "outputs" in output else {})
    st.warnings.extend(f"工程 {a.stage} の定義の outputs に無いキーを finish が捨てる: {k}" for k in dropped)
    return result.result(command.TOOL, result.OK, f"{a.stage} の出力は規約に合う", metrics=metrics)


COMMANDS: dict[str, command.Command] = {**command._answer_commands((_cmd_retry, _cmd_finish)), "check": _cmd_check}


def main(argv: list[str]) -> int:
    """サブコマンド `ndf progress` のうち工程を流さないもの。`COMMANDS` はここで受け、ほかは `command` へ渡す。"""
    if not argv or argv[0] not in COMMANDS:
        return command.main(argv)
    return command.run_main(command.parser(), lambda a, cwd: command.with_store(cwd, COMMANDS[a.action], a), argv)
