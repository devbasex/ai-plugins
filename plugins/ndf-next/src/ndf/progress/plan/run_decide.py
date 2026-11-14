"""計画を進める耐久ワークフローの判断（#3097）。DB も DBOS も読まない純粋な関数である。

耐久ワークフロー（`ndf.progress.plan.run`）は、計画とレーンの進行表を読んだ写し（`Observe` の結果）を
この判断へ渡し、次の手を 1 つ受ける。手は 5 つある。

- `Observe`: 計画とレーンの進行表を読み直す
- `Launch`: 止まっているレーンの conductor を起こす。`cancel` があれば、止めた計画で実行中のスプリントを
  中止させる入力で起こす。耐久ワークフローは conductor へメッセージを送らず、起こすだけである
- `Complete`: 全レーンの行が終わり、答えていない依頼が無いので計画を完了にする（I16）
- `Report`: 起こせなかったレーンを、計画の進行表の依頼（`request_opened`）としてオーガナイザーへ知らせる
- `Sleep`: 次に読み直すまで待つ

同じ読み直しの間に同じレーンの行を 2 回起こさない。起こしたレーンの行は、行が実行中になるか終わるまで
起こし直さない（`launched`）。起動の失敗は読み直すたびに打ち直し、回数が尽きたら（打ち直しても起こせない失敗
`final` ならその場で）`failed` に残し、`Report` で計画の進行表へ書いて `ndf lanes wait` に届ける。オーガナイザーが
その依頼を閉じたら（写しの `reported` から消えたら）、`failed` と回数から外して起動の候補へ戻す。

止めた計画の進行表は終わっていて依頼を書けないので、中止の起動が尽きても `Report` を打たない。実行中の行の中止を
すべて起こせなくなったら、`failed` と `errors` を載せて `cancel_failed` で終える。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Optional, Union

from ndf.core import loop

# 計画の状態（契約 progress.json の table_states.plan）は、レーンの行の状態と同じ語の定数を使う
from ndf.progress.lane.types import CANCELLED, COMPLETED, RUNNING

# 耐久ワークフローの終わりの語
OUTCOME_COMPLETED, OUTCOME_STOPPED, OUTCOME_CANCEL_FAILED = "completed", "stopped", "cancel_failed"


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
class Report:
    """起こせなかったレーン `lane` の起動 `key` を、詳細 `detail` を添えてオーガナイザーへ知らせる手。"""

    lane: str
    key: str
    detail: str


@dataclass(frozen=True)
class Sleep:
    """`seconds` 秒待つ手。"""

    seconds: float


Act = Union[Observe, Launch, Complete, Report, Sleep]


@dataclass(frozen=True)
class Done:
    """手の結果。`Observe` は写し（`snapshot` の形）、`Launch` は `{"ok": 真偽, "detail": 文}`（打ち直しても起こせない
    失敗は `"final": True` を持つ）、`Complete` は真偽、`Report` は開いた依頼の鍵。"""

    act: Act
    value: Any = None


@dataclass(frozen=True)
class Policy:
    """読み直しの間隔の秒と、レーン 1 つの起動を打つ回数（最初の 1 回を含む）。"""

    poll: float
    attempts: int


@dataclass(frozen=True)
class RunState:
    """判断の状態。`launched` は起こして行の始まりを待つ起動、`started` は起こせた起動のすべて、
    `tried` は今の読み直しで打った起動、`attempts` は失敗した起動の回数、`reported` は依頼で知らせた起動。"""

    snapshot: Optional[dict[str, Any]] = None
    launched: tuple[str, ...] = ()
    started: tuple[str, ...] = ()
    tried: tuple[str, ...] = ()
    attempts: tuple[tuple[str, int], ...] = ()
    failed: tuple[str, ...] = ()
    reported: tuple[str, ...] = ()
    outcome: Optional[str] = None
    errors: tuple[str, ...] = field(default=())

    def count(self, key: str) -> int:
        return next((n for k, n in self.attempts if k == key), 0)

    def as_result(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "launched": list(self.started), "failed": list(self.failed), "errors": list(self.errors)}


def snapshot(
    state: str,
    complete: bool,
    wakeable: list[tuple[str, str]],
    running: list[tuple[str, str, str]],
    reported: Sequence[str] = (),
) -> dict[str, Any]:
    """`Observe` の結果の形（耐久の記録へ書くので JSON にできる値だけにする）。`running` は（レーン, 行, スプリントの ID）、
    `reported` は計画の進行表に開いている、起こせなかった知らせの依頼の起動の鍵。"""
    return {
        "state": state,
        "complete": complete,
        "wakeable": [list(w) for w in wakeable],
        "running": [list(r) for r in running],
        "reported": list(reported),
    }


def _skip(state: RunState) -> set[str]:
    return set(state.launched) | set(state.tried) | set(state.failed)


def _snap(state: RunState) -> dict[str, Any]:
    if state.snapshot is None:
        raise TypeError("読み直しの前に写しを使った（判断の誤り）")
    return state.snapshot


def _launches(snap: dict[str, Any]) -> list[Launch]:
    """写しから作る起動の手。起こせる行を始める手と、実行中のスプリントを中止させる手（それぞれ計画の並びの順）。"""
    starts = [Launch(lane, row) for lane, row in snap["wakeable"]]
    return starts + [Launch(lane, row, cancel=sid) for lane, row, sid in snap["running"]]


def _candidates(state: RunState) -> list[Launch]:
    """起こしてよいレーンの手（計画の並びの順）。止めた計画では、実行中のスプリントを中止させる手になる。"""
    snap = _snap(state)
    cancelling = snap["state"] == CANCELLED
    acts = [a for a in _launches(snap) if (a.cancel is not None) == cancelling]
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
    if snap["state"] == CANCELLED and _cancels_given_up(state, snap):
        return _finish(state, OUTCOME_CANCEL_FAILED)
    return loop.Next.step(Sleep(policy.poll), state)


def _cancels_given_up(state: RunState, snap: dict[str, Any]) -> bool:
    """止めた計画で、実行中の行の中止の起動がすべて尽きたか（もう起こせる中止が無い）。"""
    keys = {a.key for a in _launches(snap) if a.cancel is not None}
    return bool(keys) and keys <= set(state.failed)


def _reopen_closed(state: RunState, snap: dict[str, Any]) -> RunState:
    """知らせた依頼をオーガナイザーが閉じた起動を、`failed` と回数から外して起動の候補へ戻す。"""
    still = set(snap.get("reported", ()))
    closed = {k for k in state.reported if k not in still}
    if not closed:
        return state
    return replace(
        state,
        failed=tuple(k for k in state.failed if k not in closed),
        attempts=tuple((k, n) for k, n in state.attempts if k not in closed),
        reported=tuple(k for k in state.reported if k not in closed),
    )


def _live_launched(state: RunState, snap: dict[str, Any]) -> tuple[str, ...]:
    """起こした印のうち、まだ起こしたまま待っている行（まだ起こせる行に残っているもの）だけを残す。"""
    waiting = {a.key for a in _launches(snap)}
    return tuple(k for k in state.launched if k in waiting)


def _after_launch(state: RunState, act: Launch, value: dict[str, Any], policy: Policy) -> RunState:
    tried = (*state.tried, act.key)
    if value["ok"]:
        return replace(state, tried=tried, launched=(*state.launched, act.key), started=(*state.started, act.key))
    count = state.count(act.key) + 1
    attempts = tuple((k, n) for k, n in state.attempts if k != act.key) + ((act.key, count),)
    error = f"{act.key}: {value['detail']}"
    if count >= policy.attempts or value.get("final"):
        return replace(state, tried=tried, attempts=attempts, failed=(*state.failed, act.key), errors=(*state.errors, error))
    return replace(state, tried=tried, attempts=attempts, errors=(*state.errors, error))


def decide(policy: Policy, state: RunState, done: Optional[Done]) -> loop.Next[Act, RunState]:
    """前の手の結果を受けて次の手を返す。"""
    if done is None or isinstance(done.act, Sleep):
        return loop.Next.step(Observe(), state)
    if isinstance(done.act, Observe):
        snap = done.value
        fresh = replace(_reopen_closed(state, snap), snapshot=snap, tried=(), launched=_live_launched(state, snap))
        return _after_observe(fresh, policy)
    if isinstance(done.act, Launch):
        after = _after_launch(state, done.act, done.value, policy)
        # 止めた計画の進行表は終わっていて依頼を書けない（尽きた中止は _after_observe が結果に載せて終える）
        if done.act.key in after.failed and done.act.key not in state.failed and _snap(state)["state"] != CANCELLED:
            return loop.Next.step(Report(done.act.lane, done.act.key, after.errors[-1]), after)
        return _after_observe(after, policy)
    if isinstance(done.act, Report):
        return _after_observe(replace(state, reported=(*state.reported, done.act.key)), policy)
    if isinstance(done.act, Complete):
        return _finish(state, OUTCOME_COMPLETED) if done.value else loop.Next.step(Observe(), state)
    raise TypeError(f"知らない手: {done.act!r}")
