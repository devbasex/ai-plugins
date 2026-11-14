"""レーンの生死の判定（#3097・N3）: 実行中の行を持つレーンが動いているか・正常に待っているか・止まったかを 1 つに決める。

DB もプロセスも読まない純粋な関数である。材料は 3 つで、読むのは呼び手（耐久ワークフローの読み直しと `ndf lanes status`）:

- レーンの進行表の行（`LaneRow`）: 実行中か・答えが載った後に next を待っているか
- スプリントの進行表の待ち（`ndf progress wait <ID> --timeout 0` の結果）: 承認ゲートの答えを待っている（`gate`）・
  工程を流す作業が動いている（`working`）・conductor の手を待っている（`idle`）・読めない（`unknown`）
- ラッパーの一覧の行（`ndf relay ls` の 1 行）: 動いているか・ラッパーの状態（`running`・`waiting`・`stalled`）と理由。
  一覧を読めなかった回は `listed` を偽にする

判定の規則（上から先に当たったもの）:

1. 実行中の行が無い → `idle`（判定しない）
2. 答えが載った後に next が打たれていない → `resuming`（耐久ワークフローが conductor を起こし直す。止まりにしない）
3. スプリントが承認ゲートや問いの答えを待っている → `waiting_answer`（正常な待ち。止まりとして知らせない）
4. ラッパーの一覧を読めなかった・スプリントの待ちを読めなかった → `unknown`（一覧に無いことを止まりと読まない）
5. ラッパーが動いていて状態が `stalled`（認証の失敗で `/goal` が外れた など） → `stalled`（ラッパーの理由を添える）
6. ラッパーが動いている → `alive`
7. スプリントの工程を流す作業が動いている → `working`（conductor が落ちても作業は続く。終わってから止まりになる）
8. ほか（ラッパーが一覧に無い・止まっていて、スプリントは conductor の手を待つ） → `ended`

止まりとして知らせるのは `stalled` と `ended` だけである（`STALL_KINDS`）。

`stalled`（認証の失敗で `/goal` が外れた）のレーンは、子を起動するアカウントが使える（`Probes.account_ok`）なら、
耐久ワークフローが `/goal` を入れた入力で起こし直す（#3243）。使えなければ止まりとして知らせる。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Optional

from ndf.contracts import causes, result
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
# 止まりとして知らせる判定
STALL_KINDS = (STALLED, ENDED)
# スプリントの進行表の待ち（`sprint_wait` が `ndf progress wait` の結果から決める）
SPRINT_GATE, SPRINT_WORKING, SPRINT_IDLE, SPRINT_UNKNOWN = "gate", "working", "idle", "unknown"
# ラッパーの状態のうち、止まりと読むもの（`ndf relay ls` の `state`）
WRAPPER_STALLED = "stalled"
# `ndf progress wait --timeout 0` が「作業がまだ動いている」で返す止まった理由の種類
WAIT_TIMEOUT = "wait_timeout"


@dataclass(frozen=True)
class Liveness:
    """1 本のレーンの判定。`kind` は判定の種類、`reason` は人が読む理由、`row` と `sprint_id` は判定した行。"""

    kind: str
    reason: str
    row: Optional[str] = None
    sprint_id: Optional[str] = None

    @property
    def stall(self) -> bool:
        return self.kind in STALL_KINDS

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "reason": self.reason, "row": self.row, "sprint_id": self.sprint_id, "stall": self.stall}


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


def judge(row: Optional[LaneRow], sprint: str, wrapper: Optional[Mapping[str, Any]], listed: bool) -> Liveness:
    """1 本のレーンの生死を決める。`row` はレーンの実行中の行（無ければ None）、`sprint` は `sprint_wait` の値、
    `wrapper` はラッパーの一覧の行（一覧に無ければ None）、`listed` は一覧を読めたか。"""
    if row is None:
        return Liveness(IDLE, "実行中の行が無い")
    key, sid = row.key, row.sprint_id
    if row.awaiting_next:
        return Liveness(RESUMING, "答えが載った後の next を conductor が打つのを待つ（耐久ワークフローが起こし直す）", key, sid)
    if sprint == SPRINT_GATE:
        return Liveness(WAITING_ANSWER, "スプリントが承認ゲートか問いの答えを待っている", key, sid)
    if not listed or sprint == SPRINT_UNKNOWN:
        return Liveness(UNKNOWN, "ラッパーの一覧かスプリントの待ちを読めない", key, sid)
    running = bool(wrapper and wrapper.get("running"))
    if running and wrapper is not None and wrapper.get("state") == WRAPPER_STALLED:
        return Liveness(STALLED, f"conductor のラッパーが止まっている: {wrapper.get('reason') or '理由の記録が無い'}", key, sid)
    if running:
        return Liveness(ALIVE, "conductor のラッパーが動いている", key, sid)
    if sprint == SPRINT_WORKING:
        return Liveness(WORKING, "conductor のラッパーは無いが、スプリントの工程を流す作業が動いている", key, sid)
    return Liveness(ENDED, "conductor のラッパーが動いておらず、スプリントは conductor の手を待っている", key, sid)


def notice_key(lane: str, verdict: Liveness) -> str:
    """止まりの知らせの鍵。同じレーン・同じ行・同じ判定では 1 回だけ知らせ、行か判定が変われば新しい鍵になる。"""
    return f"stall:{lane}/{verdict.row}:{verdict.kind}"


def running_row(rows: list[LaneRow]) -> Optional[LaneRow]:
    """レーンの実行中の行（スプリントの ID が付いたもの）。無ければ None。"""
    return next((r for r in rows if r.active), None)


@dataclass(frozen=True)
class Probes:
    """判定の材料を読む口。`wrapper_name` はレーン名からラッパーの名前、`wrappers` はラッパーの一覧（読めなければ None）、
    `sprint` はスプリントの ID から `ndf progress wait --timeout 0` の結果（読めなければ None）、`account_ok` は
    子を起動するアカウントが使えるか（`stalled` のレーンを起こし直すか。#3243）。"""

    wrapper_name: Callable[[str], str]
    wrappers: Callable[[], Optional[Mapping[str, Mapping[str, Any]]]]
    sprint: Callable[[str], Optional[Mapping[str, Any]]]
    account_ok: Callable[[], bool] = lambda: False


def judge_lanes(lanes: Mapping[str, list[LaneRow]], probes: Probes) -> dict[str, Liveness]:
    """全レーンの判定。材料（ラッパーの一覧・スプリントの待ち）は、判定に要るレーンがあるときだけ読む。"""
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
        out[name] = judge(row, sprint, wrapper, table is not None)
    return out
