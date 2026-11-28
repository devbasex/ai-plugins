"""サブコマンド `ndf progress` の、工程を手で扱う `finish`・`check`・`retry`（`ndf.progress.command` から分けた）。

引数の形は `command.parser` が持つ。`main` はこの 3 つを受け、ほかのサブコマンドは `command` へ渡す。
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.adapters.git import GitError
from ndf.adapters.github import GitHubError
from ndf.contracts import result
from ndf.core import merge_approval
from ndf.progress import approval_decide, command, definition, model, reopening, run, stages, store
from ndf.progress.gate.answering import shared_hub, standing
from ndf.progress.gate.targets import TargetReader
from ndf.progress.run_view import FINISH_HELD
from ndf.progress.records import github_records, sync_records


def _revoker(cwd: Path) -> reopening.Revoke:
    """承認の取り消しを PR へ書き、マージキューから外す口。どちらかができなければ `model.Refused`（開き直しを記録しない）。"""
    get_hub = shared_hub(cwd)

    def revoke(revocations: list[merge_approval.Revocation]) -> None:
        try:
            hub = get_hub()
            merge_approval.write_revocations(hub, revocations, hub.viewer())
        except (merge_approval.TrailError, GitHubError, GitError) as exc:
            numbers = "・".join(f"#{n}" for n in sorted({r.target.number for r in revocations}))
            raise model.Refused(
                f"PR {numbers} の承認の取り消しとマージキューからの取り外しを終えられないので、開き直さない: {exc}"
            ) from None

    return revoke


def _retry_refusal(machine_id: str, stage: str, state: str, later: Optional[str], by_hand: bool = False) -> Optional[str]:
    """`retry` を断る理由と、代わりに打てるコマンド。打てるなら None。失敗の工程は `--later` なし、終了と手で終えた
    回答待ち（`by_hand`）の工程は必須。"""
    base = f"ndf progress retry {machine_id} {stage} --reason <理由>"
    if state == "failed":
        return f"失敗した工程 {stage} は --later を受けない。{base} を打つ" if later else None
    if state == run.FINISHED or by_hand:
        what = "手で終えた回答待ちの" if by_hand else "終了した"
        return None if later else f"{what}工程 {stage} は後ろの扱いを指定する。{base} --later reopen か --later keep を打つ"
    others = {
        "pending": f"ndf progress next {machine_id} で流す",
        "running": f"ndf progress wait {machine_id} で待つか、承認ゲートの回答を ndf answer で記録する",
        command.TOGGLE_KINDS["disable"]: f"ndf progress enable {machine_id} {stage} --reason <理由> で有効に戻す",
    }
    other = others[state] if state in others else f"ndf progress show {machine_id} で状態を確かめる"
    return f"工程 {stage} は{model.label(state)}で、retry を打てるのは失敗と終了の工程。{other}"


def _reopen_stage(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    """終了した工程を開き直し、進捗記録を書き直す。PR の承認の取り消しは、DB の追記より先に行う。

    読み取りから追記までは、回答と共有する排他（`store.approval_lock`）の中で行う。"""
    with store.approval_lock(st, a.machine_id):
        planned, seqs = _append_reopen(a, st, _revoker(cwd))
    _, errors = sync_records(run.read(st, a.machine_id), cwd, github_records)
    unsynced = [f"{e}（ndf progress resync {a.machine_id} で揃える）" for e in errors]
    return command.with_warnings(_reopened_result(a, planned, seqs), after=[*planned.warnings, *unsynced])


def _append_reopen(a: argparse.Namespace, st: store.Store, revoke: reopening.Revoke) -> tuple[reopening.Reopen, list[int]]:
    """承認を取り消してから開き直しを追記する。追記がぶつかって打ち直すときは、取り消す対象が変わった場合だけ PR を読み直す。"""
    attempts = command.append_attempts()
    revoked: list[merge_approval.Revocation] = []
    for attempt in range(1, attempts + 1):
        v = run.read(st, a.machine_id)
        planned = reopening.plan_reopen(v, a.stage, later=a.later, reason=a.reason, trigger=_trigger(a))
        if planned.revoke != revoked:
            reopening.revoke_approvals(planned, revoke)
            revoked = planned.revoke
        try:
            with store.transaction(st) as conn:
                return planned, store.append_events(conn, a.machine_id, planned.drafts, v.events[-1].seq)
        except store.Conflict:
            if attempt == attempts:
                raise
    raise AssertionError("到達しない")


def _trigger(a: argparse.Namespace) -> dict[str, str]:
    """開き直しの事象に残すきっかけ。指定した工程は、送り直しの判定に使う。"""
    return {"by": "retry", "stage": a.stage}


def _reopened_result(a: argparse.Namespace, planned: reopening.Reopen, seqs: list[int], note: str = "") -> dict[str, Any]:
    summary = f"ステートマシン {a.machine_id} の工程を開き直した: {'・'.join(planned.stages)}。続きは ndf progress next で流す{note}"
    return result.result(
        command.TOOL,
        result.OK,
        summary,
        items=[{"kind": "reopened", "stage": name} for name in planned.stages],
        metrics={"machine_id": a.machine_id, "seqs": seqs},
        next=result.command(command.TOOL, "next", a.machine_id),
    )


def _replayed_reopen(a: argparse.Namespace, v: run.View) -> Optional[dict[str, Any]]:
    """確定した開き直しの送り直しなら、書かず GitHub へ要求も送らずに初回と同じ結果を返す。違えば None。

    送り直しと扱うのは、同じ工程・理由・後ろの扱いの開き直しがあり、開き直した工程にその後の記録が無いときである。"""
    batch = reopening.committed_reopen(v, trigger=_trigger(a), later=a.later, reason=a.reason)
    if not batch:
        return None
    before = replace(v, events=[ev for ev in v.events if ev.seq < batch[0].seq])
    planned = reopening.plan_reopen(before, a.stage, later=a.later, reason=a.reason, trigger=_trigger(a))
    note = f"（開き直しは記録済み。進捗記録が古ければ ndf progress resync {a.machine_id} で揃える）"
    out = _reopened_result(a, planned, [ev.seq for ev in batch], note)
    return command.with_warnings(out, after=planned.warnings)


def _cmd_retry(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    """失敗の工程は事象 `retry` を書く（済んだステップを残す）。終了の工程は `--later` に従って開き直す。

    確定した開き直しの送り直し（`_replayed_reopen`）は書かないので、作業の排他を取れなくても受ける。"""
    if not a.reason.strip():
        raise model.Refused("retry には理由が要る（--reason <理由>）")
    with ExitStack() as stack:
        try:
            stack.enter_context(reopening.work_stopped(st, a.machine_id))
        except model.Refused:
            # 開き直しの直後に起動した作業が排他を持つ場合。履歴を読み足すのは、排他を取れないときだけにする
            replayed = _replayed_reopen(a, run.read(st, a.machine_id)) if a.later else None
            if replayed is None:
                raise
            return replayed
        return _retry_stopped(a, cwd, st)


def _retry_stopped(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    """作業の排他を取った後の `retry`。送り直しは、排他を取るまでの間に確定した開き直しも含めて、ここで読んだ記録で見分ける。"""
    v = run.read(st, a.machine_id)
    replayed = _replayed_reopen(a, v) if a.later else None
    if replayed is not None:
        return replayed
    v.snap.stage(a.stage)  # 工程の無い名前はここで断る
    state = v.rows[a.stage].state
    by_hand = state == "running" and run.step_progress(v, a.stage).held_finish is not None
    refused = _retry_refusal(a.machine_id, a.stage, state, a.later, by_hand)
    if refused is not None:
        whole = f"ステートマシンは{model.label(v.whole)}。" if model.is_terminal(v.whole) else ""
        raise model.Refused(whole + refused)
    if state == "failed":
        return command._changed(a, st, "retry", None)
    return _reopen_stage(a, cwd, st)


class _StageOutput(NamedTuple):
    """`finish` と `check` が読んだ工程の出力。`outputs` は手で書いた `outputs` から NDF が作るキーを除いた値で、
    `generated` は除いたキー（承認ゲートを持つ工程だけ）。"""

    stage: stages.StageDef
    errors: list[str]
    missing: list[tuple[str, Optional[str]]]
    outputs: Mapping[str, Any]
    generated: list[str]


def _read_stage_output(a: argparse.Namespace, st: store.Store) -> _StageOutput:
    """`finish` と `check` の共通の前半。出力を読み、工程の定義を引き、規約と突き合わせる。

    承認ゲートを持つ工程の出力は、NDF が作るキー（`definition.generated_outputs`）を求めず、書かれていれば使わない。"""
    output = command.read_json(Path(a.output))
    with store.transaction(st) as conn:
        snap = command.read_current(conn, a.machine_id).snapshot
    stage = snap.stage(a.stage)
    errs, missing = definition.check_stage_output(stage, output, handwritten=bool(stage.gates))
    outputs = output["outputs"] if not errs and "outputs" in output else {}
    made = definition.generated_outputs(stage.id) if stage.gates else ()
    generated = [k for k in made if k in outputs]
    return _StageOutput(stage, errs, missing, {k: v for k, v in outputs.items() if k not in generated}, generated)


def _generated_warnings(read: _StageOutput) -> list[str]:
    return [f"工程 {read.stage.name} の {k} は NDF が作るので、手で書いた値は使わない" for k in read.generated]


def _held_refusal(v: run.View, stage: str) -> Optional[str]:
    """承認ゲートを持つ工程を手で終えられない状態の理由と、代わりに打てるコマンド。ほかの状態は追記の確かめに任せる。"""
    row = v.rows.get(stage)
    state = row.state if row is not None else None
    if state == command.TOGGLE_KINDS["disable"]:
        return f"承認ゲートを持つ工程 {stage} は無効のままでは終えない。ndf progress enable {v.machine_id} {stage} --reason <理由> で戻す"
    if state == "running" and run.step_progress(v, stage).held_finish is not None:
        return (
            f"工程 {stage} は手で終えて承認を待っている。ndf progress next {v.machine_id} が返す回答のコマンドで回答する。"
            f"直すなら ndf progress retry {v.machine_id} {stage} --reason <理由> --later reopen を打つ"
        )
    return None


def _standing_answer(v: run.View, read: _StageOutput, cwd: Path, root: Path) -> Optional[model.Event]:
    """手で終える工程の有効な承認の事象。無ければ None（承認の記録が無ければ PR を読まない）。

    設計の工程で、承認した PR が未マージなら `model.Refused`。"""
    gate_step = next(s for s in read.stage.steps if s.kind == stages.GATE_KIND)
    rows = v.row_events(read.stage.name)
    found = approval_decide.latest_answer(rows, gate_step.gate, approval_decide.approval_boundary(rows, gate_step.id))
    if found is None or run.event_payload(found)["answer"] != run.APPROVED:
        return None
    # 手で書いた出力の値を、文脈より先に承認の対象にする
    context = {**v.context(), **read.outputs}
    pr = approval_decide.gate_pr(context, read.stage, {}, gate_step.gate, by_hand=True)
    got = standing(TargetReader(shared_hub(cwd)), root)(v, gate_step.gate, pr, found)
    if got.differences:
        return None
    if gate_step.gate == approval_decide.DESIGN_GATE and not got.merged:
        raise model.Refused(
            f"承認した設計 PR #{pr} がマージされていない。承認済みのコミット {got.head} をマージしてから ndf progress finish を打ち直す"
        )
    return found


def _finish_drafts(a: argparse.Namespace, v: run.View, read: _StageOutput, held: bool) -> tuple[list[model.EventDraft], list[str]]:
    """手で終える事象（`held` なら回答待ちの `finish_held`）と、工程が流れたときと同じ形で出力を文脈へ書く事象
    （後の工程と承認の対象が読む）。捨てた出力のキーの警告も返す。"""
    payload: dict[str, Any] = {"output": a.output}
    if held:
        payload["step"] = next(s.id for s in read.stage.steps if s.kind == stages.GATE_KIND)
    kind = FINISH_HELD if held else "manual_finished"
    done = model.EventDraft(kind, command.ACTOR, a.stage, reason=a.reason, payload=payload, rev=v.rev)
    draft, dropped = stages.outputs_draft(read.stage, v.rev, read.outputs, command.ACTOR)
    return [done, *([draft] if draft is not None else [])], dropped


def _hand_finish(a: argparse.Namespace, cwd: Path, st: store.Store, read: _StageOutput) -> tuple[bool, list[int], list[str]]:
    """承認ゲートを持つ工程を手で終える。有効な承認があれば終了、無ければ回答待ちを書く。

    確かめから追記まで作業の排他を持つ。承認の回答は排他を取らないので、追記がぶつかれば読み直して決め直す。"""
    with reopening.work_stopped(st, a.machine_id):
        return _retried_hand_finish(a, cwd, st, read)


def _retried_hand_finish(a: argparse.Namespace, cwd: Path, st: store.Store, read: _StageOutput) -> tuple[bool, list[int], list[str]]:
    """追記がぶつかる間（`command.append_attempts` 回まで）、読み直して決め直す。"""
    attempts = command.append_attempts()
    for attempt in range(1, attempts + 1):
        try:
            return _append_hand_finish(a, cwd, st, read)
        except store.Conflict:
            if attempt == attempts:
                raise
    raise AssertionError("到達しない")


def _append_hand_finish(a: argparse.Namespace, cwd: Path, st: store.Store, read: _StageOutput) -> tuple[bool, list[int], list[str]]:
    """今の記録を読んで 1 回だけ決めて追記する。読んだ後に事象が増えていれば `store.Conflict`。"""
    v = run.read(st, a.machine_id)
    refused = _held_refusal(v, a.stage)
    if refused is not None:
        raise model.Refused(refused)
    held = _standing_answer(v, read, cwd, st.root) is None
    drafts, dropped = _finish_drafts(a, v, read, held)
    with store.transaction(st) as conn:
        return held, store.append_events(conn, a.machine_id, drafts, v.events[-1].seq), dropped


def _cmd_finish(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    read = _read_stage_output(a, st)
    if read.errors:
        raise model.Refused(f"{a.stage} の出力が規約に合わない: {'; '.join(read.errors)}")
    held = False
    if read.stage.gates:
        held, seqs, dropped = _hand_finish(a, cwd, st, read)
    else:
        dropped = []

        def build(rev: int, _snap: stages.Snapshot, events: list[model.Event]) -> list[model.EventDraft]:
            drafts, dropped[:] = _finish_drafts(a, run.View(a.machine_id, rev, _snap, {}, events), read, held=False)
            return drafts

        seqs = command._append(st, a.machine_id, build)
    st.warnings.extend([*_generated_warnings(read), *dropped])
    metrics = {"machine_id": a.machine_id, "seqs": seqs}
    if not held:
        summary = f"ステートマシン {a.machine_id} の {a.stage} に manual_finished を書いた"
        return result.result(command.TOOL, result.OK, summary, metrics=metrics)
    summary = f"工程 {a.stage} は承認の記録が無いので回答を待つ。ndf progress next {a.machine_id} で回答のコマンドを受け取る"
    return result.result(command.TOOL, result.OK, summary, metrics=metrics, next=result.command(command.TOOL, "next", a.machine_id))


def _cmd_check(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    """工程の出力を `finish` と同じ規則で確かめ、書かずに返す。`metrics.outputs` は工程の定義のキー、`warnings` は `finish` が捨てるキー。"""
    read = _read_stage_output(a, st)
    stage = read.stage
    metrics = {"machine_id": a.machine_id, "stage": a.stage, "outputs": list(stage.outputs)}
    if read.errors:
        missing = [{"kind": "missing", "key": k, "source": source} for k, source in read.missing]
        return result.stopped(
            command.TOOL, "refused", f"{a.stage} の出力が規約に合わない: {'; '.join(read.errors)}", metrics=metrics, items=missing
        )
    _, dropped = stages.keep_declared(stage.outputs, read.outputs)
    st.warnings.extend(_generated_warnings(read))
    st.warnings.extend(f"工程 {a.stage} の定義の outputs に無いキーを finish が捨てる: {k}" for k in dropped)
    return result.result(command.TOOL, result.OK, f"{a.stage} の出力は規約に合う", metrics=metrics)


COMMANDS: dict[str, command.Command] = {**command._answer_commands((_cmd_retry, _cmd_finish)), "check": _cmd_check}


def main(argv: list[str]) -> int:
    """サブコマンド `ndf progress` のうち工程を流さないもの。`COMMANDS` はここで受け、ほかは `command` へ渡す。"""
    if not argv or argv[0] not in COMMANDS:
        return command.main(argv)
    return command.run_main(command.parser(), lambda a, cwd: command.with_store(cwd, COMMANDS[a.action], a), argv)
