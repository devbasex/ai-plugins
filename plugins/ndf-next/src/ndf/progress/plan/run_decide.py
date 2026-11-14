"""計画を進める耐久ワークフローの判断（#3097）。DB も DBOS も読まない純粋な関数である。

耐久ワークフロー（`ndf.progress.plan.run`）は、計画とレーンの進行表を読んだ写し（`Observe` の結果）を
この判断へ渡し、次の手を 1 つ受ける。手は 5 つある。

- `Observe`: 計画とレーンの進行表を読み直す
- `Launch`: 止まっているレーンの conductor を起こす。`cancel` があれば、止めた計画で実行中のスプリントを
  中止させる入力で起こす。`resume` があれば、答えが載った後に next が打たれていない実行中の行（3346-5）を、
  答えの後の `ndf progress next` を打つ入力で起こす。耐久ワークフローは conductor へメッセージを送らず、起こすだけである。
  `halt` なら起こさずに、最後の行が終わったレーンのまだ動いているラッパーを止める（#3173）。`goal` があれば、認証の
  失敗で `/goal` が外れた（判定が `stalled`）レーンを、`/goal` を入れ直してそのスプリントを続ける入力で起こし直す（#3243）
- `Complete`: 全レーンの行が終わり、答えていない依頼が無いので計画を完了にする（I16）
- `Report`: 起こせなかったレーンと、止まったレーン（生死の判定が `stalled`・`ended`。`ndf.progress.lane.liveness`）を、
  計画の進行表の依頼（`request_opened`）としてオーガナイザーへ知らせる
- `Withdraw`: 止まりを知らせた依頼のうち、レーンが動き出した（判定が止まりでなくなった）ものを閉じる（#2585）
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
承認ゲートや問いの答えを待つ行は判定が止まりにならないので知らせない。止まりの知らせには、最後に動いた時刻と、
その行の起動の試みの回数と最後の結果（`history`）を載せる（#2585）。止まりは経過の秒でなく状態で決めるので、
起こし直しが失敗していた時間を数えない。知らせた後にレーンが動き出せば（判定を読めた読み直しで、その鍵の止まりが
消えたら）依頼を閉じ（`Withdraw`）、同じ鍵がまた止まりになれば知らせ直す。

行を始める起動がマシン全体のプランの本数の上限で待たされた（結果に `waiting` を持つ。#2999）ときは、起動の失敗に
数えず、その読み直しでは打ち直さない。次の読み直しで数え直して起こす。

認証の失敗で `/goal` が外れたレーン（`stalled`）は、子を起動するアカウントが使えれば（写しの `relaunch`）止まりとして
知らせずに `/goal` を入れ直して起こし直す（#3243）。同じスプリントで起こし直せるのは `Policy.attempts` 回までで、
尽きたら（または起こし直しの起動が尽きたら）止まりとして知らせる。アカウントが使えなければ写しに載らず、知らせる。

区切りで止めた計画（`paused`）では次の行を起こさない。実行中の行は終わりまで流すので、答えの後の起こし直しは
続ける。実行中の行が無くなれば `paused` で終える。起こせなかった起動は、計画が再開してから知らせる。

止めた計画の進行表は終わっていて依頼を書けないので、中止の起動が尽きても `Report` を打たない。実行中の行の中止を
すべて起こせなくなったら、`failed` と `errors` を載せて `cancel_failed` で終える。

最後の行が終わった（未着手と実行中の行が無い）レーンのラッパーがまだ動いていれば、止める（`halt`。#3173）。計画を
完了にする前と、耐久ワークフローを終える前にも先に止める。止める手も起動と同じに回数を数え、尽きたら（計画が動いて
いれば）`halt_failed` の依頼で知らせ、打ち直さない（オーガナイザーが手で止めて依頼を閉じれば、計画は完了できる）。
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
    halt: bool = False
    goal: Optional[str] = None

    @property
    def key(self) -> str:
        if self.halt:
            return f"halt:{self.lane}/{self.row}"
        if self.goal:
            return f"goal:{self.goal}"
        if self.cancel:
            return f"cancel:{self.cancel}"
        if self.resume:
            return f"next:{self.resume}:{self.seq}"
        return f"{self.lane}/{self.row}"

    @property
    def continues(self) -> bool:
        """動いているレーンの続き（中止・答えの後の next・/goal の入れ直し）か。行を始める起動ではない。"""
        return any((self.cancel, self.resume, self.goal))


@dataclass(frozen=True)
class Complete:
    """計画を完了にする手。"""


# 計画の進行表の依頼の中身の kind: 起こせなかったレーン・止まったレーン・終わったのにラッパーを止められないレーン
LAUNCH_FAILED, LANE_STALLED, HALT_FAILED = "launch_failed", "lane_stalled", "halt_failed"


@dataclass(frozen=True)
class Report:
    """レーン `lane` の知らせ `key` を、詳細 `detail` を添えてオーガナイザーへ知らせる手。`kind` は依頼の中身の種類。"""

    lane: str
    key: str
    detail: str
    kind: str = LAUNCH_FAILED
    # 依頼の中身へ足す項目（止まりの知らせの最後に動いた時刻・起動の試みの回数と最後の結果。#2585）
    facts: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class Withdraw:
    """止まりを知らせた依頼 `request`（知らせの鍵 `key`）を、レーン `lane` が動き出したので閉じる手（#2585）。"""

    lane: str
    request: str
    key: str


@dataclass(frozen=True)
class Sleep:
    """`seconds` 秒待つ手。"""

    seconds: float


Act = Union[Observe, Launch, Complete, Report, Withdraw, Sleep]


@dataclass(frozen=True)
class Done:
    """手の結果。`Observe` は写し（`snapshot` の形）、`Launch` は `{"ok": 真偽, "detail": 文}`（打ち直しても起こせない
    失敗は `"final": True` を持つ）、`Complete` は真偽、`Report` は開いた依頼の鍵（計画が終わっていて書けなければ None）、
    `Withdraw` は閉じたら真（計画が動いていないか依頼が既に閉じていれば None）。"""

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
    `final` は打ち直しても起こせない失敗、`unreported` はまだ知らせていない知らせ、
    `reported` は依頼で知らせた起動、`noticed` は知らせると決めた止まりの鍵、`history` は打った起動の
    （レーン, 行, 起動の鍵, 起こせたか, 詳細）の並び（止まりの知らせに試みの回数と最後の結果を載せる）。"""

    snapshot: Optional[dict[str, Any]] = None
    launched: tuple[str, ...] = ()
    waits: tuple[tuple[str, int], ...] = ()
    started: tuple[str, ...] = ()
    tried: tuple[str, ...] = ()
    attempts: tuple[tuple[str, int], ...] = ()
    failed: tuple[str, ...] = ()
    final: tuple[str, ...] = ()
    unreported: tuple[Report, ...] = ()
    reported: tuple[str, ...] = ()
    noticed: tuple[str, ...] = ()
    history: tuple[tuple[str, str, str, bool, str], ...] = ()
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
    打たれていない実行中の行の（レーン, 行, スプリントの ID, 答えの事象の番号）。止まったレーンは空で、`with_stalls` が、
    開いている止まりの知らせの依頼は `with_stall_requests` が載せる。"""
    return {
        "state": state,
        "complete": complete,
        "wakeable": [list(w) for w in wakeable],
        "running": [list(r) for r in running],
        "reported": list(reported),
        "answered": [list(a) for a in answered],
        "stalls": [],
        "unknown": [],
        "judged": False,
        "relaunch": [],
        "stall_requests": [],
        "halts": [],
    }


def with_stalls(
    snap: dict[str, Any],
    stalls: Sequence[Sequence[Any]],
    unknown: Sequence[str] = (),
    judged: bool = True,
    relaunch: Sequence[tuple[str, str, str]] = (),
) -> dict[str, Any]:
    """写しへ生死の判定を載せる。`stalls` は止まったレーンの（レーン, 知らせの鍵, 理由, 最後に動いた時刻, 行）、
    `unknown` は判定が unknown のレーン、`judged` はこの読み直しで判定したか（判定しなければ依頼を閉じない）、
    `relaunch` は `/goal` を入れ直して起こし直せる（アカウントが使える）止まったレーンの（レーン, 行, スプリントの ID）。"""
    judged_part = {"unknown": list(unknown), "judged": judged, "relaunch": [list(r) for r in relaunch]}
    return {**snap, "stalls": [list(t) for t in stalls], **judged_part}


def with_stall_requests(snap: dict[str, Any], requests: Sequence[tuple[str, str, str]]) -> dict[str, Any]:
    """写しへ、開いている止まりの知らせの依頼の（依頼の鍵, レーン, 知らせの鍵）を載せる（#2585）。"""
    return {**snap, "stall_requests": [list(r) for r in requests]}


def with_halts(snap: dict[str, Any], halts: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """写しへ、最後の行が終わったのにラッパーが動いているレーンの（レーン, 最後の行）を載せる（#3173）。"""
    return {**snap, "halts": [list(h) for h in halts]}


def _skip(state: RunState) -> set[str]:
    return set(state.launched) | set(state.tried) | set(state.failed)


def _snap(state: RunState) -> dict[str, Any]:
    if state.snapshot is None:
        raise TypeError("読み直しの前に写しを使った（判断の誤り）")
    return state.snapshot


def _launches(snap: dict[str, Any]) -> list[Launch]:
    """写しから作る起動の手。終わったレーンのラッパーを止める手・起こせる行を始める手・答えの後の next を打たせる手・
    実行中のスプリントを中止させる手（それぞれ計画の並びの順）。"""
    starts = [Launch(lane, row) for lane, row in snap["wakeable"]]
    resumes = [Launch(lane, row, resume=sid, seq=seq) for lane, row, sid, seq in snap["answered"]]
    halts = [Launch(lane, row, halt=True) for lane, row in snap["halts"]]
    goals = [Launch(lane, row, goal=sid) for lane, row, sid in snap["relaunch"]]
    return halts + starts + resumes + goals + [Launch(lane, row, cancel=sid) for lane, row, sid in snap["running"]]


def _relaunchable(state: RunState, act: Launch, policy: Policy) -> bool:
    """`/goal` を入れ直す起こし直しが、まだ打てるか（同じスプリントで起こせた回数が `Policy.attempts` 未満で、尽きていない）。"""
    done = sum(1 for h in state.history if h[2] == act.key and h[3])
    return act.key not in state.failed and done < policy.attempts


def _candidates(state: RunState, policy: Policy) -> list[Launch]:
    """起こしてよいレーンの手（計画の並びの順）。止めた計画では、実行中のスプリントを中止させる手になる。"""
    snap = _snap(state)
    cancelling = snap["state"] == CANCELLED
    acts = [a for a in _launches(snap) if a.halt or (a.cancel is not None) == cancelling]
    skip = _skip(state)
    return [a for a in acts if a.key not in skip and (a.goal is None or _relaunchable(state, a, policy))]


def _finish(state: RunState, outcome: str) -> loop.Next[Act, RunState]:
    return loop.Next.finish(replace(state, outcome=outcome))


# 起動の候補を探す計画の状態
OBSERVED_STATES = (RUNNING, PAUSED, CANCELLED, COMPLETED)


def _end_of(snap: dict[str, Any]) -> Optional[str]:
    """写しの計画が耐久ワークフローを終える状態なら、終わりの語。"""
    if snap["state"] == COMPLETED:
        return OUTCOME_COMPLETED
    if snap["state"] == CANCELLED and not snap["running"]:
        return OUTCOME_STOPPED
    if snap["state"] == PAUSED and not snap["running"]:
        return OUTCOME_PAUSED
    return None


def _after_observe(state: RunState, policy: Policy) -> loop.Next[Act, RunState]:
    snap = _snap(state)
    running = snap["state"] == RUNNING
    found = _candidates(state, policy) if snap["state"] in OBSERVED_STATES else []
    # 終わったレーンのラッパーは、計画を完了にする前と耐久ワークフローを終える前に止める（#3173）
    halts = [a for a in found if a.halt]
    end = _end_of(snap)
    if end is not None:
        return loop.Next.step(halts[0], state) if halts else _finish(state, end)
    if running and snap["complete"]:
        return loop.Next.step(halts[0] if halts else Complete(), state)
    gone = _withdrawable(snap) if running else []
    if gone:
        return loop.Next.step(gone[0], state)
    if running and state.unreported:
        return loop.Next.step(state.unreported[0], state)
    if found:
        return loop.Next.step(found[0], state)
    if snap["state"] == CANCELLED and _cancels_given_up(state, snap):
        return _finish(state, OUTCOME_CANCEL_FAILED)
    return loop.Next.step(Sleep(policy.poll), state)


def _withdrawable(snap: dict[str, Any]) -> list[Withdraw]:
    """開いている止まりの知らせのうち、判定を読めた読み直しでその鍵の止まりが消えたもの（レーンが動き出した）。"""
    if not snap["judged"]:
        return []
    now = {s[1] for s in snap["stalls"]}
    unknown = set(snap["unknown"])
    return [Withdraw(lane, req, key) for req, lane, key in snap["stall_requests"] if key not in now and lane not in unknown]


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


def _fail(state: RunState, act: Launch, detail: str, policy: Policy, final: bool) -> RunState:
    """起動の失敗を 1 回数える。回数が尽きたか打ち直しても起こせない失敗なら `failed` に残し、計画が動いていれば
    知らせる前の起動（`unreported`）に置く（止めた計画の進行表へは依頼を書けない）。"""
    lane, key = act.lane, act.key
    count = state.count(key) + 1
    attempts = tuple((k, n) for k, n in state.attempts if k != key) + ((key, count),)
    error = f"{key}: {detail}"
    state = replace(state, attempts=attempts, errors=(*state.errors, error))
    if count < policy.attempts and not final:
        return state
    # 尽きた止める手は打ち直さない（オーガナイザーが手で止めて依頼を閉じる。閉じた後に止め直して依頼を開き直さない）
    gave_up = final or act.halt
    state = replace(state, failed=(*state.failed, key), final=(*state.final, key) if gave_up else state.final)
    if _snap(state)["state"] == CANCELLED:
        return state
    kind = HALT_FAILED if act.halt else LAUNCH_FAILED
    return replace(state, unreported=(*state.unreported, Report(lane, key, error, kind)))


def _stall_report(state: RunState, lane: str, key: str, reason: str, moved: Optional[str], row: str) -> Report:
    """止まりの知らせ。最後に動いた時刻と、その行の起動の試みの回数と最後の結果を本文と中身に載せる（#2585）。"""
    tries = [h for h in state.history if h[0] == lane and h[1] == row]
    last = None if not tries else ("起こせた" if tries[-1][3] else f"起こせなかった: {tries[-1][4]}")
    detail = f"{reason}（最後に動いた時刻 {moved or '不明'}・起動の試み {len(tries)} 回・最後の結果 {last or '無し'}）"
    return Report(lane, key, detail, LANE_STALLED, (("moved", moved), ("launches", len(tries)), ("last_launch", last)))


def _notice_stalls(state: RunState, policy: Policy) -> RunState:
    """写しの止まりのうち、まだ知らせると決めていないものを知らせる前の知らせ（`unreported`）に置く。

    `/goal` を入れ直して起こし直せるレーン（#3243）は、起こし直しが尽きるまで知らせない。
    """
    snap = _snap(state)
    goals = (Launch(lane, row, goal=sid) for lane, row, sid in snap["relaunch"])
    relaunching = {a.lane for a in goals if _relaunchable(state, a, policy)}
    fresh = [s for s in snap["stalls"] if s[1] not in state.noticed and s[0] not in relaunching]
    if not fresh:
        return state
    queued = tuple(_stall_report(state, *s) for s in fresh)
    return replace(state, unreported=(*state.unreported, *queued), noticed=(*state.noticed, *(s[1] for s in fresh)))


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
        why = "止めたラッパーが {:g} 秒たっても動いている" if act.halt else "起こした conductor が {:g} 秒の間に行を始めない"
        state = _fail(state, act, why.format(policy.up), policy, False)
    return state


def _after_launch(state: RunState, act: Launch, value: dict[str, Any], policy: Policy) -> RunState:
    state = replace(state, tried=(*state.tried, act.key))
    if value.get("waiting"):
        # マシンの上限で待たされた起動は失敗でも起動でもない（次の読み直しで数え直す）
        return state
    if not act.halt:
        state = replace(state, history=(*state.history, (act.lane, act.row, act.key, bool(value["ok"]), str(value["detail"]))))
    if value["ok"]:
        return replace(state, launched=(*state.launched, act.key), started=(*state.started, act.key))
    return _fail(state, act, value["detail"], policy, bool(value.get("final")))


def _after_withdraw(state: RunState, act: Withdraw, value: Any, policy: Policy) -> loop.Next[Act, RunState]:
    """閉じた止まりの知らせを写しと印から外す。同じ鍵がまた止まりになれば知らせ直す。"""
    snap = _snap(state)
    left = [r for r in snap["stall_requests"] if r[0] != act.request]
    state = replace(
        state,
        snapshot={**snap, "stall_requests": left},
        noticed=tuple(k for k in state.noticed if k != act.key),
        reported=tuple(k for k in state.reported if k != act.key),
    )
    if value is None:
        # 計画が動いていないか、依頼が既に閉じていた（写しが古い）。読み直して従う
        return loop.Next.step(Observe(), state)
    return _after_observe(state, policy)


def decide(policy: Policy, state: RunState, done: Optional[Done]) -> loop.Next[Act, RunState]:
    """前の手の結果を受けて次の手を返す。"""
    if done is None or isinstance(done.act, Sleep):
        return loop.Next.step(Observe(), state)
    if isinstance(done.act, Observe):
        snap = done.value
        fresh = _expire_launched(replace(_reopen_closed(state, snap), snapshot=snap, tried=()), policy)
        return _after_observe(_notice_stalls(fresh, policy), policy)
    if isinstance(done.act, Launch):
        return _after_observe(_after_launch(state, done.act, done.value, policy), policy)
    if isinstance(done.act, Report):
        rest = replace(state, unreported=tuple(u for u in state.unreported if u.key != done.act.key))
        if done.value is None:
            # 計画が終わっていて依頼を書けなかった（写しが古い）。読み直して計画の状態に従う
            return loop.Next.step(Observe(), rest)
        return _after_observe(replace(rest, reported=(*rest.reported, done.act.key)), policy)
    if isinstance(done.act, Withdraw):
        return _after_withdraw(state, done.act, done.value, policy)
    if isinstance(done.act, Complete):
        return _finish(state, OUTCOME_COMPLETED) if done.value else loop.Next.step(Observe(), state)
    raise TypeError(f"知らない手: {done.act!r}")
