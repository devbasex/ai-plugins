"""計画を進める耐久ワークフローの判断（#3097）。DB も DBOS も読まない純粋な関数である。

耐久ワークフロー（`ndf.progress.plan.run`）は、計画とレーンの進行表を読んだ写し（`Observe` の結果）を
この判断へ渡し、次の手を 1 つ受ける。手は 5 つある。

- `Observe`: 計画とレーンの進行表を読み直す
- `Launch`: 止まっているレーンの conductor を起こす。`cancel` があれば、止めた計画で実行中のスプリントを
  中止させる入力で起こす。`resume` があれば、答えが載った後に next が打たれていない実行中の行（3346-5）を、
  答えの後の `ndf progress next` を打つ入力で起こす。耐久ワークフローは conductor へメッセージを送らず、起こすだけである
- `Complete`: 全レーンの行が終わり、答えていない依頼が無いので計画を完了にする（I16）
- `Report`: 起こせなかったレーンと、止まったレーン（生死の判定が `stalled`・`ended`。`ndf.progress.lane.liveness`）を、
  計画の進行表の依頼（`request_opened`）としてオーガナイザーへ知らせる
- `Sleep`: 次に読み直すまで待つ

答えの後の起こし直しの鍵は答えの事象の番号を含む。同じ答えでは 1 回だけ起こし、次の答えが載れば新しい鍵で起こす。

同じ読み直しの間に同じレーンの行を 2 回起こさない。起こしたレーンの行は、行が実行中になるか終わるまで
起こし直さない（`launched`）。ただし起こしてから期限（`Policy.up` の秒）までに行が実行中にならなければ
（conductor が `row start` に着く前に落ちた・`row start` が断った など）、起動の失敗の 1 回として数える。
起動の失敗は読み直すたびに打ち直し、回数が尽きたら（打ち直しても起こせない失敗 `final` ならその場で）`failed` に
残し、`Report` で計画の進行表へ書いて `ndf lanes wait` に届ける（知らせる前の起動は `unreported`）。オーガナイザーが
その依頼を閉じたら（写しの `reported` から消えたら）、打ち直せば起こせる失敗は `failed` と回数から外して起動の候補へ
戻す。`final` の失敗は打ち直しても同じ失敗になるので `failed` に残し、行が動くか中止されるまで起こさない。

止まりの知らせの鍵はレーン・行・判定の種類から作り（`liveness.notice_key`）、同じ鍵では 1 回だけ知らせる（`noticed`）。
承認ゲートや問いの答えを待つ行は判定が止まりにならないので知らせない。

区切りで止めた計画（`paused`）では次の行を起こさない。実行中の行は終わりまで流すので、答えの後の起こし直しは
続ける。実行中の行が無くなれば `paused` で終える。起こせなかった起動は、計画が再開してから知らせる。

止めた計画の進行表は終わっていて依頼を書けないので、中止の起動が尽きても `Report` を打たない。実行中の行の中止を
すべて起こせなくなったら、`failed` と `errors` を載せて `cancel_failed` で終える。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Optional, Union

from ndf.core import loop

# 計画の状態（契約 progress.json の table_states.plan）は、レーンの行の状態と同じ語の定数を使う
from ndf.progress.lane.types import CANCELLED, COMPLETED, PAUSED, RUNNING

# 耐久ワークフローの終わりの語
OUTCOME_COMPLETED, OUTCOME_STOPPED, OUTCOME_CANCEL_FAILED = "completed", "stopped", "cancel_failed"
# 区切りで止めた計画の終わりは、計画の状態と同じ語にする
OUTCOME_PAUSED = PAUSED
# 終えた後に同じ計画で打ち直したら、次の番号の ID で新しく始める終わり（中止を試し直す・再開した計画を続ける）
OUTCOMES_RESTARTABLE = (OUTCOME_CANCEL_FAILED, OUTCOME_PAUSED)


@dataclass(frozen=True)
class Observe:
    """計画とレーンの進行表を読み直す手。"""


@dataclass(frozen=True)
class Launch:
    """レーン `lane` の conductor を、行 `row` を始める入力で起こす手。`cancel` があればそのスプリントを中止する入力、
    `resume` があればそのスプリントへ答えの後の next を打つ入力（`seq` は答えの事象の番号）で起こす。"""

    lane: str
    row: str
    cancel: Optional[str] = None
    resume: Optional[str] = None
    seq: int = 0

    @property
    def key(self) -> str:
        if self.cancel:
            return f"cancel:{self.cancel}"
        if self.resume:
            return f"next:{self.resume}:{self.seq}"
        return f"{self.lane}/{self.row}"


@dataclass(frozen=True)
class Complete:
    """計画を完了にする手。"""


# 計画の進行表の依頼の中身の kind: 起こせなかったレーンと、止まったレーン
LAUNCH_FAILED, LANE_STALLED = "launch_failed", "lane_stalled"


@dataclass(frozen=True)
class Report:
    """レーン `lane` の知らせ `key` を、詳細 `detail` を添えてオーガナイザーへ知らせる手。`kind` は依頼の中身の種類。"""

    lane: str
    key: str
    detail: str
    kind: str = LAUNCH_FAILED


@dataclass(frozen=True)
class Sleep:
    """`seconds` 秒待つ手。"""

    seconds: float


Act = Union[Observe, Launch, Complete, Report, Sleep]


@dataclass(frozen=True)
class Done:
    """手の結果。`Observe` は写し（`snapshot` の形）、`Launch` は `{"ok": 真偽, "detail": 文}`（打ち直しても起こせない
    失敗は `"final": True` を持つ）、`Complete` は真偽、`Report` は開いた依頼の鍵（計画が終わっていて書けなければ None）。"""

    act: Act
    value: Any = None


@dataclass(frozen=True)
class Policy:
    """読み直しの間隔の秒と、レーン 1 つの起動を打つ回数（最初の 1 回を含む）。"""

    poll: float
    attempts: int
    # 起こしてから行が実行中になるまでを待つ秒。越えたら起動の失敗の 1 回として数える
    up: float = float("inf")


@dataclass(frozen=True)
class RunState:
    """判断の状態。`launched` は起こして行の始まりを待つ起動、`waits` は起こしてから読み直した回数、
    `started` は起こせた起動のすべて、`tried` は今の読み直しで打った起動、`attempts` は失敗した起動の回数、
    `final` は打ち直しても起こせない失敗、`unreported` はまだ知らせていない失敗（レーン, 起動, 詳細）、
    `reported` は依頼で知らせた起動、`noticed` は知らせると決めた止まりの鍵。"""

    snapshot: Optional[dict[str, Any]] = None
    launched: tuple[str, ...] = ()
    waits: tuple[tuple[str, int], ...] = ()
    started: tuple[str, ...] = ()
    tried: tuple[str, ...] = ()
    attempts: tuple[tuple[str, int], ...] = ()
    failed: tuple[str, ...] = ()
    final: tuple[str, ...] = ()
    unreported: tuple[tuple[str, str, str, str], ...] = ()
    reported: tuple[str, ...] = ()
    noticed: tuple[str, ...] = ()
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
    answered: Sequence[tuple[str, str, str, int]] = (),
) -> dict[str, Any]:
    """`Observe` の結果の形（耐久の記録へ書くので JSON にできる値だけにする）。`running` は（レーン, 行, スプリントの ID）、
    `reported` は計画の進行表に開いている、起こせなかった知らせの依頼の起動の鍵、`answered` は答えが載った後に next が
    打たれていない実行中の行の（レーン, 行, スプリントの ID, 答えの事象の番号）。止まったレーンは空で、`with_stalls` が載せる。"""
    return {
        "state": state,
        "complete": complete,
        "wakeable": [list(w) for w in wakeable],
        "running": [list(r) for r in running],
        "reported": list(reported),
        "answered": [list(a) for a in answered],
        "stalls": [],
    }


def with_stalls(snap: dict[str, Any], stalls: Sequence[tuple[str, str, str]]) -> dict[str, Any]:
    """写しへ止まったレーンの（レーン, 知らせの鍵, 理由）を載せる。"""
    return {**snap, "stalls": [list(t) for t in stalls]}


def _skip(state: RunState) -> set[str]:
    return set(state.launched) | set(state.tried) | set(state.failed)


def _snap(state: RunState) -> dict[str, Any]:
    if state.snapshot is None:
        raise TypeError("読み直しの前に写しを使った（判断の誤り）")
    return state.snapshot


def _launches(snap: dict[str, Any]) -> list[Launch]:
    """写しから作る起動の手。起こせる行を始める手・答えの後の next を打たせる手・実行中のスプリントを中止させる手
    （それぞれ計画の並びの順）。"""
    starts = [Launch(lane, row) for lane, row in snap["wakeable"]]
    resumes = [Launch(lane, row, resume=sid, seq=seq) for lane, row, sid, seq in snap["answered"]]
    return starts + resumes + [Launch(lane, row, cancel=sid) for lane, row, sid in snap["running"]]


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
    if snap["state"] == PAUSED and not snap["running"]:
        return _finish(state, OUTCOME_PAUSED)
    if snap["state"] == RUNNING and snap["complete"]:
        return loop.Next.step(Complete(), state)
    found = _candidates(state) if snap["state"] in (RUNNING, PAUSED, CANCELLED) else []
    if snap["state"] == RUNNING and state.unreported:
        return loop.Next.step(Report(*state.unreported[0]), state)
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
    """知らせた依頼をオーガナイザーが閉じた起動を、`failed` と回数から外して起動の候補へ戻す。

    打ち直しても起こせない失敗（`final`）は戻さない。戻すと同じ失敗で依頼が開き直し、閉じるたびに起こし続ける。
    """
    still = set(snap["reported"])
    closed = {k for k in state.reported if k not in still}
    if not closed:
        return state
    back = closed - set(state.final)
    return replace(
        state,
        failed=tuple(k for k in state.failed if k not in back),
        attempts=tuple((k, n) for k, n in state.attempts if k not in back),
        reported=tuple(k for k in state.reported if k not in closed),
    )


def _fail(state: RunState, lane: str, key: str, detail: str, policy: Policy, final: bool) -> RunState:
    """起動の失敗を 1 回数える。回数が尽きたか打ち直しても起こせない失敗なら `failed` に残し、計画が動いていれば
    知らせる前の起動（`unreported`）に置く（止めた計画の進行表へは依頼を書けない）。"""
    count = state.count(key) + 1
    attempts = tuple((k, n) for k, n in state.attempts if k != key) + ((key, count),)
    error = f"{key}: {detail}"
    state = replace(state, attempts=attempts, errors=(*state.errors, error))
    if count < policy.attempts and not final:
        return state
    state = replace(state, failed=(*state.failed, key), final=(*state.final, key) if final else state.final)
    if _snap(state)["state"] == CANCELLED:
        return state
    return replace(state, unreported=(*state.unreported, (lane, key, error, LAUNCH_FAILED)))


def _notice_stalls(state: RunState) -> RunState:
    """写しの止まりのうち、まだ知らせると決めていないものを知らせる前の起動（`unreported`）に置く。"""
    snap = _snap(state)
    fresh = [(lane, key, reason) for lane, key, reason in snap["stalls"] if key not in state.noticed]
    if not fresh:
        return state
    queued = tuple((lane, key, reason, LANE_STALLED) for lane, key, reason in fresh)
    return replace(state, unreported=(*state.unreported, *queued), noticed=(*state.noticed, *(k for _, k, _ in fresh)))


def _expire_launched(state: RunState, policy: Policy) -> RunState:
    """起こした印のうち、まだ起こしたまま待っている行（まだ起こせる行に残っているもの）だけを残し、読み直しの回数を
    1 つ進める。期限（`Policy.up`）までに行が実行中にならなかった起動は、起動の失敗の 1 回として数える。"""
    waiting = {a.key: a for a in _launches(_snap(state))}
    counts = dict(state.waits)
    kept: list[str] = []
    waits: list[tuple[str, int]] = []
    expired: list[Launch] = []
    for key in state.launched:
        if key not in waiting:
            continue
        n = counts[key] + 1 if key in counts else 1
        if n * policy.poll >= policy.up:
            expired.append(waiting[key])
        else:
            kept.append(key)
            waits.append((key, n))
    state = replace(state, launched=tuple(kept), waits=tuple(waits))
    for act in expired:
        state = _fail(state, act.lane, act.key, f"起こした conductor が {policy.up:g} 秒の間に行を始めない", policy, False)
    return state


def _after_launch(state: RunState, act: Launch, value: dict[str, Any], policy: Policy) -> RunState:
    state = replace(state, tried=(*state.tried, act.key))
    if value["ok"]:
        return replace(state, launched=(*state.launched, act.key), started=(*state.started, act.key))
    return _fail(state, act.lane, act.key, value["detail"], policy, bool(value.get("final")))


def decide(policy: Policy, state: RunState, done: Optional[Done]) -> loop.Next[Act, RunState]:
    """前の手の結果を受けて次の手を返す。"""
    if done is None or isinstance(done.act, Sleep):
        return loop.Next.step(Observe(), state)
    if isinstance(done.act, Observe):
        snap = done.value
        fresh = _expire_launched(replace(_reopen_closed(state, snap), snapshot=snap, tried=()), policy)
        return _after_observe(_notice_stalls(fresh), policy)
    if isinstance(done.act, Launch):
        return _after_observe(_after_launch(state, done.act, done.value, policy), policy)
    if isinstance(done.act, Report):
        rest = replace(state, unreported=tuple(u for u in state.unreported if u[1] != done.act.key))
        if done.value is None:
            # 計画が終わっていて依頼を書けなかった（写しが古い）。読み直して計画の状態に従う
            return loop.Next.step(Observe(), rest)
        return _after_observe(replace(rest, reported=(*rest.reported, done.act.key)), policy)
    if isinstance(done.act, Complete):
        return _finish(state, OUTCOME_COMPLETED) if done.value else loop.Next.step(Observe(), state)
    raise TypeError(f"知らない手: {done.act!r}")
