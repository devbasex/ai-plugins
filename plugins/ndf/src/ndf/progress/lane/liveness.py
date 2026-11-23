"""レーンの生死の判定（#3097・N3）: 実行中の行を持つレーンが動いているか・正常に待っているか・止まったかを 1 つに決める。

DB もプロセスも読まない純粋な関数である。材料は 3 つで、読むのは呼び手（耐久ワークフローの読み直しと `ndf lanes status`）:

- レーンのステートマシンの行（`LaneRow`）: 実行中か・回答が載った後に next を待っているか
- スプリントのステートマシンの待ち（`ndf progress wait <ID> --timeout 0` の結果）: 承認ゲートの回答を待っている（`gate`）・
  工程を流す作業が動いている（`working`）・conductor の手を待っている（`idle`）・読めない（`unknown`）
- ラッパーの一覧の行（`ndf relay ls` の 1 行）: 動いているか・ラッパーの状態（`running`・`waiting`・`stalled`）と理由。
  一覧を読めなかった回は `listed` を偽にする

判定の規則（上から先に当たったもの）:

1. 実行中の行が無い → `idle`（判定しない）
2. 回答が載った後に next が打たれていない → `resuming`（耐久ワークフローが conductor を再起動する。止まりにしない）
3. スプリントが承認ゲートや質問の回答を待っている → `waiting_answer`（正常な待ち。止まりとして知らせない）
4. ラッパーの一覧を読めなかった・スプリントの待ちを読めなかった → `unknown`（一覧に無いことを止まりと読まない）
5. ラッパーが動いていて状態が `stalled`（認証の失敗で `/goal` が外れた など） → `stalled`（ラッパーの理由を添える）
6. ラッパーが動いていて、conductor が応答を終え、バックグラウンドの作業が無いまま `parallel-run.idle` の秒を過ぎた → `idle_conductor`
   （#4036。活動（`Activity`）を読めなければ当たらない）
7. ラッパーが動いている → `alive`
8. スプリントの工程を流す作業が動いている → `working`（conductor が落ちても作業は続く。終わってから止まりになる）
9. ほか（ラッパーが一覧に無い・止まっていて、スプリントは conductor の手を待つ） → `ended`

止まりとして知らせるのは `stalled`・`ended`・`idle_conductor` だけである（`STALL_KINDS`）。

`stalled`（認証の失敗で `/goal` が外れた）のレーンは、子を起動するアカウントが使える（`Probes.account_ok`）なら、
耐久ワークフローが `/goal` を入れた入力で再起動する（#3243）。使えなければ止まりとして知らせる。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Optional

from ndf.contracts import causes, limits, result
from ndf.progress.lane.types import LaneRow

# 判定の種類
IDLE, RESUMING, WAITING_ANSWER, UNKNOWN, STALLED, ALIVE, WORKING, ENDED = (
    "idle",
    "resuming",
    "waiting_answer",
    "unknown",
    "stalled",
    "alive",
    "working",
    "ended",
)
IDLE_CONDUCTOR = "idle_conductor"
# 止まりとして知らせる判定
STALL_KINDS = (STALLED, ENDED, IDLE_CONDUCTOR)
# 何もしていないとみなすまでの秒を持つ方針（`contracts/limits.json`）
IDLE_POLICY = "parallel-run.idle"
# スプリントのステートマシンの待ち（`sprint_wait` が `ndf progress wait` の結果から決める）
SPRINT_GATE, SPRINT_WORKING, SPRINT_IDLE, SPRINT_UNKNOWN = "gate", "working", "idle", "unknown"
# ラッパーの状態のうち、止まりと読むもの（`ndf relay ls` の `state`）
WRAPPER_STALLED = "stalled"
# `ndf progress wait --timeout 0` が「作業がまだ動いている」で返す停止理由の種類
WAIT_TIMEOUT = "wait_timeout"


@dataclass(frozen=True)
class Liveness:
    """1 本のレーンの判定。`kind` は判定の種類、`reason` は人が読む理由、`row` と `sprint_id` は判定した行。"""

    kind: str
    reason: str
    row: Optional[str] = None
    sprint_id: Optional[str] = None
    # `idle_conductor` のときだけ入る: 最後に動いた時刻（UNIX 時刻）と、まだ走っているとみなすバックグラウンドの作業の数
    moved_at: Optional[float] = None
    background: Optional[int] = None

    @property
    def stall(self) -> bool:
        return self.kind in STALL_KINDS

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "row": self.row,
            "sprint_id": self.sprint_id,
            "stall": self.stall,
            "moved_at": self.moved_at,
            "background": self.background,
        }


@dataclass(frozen=True)
class Activity:
    """conductor の活動（#4036）。`turn_done` は最後の応答が終わったか、`moved` は最後の応答と終わったバックグラウンドの作業の
    時刻のうち最も遅いもの（UNIX 時刻）、`background` は終わったバックグラウンドの作業を除いた数（確かめられなければ None）。"""

    turn_done: bool
    moved: Optional[float]
    background: Optional[int]


def idle_after() -> float:
    """何もしていないとみなすまでの秒（`contracts/limits.json` の `parallel-run.idle`）。"""
    return limits.seconds(IDLE_POLICY)


def _idle_for(activity: Optional[Activity], now: float) -> Optional[float]:
    """応答を終え、バックグラウンドの作業が無いまま閾値を過ぎていれば、最後に動いてからの秒。当たらなければ None。"""
    if activity is None or not activity.turn_done or activity.background != 0 or activity.moved is None:
        return None
    quiet = now - activity.moved
    return quiet if quiet >= idle_after() else None


def sprint_wait(out: Optional[Mapping[str, Any]]) -> str:
    """`ndf progress wait <ID> --timeout 0` の結果 JSON から、スプリントの待ちの種類を決める。読めなければ `unknown`。"""
    if not out:
        return SPRINT_UNKNOWN
    status = out.get("status")
    if status == result.GATE:
        return SPRINT_GATE
    if status == result.STOPPED and causes.kind_of(out.get("cause")) == WAIT_TIMEOUT:
        return SPRINT_WORKING
    return SPRINT_IDLE if status in (result.OK, result.STOPPED) else SPRINT_UNKNOWN


def wrapper_running(row: Optional[Mapping[str, Any]]) -> bool:
    """ラッパーの一覧（`ndf relay ls`）の行が動いているか（一覧に無ければ None で、動いていない）。"""
    return bool(row and row.get("running"))


def judge(
    row: Optional[LaneRow],
    sprint: str,
    wrapper: Optional[Mapping[str, Any]],
    listed: bool,
    activity: Optional[Activity] = None,
    now: Optional[float] = None,
) -> Liveness:
    """1 本のレーンの生死を決める。`row` はレーンの実行中の行（無ければ None）、`sprint` は `sprint_wait` の値、
    `wrapper` はラッパーの一覧の行（一覧に無ければ None）、`listed` は一覧を読めたか、`activity` は conductor の活動
    （読まなければ None）、`now` は判定の時刻（省けば今の時刻）。"""
    if row is None:
        return Liveness(IDLE, "実行中の行が無い")
    key, sid = row.key, row.sprint_id
    if row.awaiting_next:
        return Liveness(RESUMING, "回答が載った後の next を conductor が打つのを待つ（耐久ワークフローが再起動する）", key, sid)
    if sprint == SPRINT_GATE:
        return Liveness(WAITING_ANSWER, "スプリントが承認ゲートか質問の回答を待っている", key, sid)
    if not listed or sprint == SPRINT_UNKNOWN:
        return Liveness(UNKNOWN, "ラッパーの一覧かスプリントの待ちを読めない", key, sid)
    running = wrapper_running(wrapper)
    if running and wrapper is not None and wrapper.get("state") == WRAPPER_STALLED:
        return Liveness(STALLED, f"conductor のラッパーが止まっている: {wrapper.get('reason') or '理由の記録が無い'}", key, sid)
    quiet = _idle_for(activity, time.time() if now is None else now) if running else None
    if quiet is not None and activity is not None:
        reason = f"conductor が応答を終えてから {int(quiet // 60)} 分、バックグラウンドの作業が無いまま動いていない"
        return Liveness(IDLE_CONDUCTOR, reason, key, sid, activity.moved, activity.background)
    if running:
        return Liveness(ALIVE, "conductor のラッパーが動いている", key, sid)
    if sprint == SPRINT_WORKING:
        return Liveness(WORKING, "conductor のラッパーは無いが、スプリントの工程を流す作業が動いている", key, sid)
    return Liveness(ENDED, "conductor のラッパーが動いておらず、スプリントは conductor の手を待っている", key, sid)


def notice_key(lane: str, verdict: Liveness) -> str:
    """止まりの知らせのキー。同じレーン・同じ行・同じ判定では 1 回だけ知らせ、行か判定が変われば新しいキーになる。"""
    return f"stall:{lane}/{verdict.row}:{verdict.kind}"


def running_row(rows: list[LaneRow]) -> Optional[LaneRow]:
    """レーンの実行中の行（スプリントの ID が付いたもの）。無ければ None。"""
    return next((r for r in rows if r.active), None)


@dataclass(frozen=True)
class Probes:
    """判定の材料を読む口。`wrapper_name` はレーン名からラッパーの名前、`wrappers` はラッパーの一覧（読めなければ None）、
    `sprint` はスプリントの ID から `ndf progress wait --timeout 0` の結果（読めなければ None）、`account_ok` は
    子を起動するアカウントが使えるか（`stalled` のレーンを再起動するか。#3243）、`activity` はレーン名から conductor の
    活動（読まなければ None。#4036）。"""

    wrapper_name: Callable[[str], str]
    wrappers: Callable[[], Optional[Mapping[str, Mapping[str, Any]]]]
    sprint: Callable[[str], Optional[Mapping[str, Any]]]
    account_ok: Callable[[], bool] = lambda: False
    activity: Callable[[str], Optional[Activity]] = lambda _: None


def judge_lanes(lanes: Mapping[str, list[LaneRow]], probes: Probes, now: Optional[float] = None) -> dict[str, Liveness]:
    """全レーンの判定。材料（ラッパーの一覧・スプリントの待ち）は、判定に要るレーンがあるときだけ読む。
    活動は、活動を見ずに `alive` と決まるレーン（ラッパーが動いている）だけ読む。"""
    rows = {name: running_row(r) for name, r in lanes.items()}
    needs = [name for name, row in rows.items() if row is not None and not row.awaiting_next]
    table = probes.wrappers() if needs else {}
    out: dict[str, Liveness] = {}
    for name, row in rows.items():
        if row is None or name not in needs or row.sprint_id is None:
            out[name] = judge(row, SPRINT_UNKNOWN, None, False)
            continue
        sprint = sprint_wait(probes.sprint(row.sprint_id))
        wrapper = table.get(probes.wrapper_name(name)) if table is not None else None
        verdict = judge(row, sprint, wrapper, table is not None)
        out[name] = judge(row, sprint, wrapper, True, probes.activity(name), now) if verdict.kind == ALIVE else verdict
    return out
