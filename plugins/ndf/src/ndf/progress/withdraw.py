"""承認の回答を無効にするときに、回答が外した課題を戻す（課題を繰り越したレーンの行と、文脈の `issues` の事象）。

回答を読む前に取り消す経路（`gate.answering`）と、読んだ後に `approval_stale` で工程を流し直す経路（`run`）が同じ関数を使う。
どちらも呼び手が、返った事象を工程を未了に戻す事象と同じトランザクションで書く。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Optional

from ndf.progress import lane, model, run_view, stages
from ndf.progress.folding import step_progress


class Blocked(model.Refused):
    """繰り越し行を取り消せないので、回答を無効にできない。呼び手のトランザクションは何も書かずに戻す。"""


def take_back(conn: Any, v: run_view.View, stage: str, answered: model.Event, reason: str) -> list[model.EventDraft]:
    """無効にする回答が外した課題の繰り越しを取り消し、今のスプリントの文脈の `issues` へ戻す事象を返す。

    別のレーンへ移した繰り越し行は、移した先を取り消す。繰り越し行を先に取り消してあれば、課題を戻すだけにする。
    移した先が開始済み（実行中か完了）なら、その行の課題は戻さない（同じ課題を 2 か所で扱わない）。
    未着手の行を取り消せなければ `Blocked`。
    """
    payload = run_view.event_payload(answered)
    dropped = set(payload.get("dropped") or ())
    if not dropped:
        return []
    carried = payload.get("carried_row")
    elsewhere = _cancel_carried(conn, v.machine_id, carried, reason) if carried else set()
    if dropped <= elsewhere:
        return []
    earlier = [ev for ev in v.events if ev.seq < answered.seq]
    before = stages.compose_context(v.doc["context"], stages.recorded_outputs(earlier, v.rev))
    outputs = {"issues": [n for n in before["issues"] if n not in dropped & elsewhere]}
    return [model.EventDraft(stages.OUTPUTS_EVENT, run_view.CONDUCTOR, stage, payload={"outputs": outputs}, rev=v.rev)]


def _cancel_carried(conn: Any, machine_id: str, carried: str, reason: str) -> set[int]:
    """繰り越し行 `carried` の未着手の行を取り消し、開始済みで取り消せない行の課題を返す。

    レーンの移動（`ndf lanes move`）は元の行を取り消して同じ入力の行を移した先に足すので、入力が同じ行をすべて見る。
    """
    found = lane.sprint_row(conn, machine_id)
    if found is None:
        return set()
    plan_id, name, own = found
    lane.lock_plan(conn, plan_id)
    view, rows = lane.read_view(conn, plan_id)
    source = next((r for r in rows[name] if r.key == carried), None)
    if source is None:
        return set()
    copies = [(ln, r) for ln, held in rows.items() for r in held if r.input == source.input and (ln, r.key) != (name, own.key)]
    started: set[int] = set()
    for ln, row in copies:
        if row.state == lane.PENDING:
            try:
                lane.cancel_row(conn, plan_id, ln, row.key, lane.RowChange(run_view.CONDUCTOR, reason))
            except model.Refused as exc:
                raise Blocked(
                    f"繰り越し行 {ln}/{row.key} を取り消せないので、外した課題を戻せず承認を無効にできない（{exc}）。"
                    f"ndf lanes set {view.name} <after で指すレーン> --after <残す指定> --reason <理由> で指定を外してから打ち直す"
                ) from None
        elif row.state != lane.CANCELLED:
            started |= row.issues()
    return started


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
