"""計画を進める耐久ワークフローの判断（#3097）。DB も DBOS も読まない純粋な関数である。

耐久ワークフロー（`ndf.progress.plan.run`）は、計画とレーンの進行表を読んだ写し（`Observe` の結果）を
この判断へ渡し、次の手を 1 つ受ける。手は 4 つある。

- `Observe`: 計画とレーンの進行表を読み直す
- `Launch`: 止まっているレーンの conductor を起こす。`cancel` があれば、止めた計画で実行中のスプリントを
  中止させる入力で起こす。耐久ワークフローは conductor へメッセージを送らず、起こすだけである
- `Complete`: 全レーンの行が終わり、答えていない依頼が無いので計画を完了にする（I16）
- `Sleep`: 次に読み直すまで待つ

同じ読み直しの間に同じレーンの行を 2 回起こさない。起こしたレーンの行は、行が実行中になるか終わるまで
起こし直さない（`launched`）。起動の失敗は読み直すたびに打ち直し、回数が尽きたら `failed` に残して知らせる。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Optional, Union

from ndf.core import loop

# 計画の状態（契約 progress.json の table_states.plan）
RUNNING, COMPLETED, CANCELLED = "running", "completed", "cancelled"
# 耐久ワークフローの終わりの語
OUTCOME_COMPLETED, OUTCOME_STOPPED = "completed", "stopped"


@dataclass(frozen=True)
class Observe:
    """計画とレーンの進行表を読み直す手。"""


@dataclass(frozen=True)
class Launch:
    """レーン `lane` の conductor を、行 `row` を始める入力（`cancel` があればそのスプリントを中止する入力）で起こす手。"""

    lane: str
    row: str
    cancel: Optional[str] = None

    @property
    def key(self) -> str:
        return f"cancel:{self.cancel}" if self.cancel else f"{self.lane}/{self.row}"


@dataclass(frozen=True)
class Complete:
    """計画を完了にする手。"""


@dataclass(frozen=True)
class Sleep:
    """`seconds` 秒待つ手。"""

    seconds: float


Act = Union[Observe, Launch, Complete, Sleep]


@dataclass(frozen=True)
class Done:
    """手の結果。`Observe` は写し（`snapshot` の形）、`Launch` は `{"ok": 真偽, "detail": 文}`、`Complete` は真偽。"""

    act: Act
    value: Any = None


@dataclass(frozen=True)
class Policy:
    """読み直しの間隔の秒と、レーン 1 つの起動を打つ回数（最初の 1 回を含む）。"""

    poll: float
    attempts: int


@dataclass(frozen=True)
class RunState:
    """判断の状態。`tried` は今の読み直しで打った起動、`attempts` は失敗した起動の回数。"""

    snapshot: Optional[dict[str, Any]] = None
    launched: tuple[str, ...] = ()
    tried: tuple[str, ...] = ()
    attempts: tuple[tuple[str, int], ...] = ()
    failed: tuple[str, ...] = ()
    outcome: Optional[str] = None
    errors: tuple[str, ...] = field(default=())

    def count(self, key: str) -> int:
        return next((n for k, n in self.attempts if k == key), 0)

    def as_result(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "launched": list(self.launched), "failed": list(self.failed), "errors": list(self.errors)}


def snapshot(state: str, complete: bool, wakeable: list[tuple[str, str]], running: list[tuple[str, str, str]]) -> dict[str, Any]:
    """`Observe` の結果の形（耐久の記録へ書くので JSON にできる値だけにする）。`running` は（レーン, 行, スプリントの ID）。"""
    return {"state": state, "complete": complete, "wakeable": [list(w) for w in wakeable], "running": [list(r) for r in running]}


def _skip(state: RunState) -> set[str]:
    return set(state.launched) | set(state.tried) | set(state.failed)


def _snap(state: RunState) -> dict[str, Any]:
    if state.snapshot is None:
        raise TypeError("読み直しの前に写しを使った（判断の誤り）")
    return state.snapshot


def _candidates(state: RunState) -> list[Launch]:
    """起こしてよいレーンの手（計画の並びの順）。止めた計画では、実行中のスプリントを中止させる手になる。"""
    snap = _snap(state)
    if snap["state"] == CANCELLED:
        acts = [Launch(lane, row, cancel=sid) for lane, row, sid in snap["running"]]
    else:
        acts = [Launch(lane, row) for lane, row in snap["wakeable"]]
    skip = _skip(state)
    return [a for a in acts if a.key not in skip]


def _finish(state: RunState, outcome: str) -> loop.Next[Act, RunState]:
    return loop.Next.finish(replace(state, outcome=outcome))


def _after_observe(state: RunState, policy: Policy) -> loop.Next[Act, RunState]:
    snap = _snap(state)
    if snap["state"] == COMPLETED:
        return _finish(state, OUTCOME_COMPLETED)
    if snap["state"] == CANCELLED and not snap["running"]:
        return _finish(state, OUTCOME_STOPPED)
    if snap["state"] == RUNNING and snap["complete"]:
        return loop.Next.step(Complete(), state)
    found = _candidates(state) if snap["state"] in (RUNNING, CANCELLED) else []
    if found:
        return loop.Next.step(found[0], state)
    return loop.Next.step(Sleep(policy.poll), state)


def _live_launched(state: RunState, snap: dict[str, Any]) -> tuple[str, ...]:
    """起こした印のうち、まだ起こしたまま待っている行（まだ起こせる行に残っているもの）だけを残す。"""
    waiting = {f"{lane}/{row}" for lane, row in snap["wakeable"]} | {f"cancel:{r[2]}" for r in snap["running"]}
    return tuple(k for k in state.launched if k in waiting)


def _after_launch(state: RunState, act: Launch, value: dict[str, Any], policy: Policy) -> RunState:
    tried = (*state.tried, act.key)
    if value["ok"]:
        return replace(state, tried=tried, launched=(*state.launched, act.key))
    count = state.count(act.key) + 1
    attempts = tuple((k, n) for k, n in state.attempts if k != act.key) + ((act.key, count),)
    error = f"{act.key}: {value['detail']}"
    if count >= policy.attempts:
        return replace(state, tried=tried, attempts=attempts, failed=(*state.failed, act.key), errors=(*state.errors, error))
    return replace(state, tried=tried, attempts=attempts, errors=(*state.errors, error))


def decide(policy: Policy, state: RunState, done: Optional[Done]) -> loop.Next[Act, RunState]:
    """前の手の結果を受けて次の手を返す。"""
    if done is None or isinstance(done.act, Sleep):
        return loop.Next.step(Observe(), state)
    if isinstance(done.act, Observe):
        snap = done.value
        fresh = replace(state, snapshot=snap, tried=(), launched=_live_launched(state, snap))
        return _after_observe(fresh, policy)
    if isinstance(done.act, Launch):
        return _after_observe(_after_launch(state, done.act, done.value, policy), policy)
    if isinstance(done.act, Complete):
        return _finish(state, OUTCOME_COMPLETED) if done.value else loop.Next.step(Observe(), state)
    raise TypeError(f"知らない手: {done.act!r}")
