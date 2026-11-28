"""ステートマシンの事象の列を、工程の中のステップの進みへ畳み込む（純粋な処理。ファイルもプロセスも触らない）。

行のステップの進み（`StepProgress`）・次の 1 手の型（`Action`・`Move`）・回答の語を持つ。事象の種類の定数と、1 回の取得で読んだステートマシン（`View`）は
`ndf.progress.run_view` が持つ。次の 1 手を決めて流すのは `ndf.progress.run` である。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Optional

from ndf.contracts import facts, result
from ndf.progress import invoke, model, stages
from ndf.progress.run_view import (
    ANSWERED,
    APPROVAL_POSTING,
    FAILED,
    FINISH_HELD,
    FINISHED,
    REJECTING_KEY,
    REOPENED,
    STARTED,
    STEP_EVENT,
    View,
    event_payload,
    posts_rejection,
)


def answer_word(word: str) -> str:
    """回答の語（表 answers の行）。表に無ければ import の時点で `FactsError`。"""
    return str(facts.row(result.ANSWERS, word)["id"])


# 回答の語（変更要求）
REQUEST_CHANGES = answer_word("request_changes")


@dataclass
class StepProgress:
    """実行中の行のステップの進み。`generation` は変更要求と開き直しの回数で、どちらもステップを初めから流し直す。

    `anchor` は回答を数え始める事象の番号（行の開始・ステップの記録・変更要求の後の回答だけを使う）。
    `retries` は行の打ち直し（失敗の後の開始）の回数で、作業へ渡す（プランは打ち直すたびに新しい回を始める。同じ回のキーで
    打つと、プランは終わった回のバックアップの結果を返すだけだからである）。`redo` は工程全体の出力が規約に合わずに行が失敗したか。
    真なら次の開始（`retry` の後）で終わったステップの記録を無効にし、ステップを初めから流し直す（同じ出力を照らし直すだけにしない）。
    `held_finish` は手で終えて承認を待つ記録（事象 `finish_held` の payload）。待つ間は次のステップを承認ゲートにする。
    """

    generation: int = 0
    retries: int = 0
    done: dict[str, dict[str, Any]] = field(default_factory=dict)
    waiting: Optional[dict[str, Any]] = None
    anchor: int = 0
    answer: Optional[model.Event] = None
    notes: Optional[str] = None
    failed: bool = False
    redo: bool = False
    # #4446: 待ちの記録を持ったステップ（MVV 判定はゲートを省かない）・未了に戻した回数・終わりの事象の番号・設計のレビューへ戻した記録
    held: set[str] = field(default_factory=set)
    resets: dict[str, int] = field(default_factory=dict)
    finished_at: dict[str, int] = field(default_factory=dict)
    again: list[dict[str, Any]] = field(default_factory=list)
    held_finish: Optional[dict[str, Any]] = None
    # `answer` の後に却下か変更要求の投稿を試みた承認ゲートの区分（`approval_decide.rejection_posted_after` と同じ失効条件）
    rejecting: set[Optional[str]] = field(default_factory=set)

    def next_step(self, stage: stages.StageDef) -> Optional[stages.StepDef]:
        if self.held_finish is not None:
            return next(st for st in stage.steps if st.kind == stages.GATE_KIND)
        for st in stage.steps:
            if st.id not in self.done:
                return st
        return None

    def answer_value(self) -> Optional[str]:
        return str(event_payload(self.answer)["answer"]) if self.answer is not None else None

    def see(self, ev: model.Event) -> None:
        """事象 1 つを読んで進みを書き換える。変更要求（承認ゲートの request_changes）なら世代を上げてステップを初めから流し直す。"""
        handle = _SEES.get(ev.draft.kind)
        if handle is not None:
            handle(self, ev, event_payload(ev))

    def _started(self, ev: model.Event, _p: dict[str, Any]) -> None:
        """行の開始。数え始めをここへ移し、失敗の後の開始なら打ち直しを数える（出力の失敗なら済んだステップも捨てる）。"""
        self.anchor, self.answer, self.waiting, self.held_finish = ev.seq, None, None, None
        if self.failed:
            self.retries += 1
            self.done = {} if self.redo else self.done
            self.failed = self.redo = False

    def _failed(self, _ev: model.Event, p: dict[str, Any]) -> None:
        """行の失敗。止まったステップが無い（工程全体の出力が規約に合わない）なら、次の開始で初めから流し直す。"""
        self.failed, self.held_finish = True, None
        self.redo = "step" in p and p["step"] is None

    def _recorded(self, ev: model.Event, p: dict[str, Any]) -> None:
        """ステップの記録。終わったなら結果を残し、未了に戻す記録（`stages.RESET`）なら結果を捨て、ほかは待ちにする。数え始めをここへ移す。"""
        step = p["step"]
        if p["status"] == FINISHED:
            self.done[step], self.waiting = p["report"], None
            self.finished_at[step] = ev.seq
        elif p["status"] == stages.RESET:
            self.done.pop(step, None)
            self.resets[step] = (self.resets[step] if step in self.resets else 0) + 1
            if isinstance(p.get(stages.REVIEW_AGAIN), dict):
                self.again.append({"seq": ev.seq, "gate": p.get("gate"), **p[stages.REVIEW_AGAIN]})
        else:
            self.waiting = p
            self.held.add(step)
        self.anchor, self.answer = ev.seq, None

    def _reopened(self, ev: model.Event, p: dict[str, Any]) -> None:
        """開き直し。進み・待ち・回答・打ち直しの記録を捨てて世代を上げる（前の回のプランの実行の結果を使わない）。"""
        self.generation += 1
        self.retries, self.done, self.waiting, self.answer, self.anchor = 0, {}, None, None, ev.seq
        self.failed = self.redo = False
        self.held, self.resets, self.finished_at, self.again = set(), {}, {}, []
        self.notes, self.held_finish = p.get("notes"), None

    def _finish_held(self, ev: model.Event, p: dict[str, Any]) -> None:
        """手で終えた回答待ち。次のステップを承認ゲートにして、回答の数え始めをここへ移す。

        前に済んだ承認ゲートと後ろのステップの完了は無効にする。承認されずに打ち直しても、前の承認でマージ・リリースへ
        進まないためである。承認ゲートは待ちを持ったステップにして、MVV 判定で省かない。"""
        gate = p["step"]
        if gate in self.done:
            at = self.finished_at[gate]
            self.done = {step: report for step, report in self.done.items() if self.finished_at[step] < at}
        self.held.add(gate)
        self.held_finish, self.waiting, self.answer, self.anchor = p, None, None, ev.seq

    def _answered(self, ev: model.Event, p: dict[str, Any]) -> None:
        """回答。承認ゲートの変更要求なら世代を上げて初めから流し直し、ほかは数え始めの後の回答だけを残す。"""
        if p["answer"] == REQUEST_CHANGES and p["kind"] == invoke.LISTEN:
            self.generation += 1
            self.done, self.waiting, self.answer, self.anchor = {}, None, None, ev.seq
            self.held, self.again, self.held_finish = set(), [], None
            self.notes = p["notes"]
        elif ev.seq > self.anchor:
            self.answer, self.rejecting = ev, set()

    def _posting(self, ev: model.Event, p: dict[str, Any]) -> None:
        """証跡の投稿を試みた記録。却下と変更要求なら、承認ゲートの区分を覚える。"""
        if posts_rejection(ev):
            self.rejecting.add(p[REJECTING_KEY])


# 事象の種類 → 進みを書き換える StepProgress のメソッド（表に無い種類は進みを変えない）
_SEES: dict[str, Callable[[StepProgress, model.Event, dict[str, Any]], None]] = {
    ANSWERED: StepProgress._answered,
    STARTED: StepProgress._started,
    FAILED: StepProgress._failed,
    STEP_EVENT: StepProgress._recorded,
    REOPENED: StepProgress._reopened,
    FINISH_HELD: StepProgress._finish_held,
    APPROVAL_POSTING: StepProgress._posting,
}


def step_progress(v: View, stage: str) -> StepProgress:
    prog = StepProgress()
    for ev in v.row_events(stage):
        prog.see(ev)
    return prog


class Action(StrEnum):
    """次の 1 手の種類（値は結果 JSON の `metrics.move` に載る）。"""

    DONE = "done"
    COMPLETE = "complete"
    START = "start"
    RESUME = "resume"
    GATE = "gate"
    CONSUME = "consume"
    FAILED = "failed"


@dataclass(frozen=True)
class Move:
    """次の 1 手。"""

    action: Action
    stage: Optional[stages.StageDef] = None
    step: Optional[stages.StepDef] = None
    progress: Optional[StepProgress] = None
    event: Optional[model.Event] = None
