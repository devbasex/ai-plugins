"""開発ループの記録: 事象の語彙と、事象の列から今の状態を求める畳み込み。

記録は事象を追記するだけで、今の値は事象の列を畳んで求める（書き換えない）。事象 1 件は辞書で、`kind` と
`at`（UNIX 秒）と種類ごとの鍵を持つ。周（ループの 1 回）は `lap-opened` で始まり `lap-closed` で締まる。

| 事象 | 鍵 | 意味 |
| --- | --- | --- |
| `lease-taken` | `session`・`until` | 持ち主の鍵を取った（`until` まで有効） |
| `lease-returned` | `session` | 持ち主の鍵を返した（周の境か期限切れだけ。#2820） |
| `lap-opened` | `lap`・`issues` | 周を始め、選んだ課題を記録した |
| `mode-decided` | `lap`・`mode`・`basis` | モードを根拠つきで決めた（#2923） |
| `issue-excluded` | `lap`・`issue`・`reason` | 周の途中で課題を外した（#2788）。外すとモードを決め直す |
| `issues-created` | `lap`・`issues` | 周の中で起票した課題（#2132） |
| `gate-shown` | `lap`・`pr`・`head` | 設計承認ゲートを出した時点の PR の先端 |
| `gate-answered` | `lap`・`pr`・`answer`・`head`・`note` | ゲートの答え。`revise` は差し戻しとして残る（#2400） |
| `version-shipped` | `lap`・`version` | その周で版をリリースした |
| `lap-closed` | `lap`・`outcome`・`reason` | 周を締めた |
| `stop-requested` | `by`・`reason` | 止める要求。鍵は返さない（#2820） |
| `loop-halted` | `by`・`reason` | ループを止めた状態にした（`--now` か、要求の後の周の境。#2435） |
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from ndf.domains.release.approval_decide import APPROVED

# 事象の種類
LEASE_TAKEN = "lease-taken"
LEASE_RETURNED = "lease-returned"
LAP_OPENED = "lap-opened"
MODE_DECIDED = "mode-decided"
ISSUE_EXCLUDED = "issue-excluded"
ISSUES_CREATED = "issues-created"
GATE_SHOWN = "gate-shown"
GATE_ANSWERED = "gate-answered"
VERSION_SHIPPED = "version-shipped"
LAP_CLOSED = "lap-closed"
STOP_REQUESTED = "stop-requested"
LOOP_HALTED = "loop-halted"

# 周の結果の値（記録の形。変えない）
DONE, FAILED, RETURNED, HELD = "完了", "失敗", "人へ戻した", "保留"
OUTCOMES = (DONE, FAILED, RETURNED, HELD)

# ループの状態
IDLE, RUNNING, STOPPING, HALTED = "idle", "running", "stopping", "stopped"

# 止めた理由: 人が止めた（#2435 の /goal の終わりの条件が読む）
USER_STOP = "user_stop"


class RecordError(ValueError):
    """記録の事象が語彙・形に合わない。"""


@dataclass(frozen=True)
class Answer:
    """承認ゲートの答え 1 件。"""

    pr: int
    answer: str
    head: Optional[str]
    note: str
    at: float


@dataclass(frozen=True)
class Lap:
    """周 1 回の状態。"""

    number: int
    issues: tuple[int, ...]
    excluded: tuple[tuple[int, str], ...] = ()
    created: tuple[int, ...] = ()
    mode: Optional[str] = None
    basis: Optional[str] = None
    gates: tuple[tuple[int, Optional[str]], ...] = ()
    answers: tuple[Answer, ...] = ()
    released: tuple[str, ...] = ()
    outcome: Optional[str] = None
    reason: Optional[str] = None

    @property
    def open(self) -> bool:
        return self.outcome is None

    @property
    def remaining(self) -> tuple[int, ...]:
        """外した課題を除いた、周に残る課題。"""
        out = {n for n, _ in self.excluded}
        return tuple(n for n in self.issues if n not in out)

    def gate_head(self, pr: int) -> Optional[str]:
        """PR の設計承認ゲートを出した時点の先端（最後に出したもの）。出していなければ None。"""
        heads = [h for p, h in self.gates if p == pr]
        return heads[-1] if heads else None


@dataclass(frozen=True)
class Stop:
    """止める要求か、止めた状態。"""

    by: str
    reason: str
    at: float


@dataclass(frozen=True)
class LoopState:
    """事象の列を畳んだ今の状態。"""

    laps: tuple[Lap, ...] = ()
    holder: Optional[str] = None
    lease_until: float = 0.0
    requested: Optional[Stop] = None
    stopped: Optional[Stop] = None
    events: int = field(default=0, compare=False)

    @property
    def current(self) -> Optional[Lap]:
        """締めていない周。無ければ None。"""
        return self.laps[-1] if self.laps and self.laps[-1].open else None

    @property
    def status(self) -> str:
        if self.stopped is not None:
            return HALTED
        if self.requested is not None:
            return STOPPING
        return RUNNING if self.laps or self.holder is not None else IDLE

    def holds(self, session: str, now: float) -> bool:
        """`session` が今、持ち主の鍵を持つか。"""
        return self.holder == session and self.lease_until > now

    def held_by_other(self, session: str, now: float) -> bool:
        """`session` でない持ち主が、期限の切れていない鍵を持つか。"""
        return self.holder is not None and self.holder != session and self.lease_until > now

    def rejections(self) -> list[dict[str, Any]]:
        """設計承認ゲートの差し戻し（答え `revise` と `rejected`）の一覧。振り返りが数える（#2400）。"""
        return [
            {"lap": lap.number, "pr": a.pr, "answer": a.answer, "note": a.note, "at": a.at}
            for lap in self.laps
            for a in lap.answers
            if a.answer != APPROVED
        ]


# 種類ごとに要る鍵
REQUIRED: dict[str, tuple[str, ...]] = {
    LEASE_TAKEN: ("session", "until"),
    LEASE_RETURNED: ("session",),
    LAP_OPENED: ("lap", "issues"),
    MODE_DECIDED: ("lap", "mode", "basis"),
    ISSUE_EXCLUDED: ("lap", "issue", "reason"),
    ISSUES_CREATED: ("lap", "issues"),
    GATE_SHOWN: ("lap", "pr", "head"),
    GATE_ANSWERED: ("lap", "pr", "answer", "head", "note"),
    VERSION_SHIPPED: ("lap", "version"),
    LAP_CLOSED: ("lap", "outcome", "reason"),
    STOP_REQUESTED: ("by", "reason"),
    LOOP_HALTED: ("by", "reason"),
}


def event(kind: str, at: float, **keys: Any) -> dict[str, Any]:
    """事象 1 件を組む。種類が語彙に無いか、要る鍵が欠ければ `RecordError`。"""
    out = {"kind": kind, "at": at, **keys}
    check(out)
    return out


def check(ev: Mapping[str, Any]) -> None:
    """事象 1 件の形を確かめる。"""
    kind = ev["kind"] if "kind" in ev else None
    if kind not in REQUIRED:
        raise RecordError(f"開発ループの記録の知らない事象: {kind!r}")
    missing = [k for k in ("at", *REQUIRED[kind]) if k not in ev]
    if missing:
        raise RecordError(f"事象 {kind} に鍵 {', '.join(missing)} が無い")
    if kind == LAP_CLOSED and ev["outcome"] not in OUTCOMES:
        raise RecordError(f"周の結果 {ev['outcome']!r} は {'・'.join(OUTCOMES)} のどれでもない")


def _lap_index(state: LoopState, number: int) -> int:
    """番号の周の添字（同じ番号が複数あれば先頭）。無ければ `RecordError`。"""
    for i, lap in enumerate(state.laps):
        if lap.number == number:
            return i
    raise RecordError(f"記録に無い周 {number} への事象")


def _with_lap(state: LoopState, number: int, **change: Any) -> LoopState:
    i = _lap_index(state, number)
    laps = list(state.laps)
    laps[i] = replace(laps[i], **change)
    return replace(state, laps=tuple(laps))


def _apply_lap(state: LoopState, ev: Mapping[str, Any]) -> LoopState:
    """周に付く事象を当てる。"""
    kind, n = ev["kind"], int(ev["lap"])
    lap = state.laps[_lap_index(state, n)]
    if kind == MODE_DECIDED:
        return _with_lap(state, n, mode=ev["mode"], basis=ev["basis"])
    if kind == ISSUE_EXCLUDED:
        # 外すと課題の組が変わるので、決めたモードを捨てて決め直させる（#2923）
        return _with_lap(state, n, excluded=(*lap.excluded, (int(ev["issue"]), ev["reason"])), mode=None, basis=None)
    if kind == ISSUES_CREATED:
        return _with_lap(state, n, created=(*lap.created, *(int(i) for i in ev["issues"] if int(i) not in lap.created)))
    if kind == GATE_SHOWN:
        return _with_lap(state, n, gates=(*lap.gates, (int(ev["pr"]), ev["head"])))
    if kind == GATE_ANSWERED:
        got = Answer(int(ev["pr"]), ev["answer"], ev["head"], ev["note"], float(ev["at"]))
        return _with_lap(state, n, answers=(*lap.answers, got))
    if kind == VERSION_SHIPPED:
        return _with_lap(state, n, released=(*lap.released, str(ev["version"])))
    return _with_lap(state, n, outcome=ev["outcome"], reason=ev["reason"])


def apply(state: LoopState, ev: Mapping[str, Any]) -> LoopState:
    """事象 1 件を当てた次の状態。"""
    check(ev)
    kind = ev["kind"]
    state = replace(state, events=state.events + 1)
    if kind == LEASE_TAKEN:
        return replace(state, holder=ev["session"], lease_until=float(ev["until"]))
    if kind == LEASE_RETURNED:
        return replace(state, holder=None, lease_until=0.0) if state.holder == ev["session"] else state
    if kind == LAP_OPENED:
        lap = Lap(number=int(ev["lap"]), issues=tuple(int(i) for i in ev["issues"]))
        return replace(state, laps=(*state.laps, lap))
    if kind == STOP_REQUESTED:
        return replace(state, requested=Stop(ev["by"], ev["reason"], float(ev["at"])))
    if kind == LOOP_HALTED:
        return replace(state, stopped=Stop(ev["by"], ev["reason"], float(ev["at"])))
    return _apply_lap(state, ev)


def apply_all(state: LoopState, events: Iterable[Mapping[str, Any]]) -> LoopState:
    """`state` へ事象の列を古い順に当てた次の状態。"""
    for ev in events:
        state = apply(state, ev)
    return state


def fold(events: Iterable[Mapping[str, Any]]) -> LoopState:
    """事象の列を古い順に畳んだ今の状態。"""
    return apply_all(LoopState(), events)
