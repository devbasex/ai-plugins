"""マシン全体のプランの本数の上限（#3097・#2999）: 同じマシンで動いているレーンのプランの本数を数え、上限を超える起動を待たせる。

計画ごとの上限（I2・I20。`ndf.progress.lane.rules`）に加えて数える。別のリポジトリの計画が動いていても、同じマシンで
同時に流すプランの本数の合計が上限を超えないようにする。

- 数える計画は、同じマシンのラッパーの登録簿（`ndf relay ls`。利用者単位で共通）に conductor の役で載る計画と、自分の計画。
  計画の宣言は、登録簿の行の作業ディレクトリのチェックアウトの進行表から読む（`read_lanes`）
- 動いているレーンは、実行中の行を持つ（承認ゲートや問いの答えを待つ行を含む）か、ラッパーが動いているレーンで、
  宣言の `plans` を数える。同じラッパーは 1 回だけ数え、起こそうとしているレーン自身のラッパーは数えない
- 進行表を読めない計画と、どの計画の宣言にも無い conductor のラッパーは、動いているラッパー 1 本を 1 本として数え、
  読めない計画を `unreadable` に載せる
- 数えてから起こすまでを、マシンの起動の排他（`<状態の置き場>/parallel/admit.lock`。#2999 の決定 4）の中で行う。
  起動の排他を `parallel-machine.admit-lock` の秒の間に取れなければ、その読み直しでは起こさずに待たせる
- ラッパーの一覧を読めなければ、ほかの計画を数えられないので、自分の計画の実行中の行だけで決める
- 合計が 0 なら上限を見ずに起こす（待ち続けて計画が終わらない状態を作らない。#2999 の受け入れ条件 7）
- 上限は計画の上限の既定と同じ宣言（`CAP_POLICY`）から読み、どのリポジトリでも同じ値になる。保存せず、判定のたびに数え直す

`count`・`admit`・`waiting` は純粋な関数で、DB もプロセスも読まない。
"""

from __future__ import annotations

import contextlib
import fcntl
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import sqlalchemy as sa

from ndf import contracts, identity
from ndf.adapters.git import GitError
from ndf.contracts import limits
from ndf.core import durable
from ndf.core import durable_locks as locks
from ndf.progress import command, model, store
from ndf.progress.lane import PlanView, launch, read_view
from ndf.progress.lane.types import LaneRow, RUNNING

# マシンの上限の宣言（計画の上限の既定と同じ。`ndf.progress.plan.LIMIT_POLICY`）と、起動の排他を待つ上限の宣言
CAP_POLICY = ("relay.max-named", "count")
LOCK_POLICY = "parallel-machine.admit-lock"
# 起動の排他のファイル（状態の置き場からの相対パス。#2999 の決定 4）と、取り直す間隔の秒
LOCK_PATH = Path("parallel") / "admit.lock"
LOCK_POLL_SECONDS = 0.2
# 待たせた理由: 上限に当たった・起動の排他を取れなかった・ラッパーの一覧を読めなかった
RULE, LOCK, OBSERVE = "machine-plans", "lock", "observe"
# 計画の進行表を読めないときに飛ばす例外
_UNREADABLE = (
    model.Refused,
    durable.StoreStopped,
    sa.exc.SQLAlchemyError,
    contracts.ContractError,
    GitError,
    subprocess.CalledProcessError,
    OSError,
)


@dataclass(frozen=True)
class PlanLanes:
    """1 つの計画のレーン。`key` は計画の所在（進行表の ID か、読めなければ `<作業ディレクトリ>:<計画>`）、
    `lanes` は（ラッパーの名前, 宣言のプランの本数, 実行中の行を持つか）、`readable` は進行表を読めたか。"""

    key: str
    lanes: tuple[tuple[str, int, bool], ...] = ()
    readable: bool = True


@dataclass(frozen=True)
class Count:
    """マシンの数え方の結果。`total` はプランの本数の合計、`lanes` は数えたラッパー、`unreadable` は読めない計画。"""

    total: int
    lanes: tuple[str, ...]
    unreadable: tuple[str, ...]


def entry_key(row: Mapping[str, Any]) -> str:
    """登録簿の行の計画の所在（読めない計画の照合に使う）。"""
    return f"{row['cwd']}:{row['plan']}"


def _conductor(row: Mapping[str, Any]) -> bool:
    return row.get("role") == launch.ROLE and bool(row.get("plan")) and bool(row.get("cwd"))


def count(plans: Sequence[PlanLanes], listed: Mapping[str, Mapping[str, Any]], skip: str) -> Count:
    """動いているレーンのプランの本数を数える。`listed` はラッパーの一覧（名前 → 行）、`skip` は起こそうとしている
    レーンのラッパー（数えない）。"""
    running = {name for name, row in listed.items() if row.get("running")}
    seen: set[str] = set()
    total = 0
    for p in plans:
        for name, n, live in p.lanes if p.readable else ():
            if name != skip and name not in seen and (live or name in running):
                seen.add(name)
                total += n
    declared = {name for p in plans for name, _, _ in p.lanes}
    unreadable = tuple(sorted({p.key for p in plans if not p.readable}))
    for name, row in listed.items():
        # 読めない計画のラッパーと、どの宣言にも無い conductor のラッパーは 1 本と数える
        if name in running and name != skip and name not in seen and name not in declared and _conductor(row):
            seen.add(name)
            total += 1
    return Count(total, tuple(sorted(seen)), unreadable)


def admit(total: int, plans: int, cap: int) -> bool:
    """起こしてよいか。動いているレーンが無ければ上限を見ない（受け入れ条件 7）。"""
    return total == 0 or total + plans <= cap


def waiting(reason: str, got: Optional[Count], plans: int, cap: int) -> dict[str, Any]:
    """起こさずに待たせた起動の結果（耐久ワークフローは失敗に数えない）。"""
    total = got.total if got is not None else None
    body = {
        "reason": reason,
        "machine_plans": total,
        "plans": plans,
        "max_plans": cap,
        "unreadable": list(got.unreadable) if got is not None else [],
    }
    detail = {
        RULE: f"マシンで動いているプランの本数 {total} とこのレーンの {plans} 本の合計が上限 {cap} を超えるので起動を待つ",
        LOCK: "マシンの起動の排他を取れないので、次の読み直しまで起動を待つ",
        OBSERVE: f"ラッパーの一覧を読めず、自分の計画で動いているプランの本数 {total} とこのレーンの {plans} 本の合計が上限 {cap} を超えるので起動を待つ",
    }[reason]
    return {"ok": False, "waiting": body, "detail": detail}


def cap() -> int:
    return int(limits.value(*CAP_POLICY))


# --- 読み取り（進行表・登録簿）と排他 --------------------------------------------------------------


def lanes_of(view: PlanView, rows: Mapping[str, list[LaneRow]]) -> PlanLanes:
    """読んだ計画のレーン（ラッパーの名前・宣言のプランの本数・実行中の行を持つか）。"""
    lanes = tuple(
        (launch.wrapper_name(str(view.repo), view.name, name), spec.plans, any(r.state == RUNNING for r in rows[name]))
        for name, spec in view.lanes.items()
    )
    return PlanLanes(view.table_id, lanes)


def discover(listed: Mapping[str, Mapping[str, Any]]) -> list[tuple[Path, str]]:
    """登録簿に conductor の役で載る計画の（作業ディレクトリ, 計画の名前）。同じ組は 1 つにする。"""
    found = {(str(row["cwd"]), str(row["plan"])) for row in listed.values() if _conductor(row)}
    return [(Path(cwd), plan) for cwd, plan in sorted(found)]


def read_lanes(cwd: Path, plan: str, skip: Sequence[str] = ()) -> Optional[PlanLanes]:
    """作業ディレクトリ `cwd` のチェックアウトの進行表から計画 `plan` のレーンを読む。計画の進行表の ID が `skip` に
    あれば None（呼び手が読んである）。読めなければ `readable` が偽の行。"""
    key = f"{cwd}:{plan}"
    try:
        root = command.store_root_of(cwd)
        url = store.default_url(root)
        path = store.sqlite_path(url)
        if path is not None and not path.exists():
            # 進行表の DB が無いチェックアウトの計画は読めない（DB を作らない）
            return PlanLanes(key, readable=False)
        with store.open_store(url, root) as st:
            pid = model.table_id("plan", command.repo_id(cwd, st.root), plan)
            if pid in skip:
                return None
            with store.transaction(st) as conn:
                return lanes_of(*read_view(conn, pid))
    except _UNREADABLE:
        # 読めない計画は、動いているラッパーを 1 本ずつ数えて unreadable に載せる
        return PlanLanes(key, readable=False)


def machine_count(own: PlanLanes, listed: Mapping[str, Mapping[str, Any]], skip: str) -> Count:
    """自分の計画（読んである）と、登録簿の計画の動いているレーンを数える。"""
    others = [read_lanes(cwd, plan, (own.key,)) for cwd, plan in discover(listed)]
    found = [own, *(p for p in others if p is not None)]
    seen: set[str] = set()
    plans = []
    for p in found:
        if p.key not in seen:
            seen.add(p.key)
            plans.append(p)
    return count(plans, listed, skip)


def lock_path() -> Path:
    return identity.load().state_dir / LOCK_PATH


class LockBusy(Exception):
    """マシンの起動の排他を上限の秒の間に取れない。"""


@contextlib.contextmanager
def admit_lock(seconds: float, clock: Callable[[], float] = time.monotonic) -> Iterator[None]:
    """マシンの起動の排他。`seconds` の間に取れなければ `LockBusy`。"""
    handle = durable.open_lock_file(lock_path())
    try:
        deadline = clock() + seconds
        while not locks.try_flock(handle, fcntl.LOCK_EX):
            if clock() >= deadline:
                raise LockBusy(f"マシンの起動の排他を {seconds:g} 秒の間に取れない: {lock_path()}")
            time.sleep(LOCK_POLL_SECONDS)
        yield
    finally:
        handle.close()


def admitted(
    own: PlanLanes,
    lane_wrapper: str,
    plans: int,
    listed: Callable[[], Optional[Mapping[str, Mapping[str, Any]]]],
    go: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """マシンの起動の排他の中で数え、上限に収まれば `go`（ラッパーを止めて起こす）を打つ。収まらなければ待たせる。"""
    limit = cap()
    try:
        with admit_lock(limits.seconds(LOCK_POLICY)):
            table = listed()
            # 一覧を読めなければ、ほかの計画を数えられないので自分の計画の実行中の行だけで決める（計画ごとの上限と同じ）
            got = machine_count(own, table, lane_wrapper) if table is not None else count([own], {}, lane_wrapper)
            if not admit(got.total, plans, limit):
                return waiting(RULE if table is not None else OBSERVE, got, plans, limit)
            return go()
    except LockBusy:
        return waiting(LOCK, None, plans, limit)
