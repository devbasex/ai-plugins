"""承認ゲートが承認の記録（`ndf.progress.design_record`）を今の PR と照らし、回答に添える（`ndf.progress.gate` の部品。#3702）。

承認の記録を持つ工程（`design_record.applies`）でなければ、どの関数も何もしない。事象を書くのは呼び手である。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

from ndf.progress import design_record, model, run, stages
from ndf.progress.gate.asking import DESIGN_GATE, Question, Target

# 工程の先頭へ戻したことを知らせる文の末尾
RESTARTED = "工程を初めから流し直すので、ndf progress next を打つ"


def _stage_of(v: run.View, q: Question) -> Optional[stages.StageDef]:
    """質問が承認の記録を持つ工程の設計承認ゲートなら、その工程。"""
    stage = v.snap.stage(q.stage)
    return stage if q.gate == DESIGN_GATE and design_record.applies(v.snap, stage) else None


def outdated(v: run.View, q: Question, now: Target) -> Optional[str]:
    """待っている質問の記録が今の PR `now` に当てられない理由。当てられるか、記録を持たない質問なら None。

    PR を読めていなければ `model.Refused`（待ちのまま）。記録を組めないことは理由に返す。
    """
    stage = _stage_of(v, q)
    if stage is None:
        return None
    try:
        record = design_record.from_steps(stage, run.step_progress(v, q.stage).done)
    except model.Refused as exc:
        return exc.reason
    if now.binding() is None:
        raise model.Refused("PR の head とマージ先を読めないので、承認の記録と照らせない")
    return design_record.moved(record, now.repo, now.pr, now.head, now.base)


def reset(v: run.View, q: Question, why: str) -> list[model.EventDraft]:
    """質問の工程を、先頭から設計承認ゲートまで未了に戻す事象。"""
    return design_record.reset_drafts(v, v.snap.stage(q.stage), why)


def moved_after_answer(v: run.View, q: Question, payload: Mapping[str, Any], now: Target) -> Optional[str]:
    """承認した回答の記録と今の PR（head かマージ先など）の違い。違わないか、記録の無い回答・読めない PR なら None。"""
    record = payload.get(design_record.TARGET_KEY)
    if _stage_of(v, q) is None or not isinstance(record, Mapping) or now.pr is None or now.head is None:
        return None
    return design_record.moved(record, now.repo, now.pr, now.head, now.base)


def answer_extra(v: run.View, q: Question, value: str, drop: Sequence[int]) -> dict[str, Any]:
    """承認の回答の payload へ足す記録（足さない回答は空）。課題を外せない形なら `model.Refused`。

    工程「実装」の再承認（変換で求める承認）は、承認の待ちを返した結果が持つ記録をそのまま添える。
    """
    if value != run.APPROVED:
        return {}
    prog = run.step_progress(v, q.stage)
    stage = _stage_of(v, q)
    if stage is not None:
        record = design_record.from_steps(stage, prog.done)
        if drop:
            record = design_record.narrowed(record, drop, Path(v.context()["worktree"]))
        return {design_record.TARGET_KEY: record}
    if v.snap.stage(q.stage).id != design_record.IMPLEMENT_DEFINITION.stem or prog.waiting is None:
        return {}
    held = prog.waiting["report"].get(design_record.TARGET_KEY)
    return {design_record.TARGET_KEY: held} if isinstance(held, Mapping) else {}
