"""プランの遷移の判断（純粋な関数）。

`decide(plan, state, done)` は、前の手の結果 `done` から次の手を 1 つ決める。手は 3 種類ある。

- `RunStep`: ステップを打つ。結果は `Outcome`（`status` と止まった理由の種類）
- `Sleep`: 打ち直しの前に待つ（ES2）。待った後に同じステップを打つ。打ち直しはステップの数に数えない
- `AwaitAnswer`: 承認の答えを待つ。答えから `on_answer` の行き先へ進む

止まったステップの行き先は `ndf.contracts.routing.pick` だけが選ぶ（IS4・IS5）。`summary` などの文は読まない（IS7）。
行き先を選ぶたび（止まったステップの行き先・承認の答えの行き先）に、選択（`Choice`）を状態の `choices` へ足す。
書き手（`runner`）が足した分を選択の記録（`ndf.contracts.chosen`）として進捗記録へ 1 件ずつ書く。
ステップの数が上限を超えるか、知らないステップへ進めば必ず終わる。DBOS も入出力も読まない。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import NamedTuple, Optional, Union

from ndf.contracts import causes, chosen, limits, routing
from ndf.contracts.exit_codes import GATE, OK, STOPPED
from ndf.core.loop import Next
from ndf.core.plan.model import Plan, Step

# 結果の status の語のうち、手の実行が返すもの（OK・GATE・STOPPED のほか）
SLEPT, ANSWERED = "slept", "answered"
# 待ちの上限の名前（利用上限の待ちの頭打ち）
USAGE_WAIT_MAX = "supervise.limit-wait-max"
USAGE_LIMIT = "usage_limit"
# 選択の記録の規則の番号（AI が判断している分岐の一覧。issues/issue-1142-design-scripted-flow.md）。
# 止まったプランの再開点（R03）、衝突で止まったときの取り込み（R10）、承認の答えの後の行き先（R13）
RULE_STOPPED, RULE_CONFLICT, RULE_ANSWER = "R03", "R10", "R13"


class RunStep(NamedTuple):
    step: str
    attempt: int


class Sleep(NamedTuple):
    step: str
    seconds: float


class AwaitAnswer(NamedTuple):
    step: str
    seq: int


Act = Union[RunStep, Sleep, AwaitAnswer]


class Choice(NamedTuple):
    """行き先の選択 1 件。`frm` は選んだときのステップ、`to` は行き先（ステップの id か routing の語）。"""

    rule: str
    chooser: str
    frm: str
    to: str
    cause: Optional[str] = None


class Outcome(NamedTuple):
    """手の実行の結果。`kind` は止まった理由の種類、`answer` は承認の答え（期限までに無ければ None）。"""

    status: str
    kind: Optional[str] = None
    retry_after: Optional[float] = None
    answer: Optional[str] = None


@dataclass(frozen=True)
class StepState:
    """遷移の状態。`prelude` は開始点の前に通すステップの残り、`retries` は（ステップ, 種類）ごとの打ち直しの回数。"""

    current: Optional[str]
    start: Optional[str]
    prelude: tuple[str, ...] = ()
    count: int = 0
    limit: int = 0
    gates: int = 0
    retries: Mapping[str, int] = field(default_factory=dict)
    status: str = OK
    kind: Optional[str] = None
    detail: Optional[str] = None
    last: Optional[str] = None
    choices: tuple[Choice, ...] = ()


def start_state(plan: Plan, start: Optional[str], prelude: Sequence[str] = ()) -> StepState:
    """遷移の初めの状態。`start` が無ければ先頭、`end`・`stop` なら前置きの後に流さずに終わる（#3086）。"""
    first = plan.order[0] if start is None else start
    if first in (routing.END, routing.STOP):
        first = None
    return StepState(current=None, start=first, prelude=tuple(prelude), limit=plan.limit)


def _retry_key(step: str, kind: str) -> str:
    return f"{step}:{kind}"


def _finish(state: StepState, status: str, kind: Optional[str], detail: Optional[str]) -> Next:
    return Next.finish(replace(state, status=status, kind=kind, detail=detail))


def _run(plan: Plan, state: StepState, target: Optional[str]) -> Next:
    """`target` を打つ手。None か `end` なら終わる。知らないステップ・上限を超えたら止まる。"""
    if target is None or target == routing.END:
        return _finish(state, OK, None, None)
    if target == routing.STOP:
        return _finish(state, STOPPED, "refused", f"ステップ {state.last} の行き先が stop")
    if plan.step(target) is None:
        return _finish(state, STOPPED, "invalid", f"知らないステップ: {target}")
    if state.count + 1 > state.limit:
        return _finish(state, STOPPED, "fix_limit", f"ステップの数が上限 {state.limit} を超えた")
    nxt = replace(state, current=target, last=target, count=state.count + 1)
    return Next.step(RunStep(target, 0), nxt)


def _after_ok(plan: Plan, state: StepState, step: Step) -> Next:
    """ステップが通った後の行き先。前置きの中なら前置きの次か開始点、そうでなければ next か並びの次。"""
    if state.prelude and state.prelude[0] == step.id:
        rest = state.prelude[1:]
        return _run(plan, replace(state, prelude=rest), rest[0] if rest else state.start)
    target = step.next if step.next is not None else plan.following(step.id)
    return _run(plan, state, target)


def _policy(step: Step, kind: str) -> Optional[limits.Policy]:
    return step.retry[kind] if kind in step.retry else causes.policy_of(kind)


def _used(state: StepState, step_id: str, kind: str) -> int:
    """ステップ `step_id` を種類 `kind` で打ち直した回数。"""
    key = _retry_key(step_id, kind)
    return state.retries[key] if key in state.retries else 0


def _retry_state(step: Step, state: StepState, done: Outcome) -> routing.RetryState:
    used = _used(state, step.id, done.kind)
    cap = limits.seconds(USAGE_WAIT_MAX) if done.kind == USAGE_LIMIT else None
    return routing.RetryState(used, _policy(step, done.kind), done.retry_after, cap)


def _chose(state: StepState, choice: Choice) -> StepState:
    return replace(state, choices=(*state.choices, choice))


def _taken(route: routing.Route, in_prelude: bool) -> str:
    """選んだ行き先のうち、実際に進む先。前置きの中でステップへ進む選択は、止まって conductor へ戻る。"""
    if route.to == routing.STEP:
        return route.target if not in_prelude else routing.CONDUCTOR
    return route.to


def _after_stop(plan: Plan, state: StepState, step: Step, done: Outcome) -> Next:
    """止まったステップの行き先（IS4・IS5）。前置きの中で止まれば、そのステップの結果で終わる。

    打ち直し・ステップ・終わり・conductor・人のどれへ進むかを、プランの宣言と止まった理由の表から選び、選択を残す。
    """
    kind = done.kind if done.kind is not None else "unknown"
    done = done._replace(kind=kind)
    route = routing.pick(kind, on_cause=step.on_cause, on_fail=step.on_fail, retry=_retry_state(step, state, done))
    rule = RULE_CONFLICT if kind == "base_conflict" else RULE_STOPPED
    state = _chose(state, Choice(rule, chosen.SCRIPT, step.id, _taken(route, bool(state.prelude)), kind))
    detail = f"ステップ {step.id} が {kind} で止まった"
    if route.to == routing.RETRY:
        retries = {**state.retries, _retry_key(step.id, kind): _used(state, step.id, kind) + 1}
        return Next.step(Sleep(step.id, route.wait), replace(state, retries=retries, kind=kind))
    if route.to == routing.END:
        return _finish(state, OK, None, None)
    if route.to == routing.STEP and not state.prelude:
        return _run(plan, replace(state, kind=kind), route.target)
    # stop・conductor・human と、前置きの中の止まりは、その種類で止まる（行き先は止まった理由の表が持つ）
    return _finish(state, STOPPED, kind, detail)


def _after_gate(state: StepState, step: Step) -> Next:
    """承認ゲートを返したステップ。行き先の表があれば答えを待ち、無ければプランを承認ゲートで終える。"""
    if not step.on_answer:
        return _finish(state, GATE, None, f"ステップ {step.id} が承認ゲートで止まった")
    nxt = replace(state, gates=state.gates + 1)
    return Next.step(AwaitAnswer(step.id, nxt.gates), nxt)


def _after_answer(plan: Plan, state: StepState, step: Step, done: Outcome) -> Next:
    """答えを受けた後。答えが無ければ待ち直し、表に無い答えは止める。"""
    if done.answer is None:
        return Next.step(AwaitAnswer(step.id, state.gates), state)
    if done.answer not in step.on_answer:
        return _finish(state, STOPPED, "invalid", f"ステップ {step.id} の知らない答え: {done.answer}")
    target = step.on_answer[done.answer]
    choice = Choice(RULE_ANSWER, chosen.chooser(None, (), answered_by_human=True), step.id, target if target else routing.END)
    return _run(plan, _chose(state, choice), target)


def decide(plan: Plan, state: StepState, done: Optional[Outcome]) -> Next:
    """前の手の結果から次の手か終わりを決める。初めの呼び出しの `done` は None。"""
    if done is None:
        first = state.prelude[0] if state.prelude else state.start
        return _run(plan, state, first)
    step = plan.step(state.current)
    if done.status == SLEPT:
        return Next.step(RunStep(step.id, state.retries[_retry_key(step.id, state.kind)]), state)
    if done.status == ANSWERED:
        return _after_answer(plan, state, step, done)
    if done.status == OK:
        return _after_ok(plan, state, step)
    if done.status == GATE:
        return _after_gate(state, step)
    return _after_stop(plan, state, step, done)
