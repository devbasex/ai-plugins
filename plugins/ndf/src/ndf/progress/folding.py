"""ステートマシンの事象の列を、工程の中のステップの進みへ畳み込む（純粋な処理。ファイルもプロセスも触らない）。

行のステップの進み（`StepProgress`）と回答の語を持つ。事象の種類の定数と、1 回の取得で読んだステートマシン（`View`）は
`ndf.progress.run_view` が持つ。次の 1 手を決めて流すのは `ndf.progress.run` である。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Optional

from ndf.contracts import facts, result
from ndf.progress import invoke, model, stages
from ndf.progress.run_view import ANSWERED, FAILED, FINISHED, STARTED, STEP_EVENT, View, event_payload


def answer_word(word: str) -> str:
    """回答の語（表 answers の行）。表に無ければ import の時点で `FactsError`。"""
    return str(facts.row(result.ANSWERS, word)["id"])


# 回答の語（変更要求）
REQUEST_CHANGES = answer_word("request_changes")


@dataclass
class StepProgress:
    """実行中の行のステップの進み。`generation` は変更要求の回数で、変更を要求するとステップを初めから流し直す。

    `anchor` は回答を数え始める事象の番号（行の開始・ステップの記録・変更要求の後の回答だけを使う）。
    `retries` は行の打ち直し（失敗の後の開始）の回数で、作業へ渡す（プランは打ち直すたびに新しい回を始める。同じ回のキーで
    打つと、プランは終わった回のバックアップの結果を返すだけだからである）。`redo` は工程全体の出力が規約に合わずに行が失敗したか。
    真なら次の開始（`retry` の後）で終わったステップの記録を無効にし、ステップを初めから流し直す（同じ出力を照らし直すだけにしない）。
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
    carries: frozenset[str] = frozenset()  # 承認されなくても流し直さず先へ進む承認ゲート（定義の on_reject。#4446）
    # #4446: 待ちの記録を持ったステップ（MVV 判定はゲートを省かない）・未了に戻した回数・終わりの事象の番号・設計のレビューへ戻した記録
    held: set[str] = field(default_factory=set)
    resets: dict[str, int] = field(default_factory=dict)
    finished_at: dict[str, int] = field(default_factory=dict)
    again: list[dict[str, Any]] = field(default_factory=list)

    def next_step(self, stage: stages.StageDef) -> Optional[stages.StepDef]:
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
        self.anchor, self.answer, self.waiting = ev.seq, None, None
        if self.failed:
            self.retries += 1
            self.done = {} if self.redo else self.done
            self.failed = self.redo = False

    def _failed(self, _ev: model.Event, p: dict[str, Any]) -> None:
        """行の失敗。止まったステップが無い（工程全体の出力が規約に合わない）なら、次の開始で初めから流し直す。"""
        self.failed = True
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

    def _answered(self, ev: model.Event, p: dict[str, Any]) -> None:
        """回答。承認ゲートの変更要求なら世代を上げて初めから流し直し、ほかは数え始めの後の回答だけを残す。"""
        if p["answer"] == REQUEST_CHANGES and p["kind"] == invoke.LISTEN and p["step"] not in self.carries:
            self.generation += 1
            self.done, self.waiting, self.answer, self.anchor = {}, None, None, ev.seq
            self.held, self.again = set(), []
            self.notes = p["notes"]
        elif ev.seq > self.anchor:
            self.answer = ev


# 事象の種類 → 進みを書き換える StepProgress のメソッド（表に無い種類は進みを変えない）
_SEES: dict[str, Callable[[StepProgress, model.Event, dict[str, Any]], None]] = {
    ANSWERED: StepProgress._answered,
    STARTED: StepProgress._started,
    FAILED: StepProgress._failed,
    STEP_EVENT: StepProgress._recorded,
}


def step_progress(v: View, stage: str) -> StepProgress:
    prog = StepProgress(carries=frozenset(st.id for s in v.snap.stages if s.name == stage for st in s.steps if st.on_reject))
    for ev in v.row_events(stage):
        prog.see(ev)
    return prog
