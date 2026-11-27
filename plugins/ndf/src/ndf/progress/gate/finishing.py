"""サブコマンド `ndf progress` の、工程を手で扱う `finish`・`check`・`retry`（`ndf.progress.command` から分けた）。

引数の形は `command.parser` が持つ。`main` はこの 3 つを受け、ほかのサブコマンドは `command` へ渡す。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Optional

from ndf.adapters.git import GitError
from ndf.adapters.github import GitHubError
from ndf.contracts import result
from ndf.core import merge_approval
from ndf.progress import command, definition, model, reopening, run, stages, store
from ndf.progress.gate.answering import shared_hub
from ndf.progress.records import github_records, sync_records


def _revoker(cwd: Path) -> reopening.Revoke:
    """承認の取り消しを PR へ書く口。書けなければ `model.Refused`（開き直しを記録しない）。"""
    get_hub = shared_hub(cwd)

    def revoke(target: merge_approval.MergeTarget, record_id: str) -> bool:
        try:
            hub = get_hub()
            return merge_approval.write_revocation(hub, target, record_id, hub.viewer())
        except (merge_approval.TrailError, GitHubError, GitError) as exc:
            raise model.Refused(f"PR #{target.number} の承認を取り消せないので、開き直さない: {exc}") from None

    return revoke


def _retry_refusal(machine_id: str, stage: str, state: str, later: Optional[str]) -> Optional[str]:
    """`retry` を断る理由と、代わりに打てるコマンド。打てるなら None。失敗の工程は `--later` なし、終了の工程は必須。"""
    base = f"ndf progress retry {machine_id} {stage} --reason <理由>"
    if state == "failed":
        return f"失敗した工程 {stage} は --later を受けない。{base} を打つ" if later else None
    if state == run.FINISHED:
        return None if later else f"終了した工程 {stage} は後ろの扱いを指定する。{base} --later reopen か --later keep を打つ"
    others = {
        "pending": f"ndf progress next {machine_id} で流す",
        "running": f"ndf progress wait {machine_id} で待つか、承認ゲートの回答を ndf answer で記録する",
        command.TOGGLE_KINDS["disable"]: f"ndf progress enable {machine_id} {stage} --reason <理由> で有効に戻す",
    }
    other = others[state] if state in others else f"ndf progress show {machine_id} で状態を確かめる"
    return f"工程 {stage} は{model.label(state)}で、retry を打てるのは失敗と終了の工程。{other}"


def _reopen_stage(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    """終了した工程を開き直し、進捗記録を書き直す。PR の承認の取り消しは、DB の追記より先に書く。"""
    revoke = _revoker(cwd)
    attempts = command.append_attempts()
    for attempt in range(1, attempts + 1):
        v = run.read(st, a.machine_id)
        planned = reopening.plan_reopen(v, a.stage, later=a.later, reason=a.reason, trigger={"by": "retry"})
        reopening.revoke_approvals(planned, revoke)
        try:
            with store.transaction(st) as conn:
                # 読んだ後に承認が増えていれば（回答は排他を取らない）、取り消す対象を組み直す
                seqs = store.append_events(conn, a.machine_id, planned.drafts, v.events[-1].seq)
            break
        except store.Conflict:
            if attempt == attempts:
                raise
    _, errors = sync_records(run.read(st, a.machine_id), cwd, github_records)
    unsynced = [f"{e}（ndf progress resync {a.machine_id} で揃える）" for e in errors]
    summary = f"ステートマシン {a.machine_id} の工程を開き直した: {'・'.join(planned.stages)}。続きは ndf progress next で流す"
    out = result.result(
        command.TOOL,
        result.OK,
        summary,
        items=[{"kind": "reopened", "stage": name} for name in planned.stages],
        metrics={"machine_id": a.machine_id, "seqs": seqs},
        next=result.command(command.TOOL, "next", a.machine_id),
    )
    return command.with_warnings(out, after=[*planned.warnings, *unsynced])


def _cmd_retry(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    """失敗の工程は事象 `retry` を書く（済んだステップを残す）。終了の工程は `--later` に従って開き直す。"""
    if not a.reason.strip():
        raise model.Refused("retry には理由が要る（--reason <理由>）")
    with reopening.work_stopped(st, a.machine_id):
        v = run.read(st, a.machine_id)
        v.snap.stage(a.stage)  # 工程の無い名前はここで断る
        state = v.rows[a.stage].state
        refused = _retry_refusal(a.machine_id, a.stage, state, a.later)
        if refused is not None:
            whole = f"ステートマシンは{model.label(v.whole)}。" if model.is_terminal(v.whole) else ""
            raise model.Refused(whole + refused)
        if state == "failed":
            return command._changed(a, st, "retry", None)
        return _reopen_stage(a, cwd, st)


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
