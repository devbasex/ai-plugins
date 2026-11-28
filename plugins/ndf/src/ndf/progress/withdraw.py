"""承認の回答を無効にするときに、回答が外した課題を戻す（文脈の `issues` の事象と、課題を繰り越したレーンの行）。

回答を読む前に取り消す経路（`gate.answering`）と、読んだ後に `approval_stale` で工程を流し直す経路（`run`）が同じ関数を使う。
どちらも呼び手が、工程を未了に戻す事象と同じトランザクションで書く。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Optional

from ndf.progress import lane, model, run_view, stages
from ndf.progress.folding import step_progress


def undropped(v: run_view.View, stage: str, answered: model.Event) -> list[model.EventDraft]:
    """無効にする回答が外した課題を、今のスプリントの文脈の `issues` へ戻す事象（回答の前の値）。"""
    if not run_view.event_payload(answered).get("dropped"):
        return []
    earlier = [ev for ev in v.events if ev.seq < answered.seq]
    before = stages.compose_context(v.doc["context"], stages.recorded_outputs(earlier, v.rev))
    payload = {"outputs": {"issues": before["issues"]}}
    return [model.EventDraft(stages.OUTPUTS_EVENT, run_view.CONDUCTOR, stage, payload=payload, rev=v.rev)]


def uncarry(conn: Any, machine_id: str, answered: model.Event, reason: str) -> None:
    """無効にする回答が外した課題を入れたレーンの行を取り消す。"""
    carried = run_view.event_payload(answered).get("carried_row")
    if not carried:
        return
    found = lane.sprint_row(conn, machine_id)
    if found is None:
        return
    plan_id, name, _row = found
    lane.cancel_row(conn, plan_id, name, carried, lane.RowChange(run_view.CONDUCTOR, reason))


def consumed_answer(v: run_view.View, s: stages.StageDef, gate: str, drafts: Sequence[model.EventDraft]) -> Optional[model.Event]:
    """`drafts` が承認ゲート `gate` の済んだステップを未了に戻すなら、そのステップが読んだ回答の事象。ほかは None。"""
    at = s.gate_index(gate)
    if at is None:
        return None
    step = s.steps[at].id
    done = step_progress(v, s.name).done
    reset = any(
        d.kind == run_view.STEP_EVENT and d.payload is not None and d.payload["step"] == step and d.payload["status"] == stages.RESET
        for d in drafts
    )
    if not reset or step not in done:
        return None
    metrics = done[step].get("metrics")
    seq = metrics.get("seq") if isinstance(metrics, dict) else None
    return next((ev for ev in v.row_events(s.name) if ev.draft.kind == run_view.ANSWERED and ev.seq == seq), None)
