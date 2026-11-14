"""開発ループの副命令の判断: 書く事象か、断りの種類と次の 1 手（SF11）。

副命令は記録を畳んだ状態と引数をここへ渡し、`Decision` を受けて事象を書くか、断りをそのまま結果にする。
断りと止まりは必ず止まった理由の種類（`kind`）を持ち、打てる次の 1 手（`next`）があれば添える。読み手は
`metrics` を読まずに `next` で次の手を決める。

持ち主の鍵（#2820）:

- `start` が鍵を取り、周の境ごとに延ばす。ほかのセッションは期限の切れていない鍵があれば入れない
- `stop` は止める要求を置くだけで鍵を返さない。鍵を返すのは周の境（`lap` で締めたとき）と期限切れだけである
- `stop --now` はその場でループを止めた状態にし、締めていない周を保留で締める（#2435）。鍵は返さないが、
  止めたループへの書き込みはすべて断るので、別のセッションが周の途中へ入ることは無い
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from ndf.domains.loop import lap_decide
from ndf.domains.loop import record_decide as rec

# 止める要求を置ける主体: 人・オーガナイザー（人の指示を受けて）
STOP_BY = ("human", "organizer")
# 断りの種類（止まった理由の表の種類）
REFUSED, PRECONDITION, INVALID = "refused", "precondition", "invalid"


@dataclass(frozen=True)
class Step:
    """次の 1 手: エントリポイントの後ろの引数と、打つ側が埋める鍵。`session` なら打つ側のセッションを添える。"""

    args: tuple[str, ...]
    ask: tuple[str, ...] = ()
    session: bool = False


@dataclass(frozen=True)
class Decision:
    """副命令の判断。`kind` が None なら `events` を書いて成功、ほかは断り（何も書かない）。`items` は結果の行（選定の行）。"""

    summary: str
    events: tuple[dict[str, Any], ...] = ()
    kind: Optional[str] = None
    next: Optional[Step] = None
    metrics: dict[str, Any] = field(default_factory=dict)
    items: tuple[dict[str, Any], ...] = ()

    @property
    def refused(self) -> bool:
        return self.kind is not None


@dataclass(frozen=True)
class Ctx:
    """副命令を打った文脈: ループの名前・打ったセッション・今の時刻（UNIX 秒）。"""

    name: str
    session: Optional[str]
    now: float


def refuse(kind: str, summary: str, nxt: Optional[Step] = None) -> Decision:
    return Decision(summary, kind=kind, next=nxt)


def get_step(name: str) -> Step:
    return Step(("loop", "get", name))


def next_step(state: rec.LoopState, name: str) -> Optional[Step]:
    """ループの今の状態から、次に打つ 1 手。止めたループは None。"""
    lap = state.current
    if state.status == rec.HALTED:
        return None
    if lap is None:
        if state.requested is not None:
            return Step(("loop", "lap", name), session=True)
        return Step(("loop", "select", name), ask=("candidates",), session=True)
    if lap.remaining and lap.mode is None:
        return Step(("loop", "sprint", name), ask=("mode", "basis"), session=True)
    return Step(("loop", "lap", name), session=True)


def _lease_refusal(state: rec.LoopState, c: Ctx) -> Optional[Decision]:
    """鍵を持たないセッションの書き込みの断り。止めたループへの書き込みも断る。"""
    if state.status == rec.HALTED:
        assert state.stopped is not None
        return refuse(REFUSED, f"ループ {c.name} は止めた（{state.stopped.reason}）。新しい名前で始める")
    if not state.holds(c.session, c.now):
        if state.held_by_other(c.session, c.now):
            return refuse(REFUSED, f"ループ {c.name} の鍵はセッション {state.holder} が持つ", get_step(c.name))
        return refuse(PRECONDITION, f"セッション {c.session} は鍵を持たない", Step(("loop", "start", c.name), session=True))
    return None


def start(state: rec.LoopState, c: Ctx, ttl: float) -> Decision:
    """鍵を取る（持っていれば延ばす）。ほかのセッションの生きた鍵があれば断る。"""
    if state.status == rec.HALTED:
        return refuse(REFUSED, f"ループ {c.name} は止めた。新しい名前で始める")
    if state.held_by_other(c.session, c.now):
        return refuse(REFUSED, f"ループ {c.name} の鍵はセッション {state.holder} が持つ（期限 {state.lease_until:.0f}）", get_step(c.name))
    ev = rec.event(rec.LEASE_TAKEN, c.now, session=c.session, until=c.now + ttl)
    after = rec.apply(state, ev)
    return Decision(f"ループ {c.name} の鍵を取った", (ev,), next=next_step(after, c.name))


def _halt_event(by: str, now: float) -> dict[str, Any]:
    """人が止めた（`USER_STOP`）としてループを止めた状態にする事象。"""
    return rec.event(rec.LOOP_HALTED, now, by=by, reason=rec.USER_STOP)


def _close_event(lap: rec.Lap, facts: lap_decide.LapFacts, now: float) -> tuple[dict[str, Any], str, str]:
    """周を締める事象と、その結果・理由（`lap_decide.lap_outcome` が決める）。"""
    outcome, why = lap_decide.lap_outcome(facts)
    return rec.event(rec.LAP_CLOSED, now, lap=lap.number, outcome=outcome, reason=why), outcome, why


def _boundary(state: rec.LoopState, c: Ctx) -> list[dict[str, Any]]:
    """周の境で、止める要求があれば止めた状態にして鍵を返す事象。無ければ空。"""
    if state.requested is None or state.status == rec.HALTED:
        return []
    req = state.requested
    return [_halt_event(req.by, c.now), rec.event(rec.LEASE_RETURNED, c.now, session=c.session)]


def open_lap(state: rec.LoopState, c: Ctx, issues: Sequence[int]) -> Decision:
    """周を始める。周の境なので、止める要求があれば周を始めずに止める。"""
    refusal = _lease_refusal(state, c)
    if refusal is not None:
        return refusal
    if state.current is not None:
        return refuse(REFUSED, f"周 {state.current.number} を締めていない", next_step(state, c.name))
    stop = _boundary(state, c)
    if stop:
        return Decision(f"止める要求があり、周を始めずにループ {c.name} を止めた", tuple(stop))
    if not issues:
        return refuse(PRECONDITION, "選べる課題が無い", get_step(c.name))
    number = state.laps[-1].number + 1 if state.laps else 1
    ev = rec.event(rec.LAP_OPENED, c.now, lap=number, issues=list(issues))
    return Decision(f"周 {number} を始めた", (ev,), next=next_step(rec.apply(state, ev), c.name), metrics={"lap": number})


def _open_lap_refusal(state: rec.LoopState, name: str) -> Optional[Decision]:
    if state.status == rec.HALTED:
        return refuse(REFUSED, f"ループ {name} は止めた")
    if state.current is None:
        return refuse(PRECONDITION, "締めていない周が無い", next_step(state, name))
    return None


def _open_lap(state: rec.LoopState, c: Ctx, lease: bool) -> tuple[Optional[Decision], Optional[rec.Lap]]:
    """断りか、締めていない周。`lease` なら鍵の断りを先に当てる。"""
    refusal = (_lease_refusal(state, c) if lease else None) or _open_lap_refusal(state, c.name)
    if refusal is not None:
        return refusal, None
    return None, state.current


def decide_mode(state: rec.LoopState, c: Ctx, mode: str, basis: str, modes: Sequence[str]) -> Decision:
    """周のモードを根拠つきで決める（#2923）。根拠の無い判定は断る。"""
    refusal, lap = _open_lap(state, c, lease=True)
    if refusal is not None:
        return refusal
    assert lap is not None
    if mode not in modes:
        return refuse(INVALID, f"モード {mode!r} は {'・'.join(modes)} のどれでもない", next_step(state, c.name))
    if not basis.strip():
        return refuse(INVALID, "モードの判定の根拠が空", next_step(state, c.name))
    ev = rec.event(rec.MODE_DECIDED, c.now, lap=lap.number, mode=mode, basis=basis.strip())
    return Decision(f"周 {lap.number} のモードを {mode} にした", (ev,), next=next_step(rec.apply(state, ev), c.name))


def exclude(state: rec.LoopState, c: Ctx, issue: int, reason: str) -> Decision:
    """周の途中で課題を外す（#2788）。外すとモードを決め直す（#2923）。"""
    refusal, lap = _open_lap(state, c, lease=True)
    if refusal is not None:
        return refusal
    assert lap is not None
    if issue not in lap.remaining:
        return refuse(PRECONDITION, f"課題 #{issue} は周 {lap.number} に残っていない", get_step(c.name))
    if not reason.strip():
        return refuse(INVALID, "外す理由が空", get_step(c.name))
    ev = rec.event(rec.ISSUE_EXCLUDED, c.now, lap=lap.number, issue=issue, reason=reason.strip())
    after = rec.apply(state, ev)
    left = after.current.remaining if after.current is not None else ()
    return Decision(f"周 {lap.number} から課題 #{issue} を外した（残り {len(left)} 件）", (ev,), next=next_step(after, c.name),
                    metrics={"remaining": list(left)})  # fmt: skip


def created(state: rec.LoopState, c: Ctx, issues: Sequence[int]) -> Decision:
    """周の中で起票した課題を記録する（#2132）。鍵は要らない（周の中の作業が書く）。"""
    refusal, lap = _open_lap(state, c, lease=False)
    if refusal is not None:
        return refusal
    assert lap is not None
    if not issues:
        return refuse(INVALID, "課題の番号が無い")
    ev = rec.event(rec.ISSUES_CREATED, c.now, lap=lap.number, issues=list(issues))
    return Decision(f"周 {lap.number} の起票 {len(issues)} 件を記録した", (ev,))


def gate(state: rec.LoopState, c: Ctx, pr: int, head: str) -> Decision:
    """設計承認ゲートを出した時点の PR の先端を記録する。"""
    refusal, lap = _open_lap(state, c, lease=False)
    if refusal is not None:
        return refusal
    assert lap is not None
    ev = rec.event(rec.GATE_SHOWN, c.now, lap=lap.number, pr=pr, head=head)
    return Decision(f"PR #{pr} の設計承認ゲートを先端 {head[:12]} で記録した", (ev,))


def answer(state: rec.LoopState, c: Ctx, pr: int, given: str, head: Optional[str], note: str) -> Decision:
    """設計承認ゲートの答えを記録する。

    先端は呼び手が 1 回だけ読んだ値（`head`）を使い、ゲートを出した時点と違えば答えを書かずに断る（#2714）。
    書いた答えの先端を `metrics.head` で返し、マージはその値を `--match-head-commit` に渡す。
    `revise`・`rejected` は差し戻しとして記録に残る（#2400）。
    """
    refusal, lap = _open_lap(state, c, lease=False)
    if refusal is not None:
        return refusal
    assert lap is not None
    shown = lap.gate_head(pr)
    if shown is None:
        return refuse(PRECONDITION, f"PR #{pr} の設計承認ゲートを記録していない", Step(("loop", "gate", c.name, "--pr", str(pr))))
    if head != shown:
        return refuse(PRECONDITION, f"PR #{pr} の先端がゲートを出した {shown[:12]} から {str(head)[:12]} へ動いた。資料を出し直す",
                      Step(("loop", "gate", c.name, "--pr", str(pr))))  # fmt: skip
    ev = rec.event(rec.GATE_ANSWERED, c.now, lap=lap.number, pr=pr, answer=given, head=head, note=note)
    return Decision(f"PR #{pr} の答え {given} を記録した", (ev,), metrics={"head": head, "answer": given})


def close_lap(state: rec.LoopState, c: Ctx, facts: lap_decide.LapFacts, ttl: float) -> Decision:
    """周を締める。結果は `lap_decide.lap_outcome` が決める。

    周の境なので、止める要求があれば止めて鍵を返し、無ければ鍵を `ttl` 秒延ばす。
    """
    refusal = _lease_refusal(state, c)
    if refusal is not None:
        return refusal
    lap = state.current
    if lap is None:
        stop = _boundary(state, c)
        if stop:
            return Decision(f"止める要求があり、ループ {c.name} を止めた", tuple(stop))
        return refuse(PRECONDITION, "締めていない周が無い", next_step(state, c.name))
    # 締めるときに渡した版は、記録に無いものだけを出した版として残す
    shipped = [
        rec.event(rec.VERSION_SHIPPED, c.now, lap=lap.number, version=v) for v in dict.fromkeys(facts.released) if v not in lap.released
    ]
    closing, outcome, why = _close_event(lap, replace(facts, released=(*lap.released, *(e["version"] for e in shipped))), c.now)
    events = [*shipped, closing]
    after = state
    for ev in events:
        after = rec.apply(after, ev)
    tail = _boundary(after, c)
    tail = tail if tail else [rec.event(rec.LEASE_TAKEN, c.now, session=c.session, until=c.now + ttl)]
    for ev in tail:
        after = rec.apply(after, ev)
    events += tail
    return Decision(f"周 {lap.number} を{outcome}で締めた: {why}", tuple(events), next=next_step(after, c.name),
                    metrics={"lap": lap.number, "outcome": outcome, "streak": lap_decide.failure_streak(after)})  # fmt: skip


def released(state: rec.LoopState, c: Ctx, version: str) -> Decision:
    """その周で出した版を記録する。周の結果の判断が読む。"""
    refusal, lap = _open_lap(state, c, lease=False)
    if refusal is not None:
        return refusal
    assert lap is not None
    ev = rec.event(rec.VERSION_SHIPPED, c.now, lap=lap.number, version=version)
    return Decision(f"周 {lap.number} のリリース {version} を記録した", (ev,))


def stop(state: rec.LoopState, c: Ctx, by: str, reason: str, at_once: bool) -> Decision:
    """止める要求を置く。鍵は返さない（#2820）。`at_once` ならその場で止めた状態にする（#2435）。"""
    if state.status == rec.HALTED:
        return refuse(REFUSED, f"ループ {c.name} は既に止めた")
    if by not in STOP_BY:
        return refuse(INVALID, f"止める主体は {'・'.join(STOP_BY)} のどれか: {by!r}")
    text = reason.strip() if reason.strip() else "理由の記載なし"
    events = [rec.event(rec.STOP_REQUESTED, c.now, by=by, reason=text)]
    if not at_once:
        after = rec.apply(state, events[0])
        return Decision(f"ループ {c.name} へ止める要求を置いた。周の境で止まる", tuple(events), next=next_step(after, c.name))
    lap = state.current
    if lap is not None:
        events.append(_close_event(lap, lap_decide.LapFacts(released=lap.released, held=True), c.now)[0])
    events.append(_halt_event(by, c.now))
    return Decision(f"ループ {c.name} を止めた（{rec.USER_STOP}）", tuple(events))
