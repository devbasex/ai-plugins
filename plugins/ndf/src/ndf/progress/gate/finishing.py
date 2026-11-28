"""サブコマンド `ndf progress` の、工程を手で扱う `finish`・`check`・`retry`（`ndf.progress.command` から分けた）。

引数の形は `command.parser` が持つ。`main` はこの 3 つを受け、ほかのサブコマンドは `command` へ渡す。
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.adapters.git import Git, GitError
from ndf.adapters.github import GitHub, GitHubError
from ndf.contracts import result
from ndf.core import merge_approval
from ndf.progress import approval_decide, command, definition, delivery_decide, invoke, model, reopening, run, stages, store
from ndf.progress.gate import delivery_check
from ndf.progress.gate.answering import github_observe, shared_hub
from ndf.progress.gate.asking import check_stage_pr, gate_result, gate_stage, off_wait_question, pr_url, question, show_question
from ndf.progress.gate.rechecking import standing
from ndf.progress.gate.targets import TargetReader
from ndf.progress.run_view import (
    DEPLOY_STAGE_ID,
    FINISH_HELD,
    NO_RELEASE,
    NO_RELEASE_REASON,
    RECORD_PR,
    no_release_disables,
    work_root,
)
from ndf.progress.records import github_records, sync_records

# 手で終える操作が MVV 判定のステップを打つ口。テストは差し替える
judge_invoker: run.Invoker = invoke.invoke


@dataclass
class _Judgement:
    """手で終える工程の MVV 判定。追記の打ち直しでは打ち直さない（`done` が真なら `report` を使う）。"""

    done: bool = False
    report: Optional[dict[str, Any]] = None
    warnings: tuple[str, ...] = ()


def revoker(get_hub: Callable[[], GitHub], not_done: str) -> reopening.Revoke:
    """承認の取り消しを PR へ書き、マージキューから外す口。どちらかができなければ `model.Refused`（`not_done` は記録しないこと）。"""

    def revoke(revocations: list[merge_approval.Revocation]) -> None:
        if not revocations:
            return
        try:
            hub = get_hub()
            merge_approval.write_revocations(hub, revocations, hub.viewer())
        except (merge_approval.TrailError, GitHubError, GitError) as exc:
            numbers = "・".join(f"#{n}" for n in sorted({r.target.number for r in revocations}))
            raise model.Refused(f"PR {numbers} の承認の取り消しとマージキューからの取り外しを終えられないので、{not_done}: {exc}") from None

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
        planned, seqs = _append_reopen(a, st, revoker(shared_hub(cwd), "開き直さない"))
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
                seqs = store.append_events(conn, a.machine_id, planned.drafts, v.events[-1].seq)
            return planned, seqs
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
    if (a.no_release or a.record_pr is not None) and stage.id != DEPLOY_STAGE_ID:
        raise model.Refused(f"--record-pr と --no-release はデプロイの工程だけで指定できる（{a.stage} はデプロイの工程でない）")
    errs, missing = definition.check_stage_output(stage, output, handwritten=bool(stage.gates), no_release=a.no_release)
    outputs = output["outputs"] if not errs and "outputs" in output else {}
    made = definition.generated_outputs(stage.id) if stage.gates else ()
    generated = [k for k in made if k in outputs]
    return _StageOutput(stage, errs, missing, {k: v for k, v in outputs.items() if k not in generated}, generated)


def _generated_warnings(read: _StageOutput) -> list[str]:
    return [f"工程 {read.stage.name} の {k} は NDF が作るので、手で書いた値は使わない" for k in read.generated]


def _release_facts(a: argparse.Namespace, v: run.View, read: _StageOutput, cwd: Path, reader: TargetReader) -> dict[str, Any]:
    """手で終えるデプロイの工程のリリース記録と CD の結果を確かめ、事象に残す値を返す。ほかの工程は空。

    条件に合わなければ `model.Refused`、読めなければ `delivery_decide.Unreadable`。承認の有無は問わない（#4520）。"""
    if read.stage.id != DEPLOY_STAGE_ID:
        return {}
    context = {**v.context(), **read.outputs}
    record_pr = a.record_pr if a.record_pr is not None else delivery_check.default_record_pr(context)
    got = delivery_check.check(shared_hub(cwd), cwd, context, record_pr, a.no_release, reader.pull)
    if not got.ok:
        raise model.Refused(f"工程 {a.stage} を終えられない: {got.why}（読んだ PR #{record_pr}。別の PR は --record-pr <番号>）")
    facts: dict[str, Any] = {RECORD_PR: record_pr}
    if a.no_release:
        facts.update({NO_RELEASE: True, NO_RELEASE_REASON: got.no_release_reason})
    if got.cd_result is not None:
        facts["cd_result"] = got.cd_result
    return facts


def _standing_answer(v: run.View, read: _StageOutput, root: Path, reader: TargetReader, facts: Mapping[str, Any]) -> Optional[model.Event]:
    """手で終える工程の有効な承認の事象。無ければ None（承認の記録が無ければ PR を読まない）。

    設計の工程で、承認した PR が未マージなら `model.Refused`。"""
    gate_step = next(s for s in read.stage.steps if s.kind == stages.GATE_KIND)
    rows = v.row_events(read.stage.name)
    reason = facts.get(NO_RELEASE_REASON)
    found = approval_decide.counted_approval(rows, gate_step.gate, gate_step.id, closing=True, no_release_reason=reason)
    if found is None:
        return None
    # 手で書いた出力の値を、文脈より先に承認の対象にする
    context = {**v.context(), **read.outputs}
    pr = approval_decide.gate_pr(context, read.stage, {}, gate_step.gate, by_hand=True)
    got = standing(reader, root)(v, gate_step.gate, pr, found)
    if got.differences:
        return None
    if gate_step.gate == approval_decide.DESIGN_GATE and not got.merged:
        raise model.Refused(
            f"承認した設計 PR #{pr} がマージされていない。承認済みのコミット {got.head} をマージしてから ndf progress finish を打ち直す"
        )
    return found


def _finish_refusal(v: run.View, stage: str) -> Optional[str]:
    """手で終えられない状態の理由と、代わりに打てるコマンド。終えられる状態は None（追記の確かめに任せる）。"""
    if model.is_terminal(v.whole):
        return f"ステートマシンは{model.label(v.whole)}で、工程を終えられない。状態は ndf progress show {v.machine_id} で確かめる"
    found = v.snap.stage(stage)
    row = v.rows.get(stage)
    state = row.state if row is not None else None
    if found.gates and state == command.TOGGLE_KINDS["disable"]:
        return f"承認ゲートを持つ工程 {stage} は無効のままでは終えない。ndf progress enable {v.machine_id} {stage} --reason <理由> で戻す"
    if state == run.FINISHED:
        also = f"。変更を求められたなら ndf progress question {v.machine_id} {stage} の質問へ回答する" if found.gates else ""
        return (
            f"工程 {stage} は{model.label(state)}で、手で終えられない。直すなら "
            f"ndf progress retry {v.machine_id} {stage} --reason <理由> --later reopen|keep を打つ{also}"
        )
    if state != "running":
        return None
    if run.step_progress(v, stage).held_finish is not None:
        return (
            f"工程 {stage} は手で終えて承認を待っている。ndf progress next {v.machine_id} が返す回答のコマンドで回答する。"
            f"直すなら ndf progress retry {v.machine_id} {stage} --reason <理由> --later reopen を打つ"
        )
    m = run.decide(v)
    if m.action == run.Action.GATE and m.stage is not None and m.stage.name == stage:
        return f"工程 {stage} は承認ゲートの回答を待っている。ndf progress next {v.machine_id} が返す回答のコマンドで回答する"
    return f"工程 {stage} は{model.label(state)}で、手で終えられない。ndf progress wait {v.machine_id} で作業の終わりを待つ"


def ask_question(st: store.Store, machine_id: str, stage: str, pr: Optional[int], cwd: Path) -> dict[str, Any]:
    """承認ゲートの工程の質問・承認資料・回答のコマンドを作る（`ndf progress question`）。回答を待っていれば今の質問を返す。

    `--pr` は設計承認ゲートだけで受け、同じリポジトリの、head のブランチが工程のブランチの PR に限る。リリースのリクエストは
    出さない。"""
    v = run.read(st, machine_id)
    found, step = gate_stage(v, stage)
    hub = shared_hub(cwd)
    observe = github_observe(cwd, st.root, hub, TargetReader(hub))
    try:
        repo: Optional[str] = Git(cwd).remote_repo()
    except GitError:  # 表示の URL だけに使うので、無ければ番号で示す
        repo = None
    number = _question_pr(v, found, step.gate, pr)
    m = run.decide(v)
    waits = m.action == run.Action.GATE and m.stage is not None and m.stage.name == stage and invoke.is_listen(step)
    q = question(v, m, repo=repo) if waits else off_wait_question(v, stage, pr=number, repo=repo)
    if not waits and found.id == DEPLOY_STAGE_ID and delivery_check.released(hub)(v, stage):
        q = replace(q, after_release=True)
    # 対象の PR の記録が無い待ちの質問は、`--pr` の番号を対象にする
    adopted = waits and q.pr is None and number is not None
    if adopted:
        q = replace(q, pr=number, pr_url=pr_url(repo, number))
    if adopted or not waits:
        check_stage_pr(v, found, step.gate, q.pr, observe(q, material=False))
    q, path = show_question(st.root, v, q, observe, private=st.private)
    # 結果の `tool` は打ったコマンド（回答のルートは `ndf answer` のまま）
    return gate_result(q, {"machine_id": machine_id, "stage": stage}, path, tool=command.TOOL)


def _question_pr(v: run.View, stage: stages.StageDef, gate: Optional[str], pr: Optional[int]) -> Optional[int]:
    """質問の対象の PR。設計承認ゲートは記録した `design_pr`、無ければ `--pr`。ほかのゲートは文脈の `pr` だけ。"""
    known = approval_decide.target_pr(v, stage, gate)
    again = f"ndf progress question {v.machine_id} {stage.name}"
    if gate != approval_decide.DESIGN_GATE:
        if pr is not None:
            raise model.Refused(f"工程 {stage.name} の承認ゲートは --pr を受けない（対象は文脈の pr）。{again} を打つ")
        return known
    if known is not None and pr is not None and pr != known:
        raise model.Refused(f"工程 {stage.name} の対象の PR は #{known}。{again}（--pr は省くか #{known}）を打つ")
    if known is None and pr is None:
        raise model.Refused(f"工程 {stage.name} の設計 PR の記録が無い。{again} --pr <番号> を打つ")
    return known if known is not None else pr


def _cmd_off_wait(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    return ask_question(st, a.machine_id, a.stage, a.pr, cwd)


def _judge_step(stage: stages.StageDef) -> Optional[stages.StepDef]:
    """工程の設計の MVV 判定のステップ。無ければ None（デプロイの判定は supervisor が打つ）。"""
    found = model.MVV_GATE_STEPS[approval_decide.DESIGN_GATE]
    return next((s for s in stage.steps if s.id == found), None)


def _judge_design(st: store.Store, v: run.View, read: _StageOutput, cwd: Path) -> _Judgement:
    """設計の工程の MVV 判定のステップを、自動の流れと同じく入力の作業場所で打つ（#4520 の決定 7）。承認の対象の PR を
    `--expect-pr` で渡す。打てない・失敗した判定は結果を持たず、人へ問う。"""
    step = _judge_step(read.stage)
    assert step is not None
    failed = "MVV 判定をできないので、承認は人へ問う: "
    # 手で書いた出力の値を、文脈より先に判定の入力と対象にする
    context = {**v.context(), **read.outputs}
    pr = approval_decide.gate_pr(context, read.stage, {}, approval_decide.DESIGN_GATE, by_hand=True)
    if pr is None:
        return _Judgement(True, warnings=(failed + "設計 PR の番号が無い",))
    try:
        inputs = invoke.resolve_inputs(read.stage, context)
    except invoke.StageInputError as exc:
        return _Judgement(True, warnings=(failed + str(exc),))
    made = invoke.stage_run(read.stage, step, inputs)
    made = replace(made, args={**made.args, "argv": [*made.args["argv"], "--expect-pr", str(pr)]})
    made = run.kept_by_objection(v, read.stage, step, made)
    location = run.step_location(step.id, run.step_progress(v, read.stage.name))
    got = judge_invoker(made, cwd, work_root(st.root, v.machine_id) / read.stage.id / location, None)
    if got.status != result.OK:
        why = got.cause["detail"] if got.cause is not None else "判定のステップが終わらない"
        return _Judgement(True, warnings=(*got.warnings, failed + why))
    errs = definition.validate_io(step.output_ref, got.report) if step.output_ref else []
    if errs or model.MVV_KEY not in got.outputs:
        return _Judgement(True, warnings=(*got.warnings, failed + "判定の結果が出力の規約に合わない " + "; ".join(errs)))
    return _Judgement(True, got.report, tuple(got.warnings))


def _finish_drafts(
    a: argparse.Namespace,
    v: run.View,
    read: _StageOutput,
    held: bool,
    judged: Optional[_Judgement] = None,
    facts: Optional[Mapping[str, Any]] = None,
) -> tuple[list[model.EventDraft], list[str]]:
    """手で終える事象（`held` なら回答待ちの `finish_held`）と、工程が流れたときと同じ形で出力を文脈へ書く事象
    （後の工程と承認の対象が読む）。捨てた出力のキーの警告も返す。

    `judged` は回答待ちの工程の MVV 判定で、結果を回答待ちの事象に持たせ、出力にも書く。`facts` はデプロイの工程の
    確かめた値で、事象に残す。リリースしないで終了を書くなら、出したバージョンを読む工程を同じ追記で無効にする。"""
    facts = facts if facts is not None else {}
    payload: dict[str, Any] = {"output": a.output, **facts}
    outputs = read.outputs
    if held:
        payload["step"] = next(s.id for s in read.stage.steps if s.kind == stages.GATE_KIND)
    step = _judge_step(read.stage) if held else None
    if step is not None:
        report = judged.report if judged is not None else None
        payload["judged"] = {"step": step.id, "report": report}
        if report is not None:
            outputs = {**outputs, model.MVV_KEY: report["outputs"][model.MVV_KEY]}
    kind = FINISH_HELD if held else "manual_finished"
    done = model.EventDraft(kind, command.ACTOR, a.stage, reason=a.reason, payload=payload, rev=v.rev)
    draft, dropped = stages.outputs_draft(read.stage, v.rev, outputs, command.ACTOR)
    off = no_release_disables(v, a.stage) if not held and facts.get(NO_RELEASE) else []
    return [done, *([draft] if draft is not None else []), *off], dropped


class _HandFinished(NamedTuple):
    """承認ゲートを持つ工程を手で終えた結果。`held` は回答待ちを書いたか、`skips` は MVV 判定が承認を省くと決めたか。"""

    held: bool
    seqs: list[int]
    dropped: list[str]
    skips: bool = False


def _hand_finish(a: argparse.Namespace, cwd: Path, st: store.Store, read: _StageOutput) -> _HandFinished:
    """承認ゲートを持つ工程を手で終える。有効な承認があれば終了、無ければ回答待ちを書く。

    確かめから MVV 判定、追記まで作業の排他を持つ。承認の回答は排他を取らないので、追記がぶつかれば読み直して決め直す。"""
    with reopening.work_stopped(st, a.machine_id):
        return _retried_hand_finish(a, cwd, st, read)


def _retried_hand_finish(a: argparse.Namespace, cwd: Path, st: store.Store, read: _StageOutput) -> _HandFinished:
    """追記がぶつかる間（`command.append_attempts` 回まで）、読み直して決め直す。MVV 判定は打ち直さない。"""
    attempts = command.append_attempts()
    judged = _Judgement()
    for attempt in range(1, attempts + 1):
        try:
            return _append_hand_finish(a, cwd, st, read, judged)
        except store.Conflict:
            if attempt == attempts:
                raise
    raise AssertionError("到達しない")


def _append_hand_finish(a: argparse.Namespace, cwd: Path, st: store.Store, read: _StageOutput, judged: _Judgement) -> _HandFinished:
    """今の記録を読んで 1 回だけ決めて追記する。読んだ後に事象が増えていれば `store.Conflict`。"""
    v = run.read(st, a.machine_id)
    refused = _finish_refusal(v, a.stage)
    if refused is not None:
        raise model.Refused(refused)
    reader = TargetReader(shared_hub(cwd))
    facts = _release_facts(a, v, read, cwd, reader)
    held = _standing_answer(v, read, st.root, reader, facts) is None
    drafts, dropped = _finish_drafts(a, v, read, held, facts=facts)

    def append(skips: bool = False) -> _HandFinished:
        if facts.get("cd_result") == delivery_decide.UNKNOWN:
            st.warnings.append("CD の実行の結果が分からず、未確定の記録を受けた（cd_result: unknown）")
        with store.transaction(st) as conn:
            return _HandFinished(held, store.append_events(conn, a.machine_id, drafts, v.events[-1].seq), dropped, skips)

    if not held:
        return append()
    # 回答待ちを書くと先の承認は境界の前になるので、PR の証跡も先に取り消す。回答待ちを書けない状態なら、判定と取り消しの前に断る
    model.check_event("sprint", model.rows_for("sprint", v.events, drafts[0]), v.whole, drafts[0])
    skips = False
    if _judge_step(read.stage) is not None:
        if not judged.done:
            got = _judge_design(st, v, read, cwd)
            judged.done, judged.report, judged.warnings = got.done, got.report, got.warnings
        drafts, dropped = _finish_drafts(a, v, read, held, judged, facts)
        skips = _skips(v, read.stage, judged)
    # 取り消してから追記するまでは、回答の送り直しが取り消した承認を PR へ戻さないように、回答と共有する排他を持つ
    with store.approval_lock_after_answer(st, a.machine_id, time.sleep):
        with store.transaction(st) as conn:
            moved = store.last_seq(conn, a.machine_id) != v.events[-1].seq
        if moved:
            raise store.Conflict("排他を待つ間に事象が増えた")  # 確定した回答を読み直して、取り消す前に決め直す
        revoker(shared_hub(cwd), "回答待ちを書かない")(approval_decide.stage_revocations(v, a.stage))
        done = append(skips)
    st.warnings.extend(judged.warnings)
    return done


def _skips(v: run.View, stage: stages.StageDef, judged: _Judgement) -> bool:
    """次の `next` が MVV 判定で承認を省くか（`run.decide` が回答待ちで見る条件のうち、記録を書く前に分かるもの）。"""
    step = _judge_step(stage)
    gate = approval_decide.DESIGN_GATE
    if step is None or judged.report is None or approval_decide.objection_remains(v.stage_events(stage.name), gate):
        return False
    return model.mvv_skip({step.id: judged.report}, step.id, gate) is not None


def _cmd_finish(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    read = _read_stage_output(a, st)
    if read.errors:
        raise model.Refused(f"{a.stage} の出力が規約に合わない: {'; '.join(read.errors)}")
    held = skips = False
    if read.stage.gates:
        try:
            held, seqs, dropped, skips = _hand_finish(a, cwd, st, read)
        except delivery_decide.Unreadable as exc:
            return result.stopped(command.TOOL, exc.kind, exc.detail)
    else:
        dropped = []

        def build(rev: int, _snap: stages.Snapshot, events: list[model.Event]) -> list[model.EventDraft]:
            now = run.View(a.machine_id, rev, _snap, {}, events)
            refused = _finish_refusal(now, a.stage)
            if refused is not None:
                raise model.Refused(refused)
            drafts, dropped[:] = _finish_drafts(a, now, read, held=False)
            return drafts

        seqs = command._append(st, a.machine_id, build)
    st.warnings.extend([*_generated_warnings(read), *dropped])
    metrics = {"machine_id": a.machine_id, "seqs": seqs}
    if not held:
        summary = f"ステートマシン {a.machine_id} の {a.stage} に manual_finished を書いた"
        return result.result(command.TOOL, result.OK, summary, metrics=metrics)
    summary = f"工程 {a.stage} は承認の記録が無いので回答を待つ。ndf progress next {a.machine_id} で回答のコマンドを受け取る"
    if skips:
        summary = f"工程 {a.stage} は MVV 判定が承認を省くと決めた。承認は ndf progress next {a.machine_id} が記録する"
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


COMMANDS: dict[str, command.Command] = {
    **command._answer_commands((_cmd_retry, _cmd_finish)),
    "check": _cmd_check,
    "question": _cmd_off_wait,
}


def main(argv: list[str]) -> int:
    """サブコマンド `ndf progress` のうち工程を流さないもの。`COMMANDS` はここで受け、ほかは `command` へ渡す。"""
    if not argv or argv[0] not in COMMANDS:
        return command.main(argv)
    return command.run_main(command.parser(), lambda a, cwd: command.with_store(cwd, COMMANDS[a.action], a), argv)
