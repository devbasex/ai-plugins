"""承認ゲートの回答を読む手と、回答が見た対象（`ndf.progress.run` から分けた）。

次の 1 手を決めて流すのは `run` で、`run` がここを読む。import が輪にならないように、ここは `run` を読まず、
駆動の側の操作は `Hands`（`run.Drive` が持つ）で受ける。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Optional, Protocol

from ndf.contracts import result
from ndf.progress import approval_decide, invoke, model, stages, store
from ndf.progress.folding import Action, Move
from ndf.progress.run_view import (
    ACTOR,
    ANSWERED,
    CONDUCTOR,
    FINISHED,
    STEP_EVENT,
    StageAnswers,
    View,
    closes_only,
    event_payload,
    read_accepted_head,
    work_root,
)

TOOL = __package__.rpartition(".")[2]
# 回答の語（承認）
APPROVED = invoke.APPROVED


class Hands(Protocol):
    """回答を読む手が使う駆動の操作。"""

    machine_id: str
    env: Any  # `run.Env`
    st: store.Store

    def warn(self, text: str) -> None: ...

    def write(self, *drafts: model.EventDraft) -> None: ...

    def synced(self) -> None: ...

    def report(self, v: View, m: Move) -> dict[str, Any]:
        """副作用の無い 1 手の結果。"""
        ...

    def proceed(self, v: View, m: Move, summary: str) -> dict[str, Any]:
        """`next` を打ち直させる結果。"""
        ...

    def refuse(self, v: View, m: Move, detail: str) -> None:
        """今の手の行を、断られた失敗にする。"""
        ...


def gate_report(step: stages.StepDef, answer: model.Event) -> dict[str, Any]:
    """承認ゲートのステップの出力（回答を持つ結果 JSON）。"""
    p = event_payload(answer)
    summary = f"承認ゲート {step.gate} の回答: {p['answer']}（{answer.draft.reason}）"
    return result.result(TOOL, result.OK, summary, metrics={"answer": p["answer"], "seq": answer.seq})


def consume(d: Hands, v: View, m: Move) -> Optional[dict[str, Any]]:
    """承認ゲートの回答を読む。回答が無ければ直前の MVV 判定が省けると決めた承認ゲートで、`service:mvv` の承認を書く。

    省いた回答を書いたら、同じ駆動では読まずに `next` を返して止まる。同じ呼び出しは判定の前に読んだ対象を持ちうるので、
    続けて読むと判定の後に進んだ PR の head でも承認ゲートを省いてしまう。次の `next` が読み直して照らしてから読む。
    読む回答は、呼び手（`run._compared_move`）が作業の排他の中で対象と照らしてある。
    数えない MVV 判定の回答（`model.mvv_answer_void`）と、省けると決めた判定の無い手は、事象を書かずに承認ゲートの待ちを返す（#3959）。
    """
    assert m.stage is not None and m.step is not None and m.progress is not None
    if m.progress.held_finish is not None:
        return consume_held(d, v, m)
    waiting = replace(m, action=Action.GATE, event=None)
    if m.event is not None and model.mvv_answer_void(event_payload(m.event), m.step.gate):
        return d.report(v, waiting)
    if m.event is None:
        # 同じ対象への有効な先の承認があれば、MVV の回答を書かずにそれを使う
        found = standing_approval(d, v, m.stage, m.step)
        if found is not None:
            _pass_gate(d, v, m, found)
            return None
        held = model.mvv_skip(m.progress.done, m.stage.mvv_before(m.step.id), m.step.gate)
        if held is None:
            return d.report(v, waiting)
        qid = model.question_id(v.machine_id, v.rev, m.stage.name, m.step.id, m.progress.anchor)
        payload, reason = model.mvv_answer(m.step.id, invoke.LISTEN, APPROVED, held, qid)
        d.write(model.EventDraft(ANSWERED, CONDUCTOR, m.stage.name, reason=reason, payload=payload, rev=v.rev))
        return d.proceed(v, m, f"{reason}。回答は検証を通してから読むので next を打つ")
    if event_payload(m.event)["answer"] == APPROVED:
        _pass_gate(d, v, m, m.event)
        return None
    detail = f"承認ゲート {m.step.gate} で承認されなかった（{m.event.draft.reason}）"
    d.refuse(v, m, detail)
    d.synced()
    return None


def _standing(d: Hands, v: View, s: stages.StageDef, step: stages.StepDef, answered: model.Event) -> approval_decide.Standing:
    """承認の回答 `answered` を、今の工程の承認の対象に照らす。"""
    return d.env.standing(v, step.gate, approval_decide.target_pr(v, s, step.gate), answered)


def standing_approval(d: Hands, v: View, s: stages.StageDef, step: stages.StepDef) -> Optional[model.Event]:
    """承認ゲート `step` の有効な先の承認の事象。無ければ None（承認の記録が無ければ対象を読まない）。"""
    found = approval_decide.counted_approval(v.row_events(s.name), step.gate, step.id)
    if found is None:
        return None  # 数えない回答は、対象を読むまでもなく承認でない
    return None if _standing(d, v, s, step, found).differences else found


def _pass_gate(d: Hands, v: View, m: Move, answered: model.Event) -> None:
    """承認の回答 `answered` で承認ゲートのステップを済ませる。"""
    assert m.stage is not None and m.step is not None and m.progress is not None
    rep = gate_report(m.step, answered)
    payload = {"step": m.step.id, "status": FINISHED, "report": rep, "generation": m.progress.generation}
    d.write(model.EventDraft(STEP_EVENT, ACTOR, m.stage.name, payload=payload, rev=v.rev))


def prior_approval(d: Hands, v: View, m: Move) -> Optional[dict[str, Any]]:
    """自動の流れの承認ゲートの待ち。有効な先の承認があれば質問せずにステップを済ませ、無ければ待ちを返す。

    手で終えた回答待ちは、回答を `consume_held` が読むので、ここでは先の承認を使わない。"""
    assert m.stage is not None
    if m.step is None or m.progress is None or not invoke.is_listen(m.step) or m.progress.held_finish is not None:
        return d.report(v, m)
    found = standing_approval(d, v, m.stage, m.step)
    if found is None:
        return d.report(v, m)
    _pass_gate(d, v, m, found)
    return None


def consume_held(d: Hands, v: View, m: Move) -> Optional[dict[str, Any]]:
    """手で終えた回答待ちの工程の回答を読み、有効な承認なら終了を書く（工程のステップは流さない）。

    承認の対象が変わっていれば今の対象で質問し直し、設計は承認した PR がマージ済みでなければ終了を書かない。"""
    assert m.stage is not None and m.step is not None and m.progress is not None and m.progress.held_finish is not None
    if m.event is None:
        return d.report(v, replace(m, action=Action.GATE))
    if event_payload(m.event)["answer"] != APPROVED:
        d.refuse(v, m, f"承認ゲート {m.step.gate} で承認されなかった（{m.event.draft.reason}）")
        d.synced()
        return None
    got = _standing(d, v, m.stage, m.step, m.event)
    if got.differences:
        why = "承認の後に対象が変わった: " + " / ".join(got.differences)
        if got.merged:
            why = f"{why}（承認と違う内容がマージされた。今の head {got.head}）"
        wait = {"step": m.step.id, "status": "waiting", "report": None, "generation": m.progress.generation, "reason": why}
        # 取り消してから境界を進めるまでは、回答の送り直しが取り消した承認を PR へ戻さないように、回答と共有する排他を持つ
        with store.approval_lock(d.st, v.machine_id):
            d.env.revoke(approval_decide.stage_revocations(v, m.stage.name))
            d.write(model.EventDraft(STEP_EVENT, ACTOR, m.stage.name, payload=wait, rev=v.rev))
        d.warn(why)
        return None
    if m.step.gate == approval_decide.DESIGN_GATE and not got.merged:
        detail = (
            f"承認した設計 PR がマージされていない。承認済みのコミット {got.head} をマージしてから ndf progress next {v.machine_id} を打つ"
        )
        return result.stopped(TOOL, "precondition", detail, next=result.command(TOOL, "next", v.machine_id))
    reason = f"手で終えた工程の承認（回答の事象 {m.event.seq}）を読んだ"
    payload = {"output": m.progress.held_finish["output"], "approval": m.event.seq}
    d.write(model.EventDraft(FINISHED, ACTOR, m.stage.name, reason=reason, payload=payload, rev=v.rev))
    d.synced()
    return None


def approved_seen(answers: StageAnswers, root: Path) -> Optional[dict[str, Any]]:
    """工程の承認ゲートの最新の承認の回答が見た対象（事象の `seen`）。無ければ None（#4409 の決定 5）。

    利用者の回答でも、MVV 判定が通した回答（`service:mvv`）でも同じ形で読む。マージが本番へ反映されない（`production` が偽）
    承認は、出し直さずに繰り上げた head（`gate.recheck`）を重ねる。分からない・本番へ反映される承認には重ねない。
    `root` は状態の保存先。最新の承認の回答は `answers` の走査から得る（#4513）。
    """
    ev = answers.approved()
    if ev is None:
        return None
    p = event_payload(ev)
    if closes_only(p) or approval_decide.saw_another_pr(answers.view, answers.stage, p):
        return None
    seen = p.get("seen")
    if not isinstance(seen, dict):
        return None
    gate_id = p.get("gate_id")
    carried = None
    if seen.get("production") is False and gate_id:
        carried = read_accepted_head(work_root(root, answers.machine_id), str(gate_id))
    return {**seen, "head": carried} if carried else dict(seen)
