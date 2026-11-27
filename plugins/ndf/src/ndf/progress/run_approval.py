"""承認ゲートの回答を読む手と、回答が見た対象（`ndf.progress.run` から分けた）。

次の 1 手を決めて流すのは `run` で、`run` がここを読む。import が輪にならないように、ここは `run` を読まず、
駆動の側の操作は `Hands`（`run.Drive` が持つ）で受ける。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Optional, Protocol

from ndf.contracts import result
from ndf.progress import invoke, model, stages
from ndf.progress.folding import Action, Move
from ndf.progress.run_view import (
    ACTOR,
    ANSWERED,
    CONDUCTOR,
    FINISHED,
    STEP_EVENT,
    StageAnswers,
    View,
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

    省いた回答を書いたら、同じ駆動では読まずに `next` を返して止まる。回答を読む前の検証（`gate.recheck`）はサブコマンドの
    エントリポイントでだけ打つため、続けて読むと判定の後に進んだ PR の head でも承認ゲートを省いてしまう。次の `next` が検証を通してから読む。
    数えない MVV 判定の回答（`model.mvv_answer_void`）と、省けると決めた判定の無い手は、事象を書かずに承認ゲートの待ちを返す（#3959）。
    """
    assert m.stage is not None and m.step is not None and m.progress is not None
    waiting = replace(m, action=Action.GATE, event=None)
    if m.event is not None and model.mvv_answer_void(event_payload(m.event), m.step.gate):
        return d.report(v, waiting)
    if m.event is None:
        held = model.mvv_skip(m.progress.done, m.stage.mvv_before(m.step.id), m.step.gate)
        if held is None:
            return d.report(v, waiting)
        qid = model.question_id(v.machine_id, v.rev, m.stage.name, m.step.id, m.progress.anchor)
        payload, reason = model.mvv_answer(m.step.id, invoke.LISTEN, APPROVED, held, qid)
        d.write(model.EventDraft(ANSWERED, CONDUCTOR, m.stage.name, reason=reason, payload=payload, rev=v.rev))
        return d.proceed(v, m, f"{reason}。回答は検証を通してから読むので next を打つ")
    if event_payload(m.event)["answer"] == APPROVED:
        rep = gate_report(m.step, m.event)
        payload = {"step": m.step.id, "status": FINISHED, "report": rep, "generation": m.progress.generation}
        d.write(model.EventDraft(STEP_EVENT, ACTOR, m.stage.name, payload=payload, rev=v.rev))
        return None
    detail = f"承認ゲート {m.step.gate} で承認されなかった（{m.event.draft.reason}）"
    d.refuse(v, m, detail)
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
    seen = p.get("seen")
    if not isinstance(seen, dict):
        return None
    gate_id = p.get("gate_id")
    carried = None
    if seen.get("production") is False and gate_id:
        carried = read_accepted_head(work_root(root, answers.machine_id), str(gate_id))
    return {**seen, "head": carried} if carried else dict(seen)
