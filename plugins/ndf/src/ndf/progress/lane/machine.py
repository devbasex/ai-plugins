"""マシン全体の稼働中のレーンの数の上限（#3097・#2999・#3989）: 同じマシンで稼働中のレーンと名前つきのラッパーを 1 本ずつ数え、上限を超える起動を待たせる。

同時実行の制御は 2 つの層である。セッションの数（このモジュール。稼働中のレーンとラッパーの数）と、重い処理（テストのスロット
`ndf.testing.slots`）。計画とレーンは同時実行の値を持たない（#3989 の決定 1）。

- 数えるのは 2 つの和集合で、同じラッパーは 1 回、起動しようとしているレーン自身のラッパーは数えない（#3989 の決定 3）
  1. マシンの計画の稼働中のレーン（実行中の行を持つ。承認ゲートや質問の回答を待つ行を含む）のラッパー
  2. ラッパーの一覧（`ndf relay ls`）で動いているラッパー（役に依らない。リレーハブが数えるラッパーを含む）
- マシンの計画は、自分の計画・一覧に conductor の役で載る計画・マシンの計画の索引（`<状態の保存先>/parallel/plans/`）の計画の和で、
  1 計画 1 回読む。索引は起動の許可が通ったときに排他の中で書き、耐久ワークフローが終わるときに消す。ラッパーがすべて止まって
  登録簿から消えた計画も、実行中の行を持つ間は索引から見つかる
- ステートマシンを読めない計画は `unreadable` に載せる（そのラッパーは一覧で動いていれば 2 で数える）
- 数えてから起動するまでを、マシンの起動の排他（`<状態の保存先>/parallel/admit.lock`）の中で行う。起動の排他を
  `parallel-machine.admit-lock` の秒の間に取れなければ、その読み直しでは起動せずに待たせる
- ラッパーの一覧を読めなければ、ほかの計画を数えられないので、自分の計画の実行中の行だけで決める
- 上限は判定のたびに `ndf.contracts.named_cap.read`（マシンの設定 → `NDF_RELAY_MAX_NAMED` → 上限の設定）で読む。リレーハブと同じ値である

`count`・`admit`・`waiting` は純粋な関数で、DB もプロセスも読まない。
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Optional

import sqlalchemy as sa

from ndf import contracts, identity
from ndf.adapters.git import GitError
from ndf.contracts import limits
from ndf.contracts import named_cap
from ndf.core import durable
from ndf.core import durable_locks as locks
from ndf.progress import command, model, store
from ndf.progress.lane import PlanView, launch, liveness, read_view
from ndf.progress.lane.types import LaneRow, RUNNING

# マシンの上限の設定（`ndf.contracts.named_cap` と同じ値）と、起動の排他を待つ上限の設定
CAP_POLICY = named_cap.POLICY
LOCK_POLICY = "parallel-machine.admit-lock"
# 起動の排他のファイル（状態の保存先からの相対パス。#2999 の決定 4）と、取り直す間隔の秒
LOCK_PATH = Path("parallel") / "admit.lock"
LOCK_POLL_SECONDS = 0.2
# マシンの計画の索引の保存先（状態の保存先からの相対パス。#3989 の決定 3）
INDEX_PATH = Path("parallel") / "plans"
INDEX_VERSION = 1
# 待たせた理由: 上限に当たった・起動の排他を取れなかった・ラッパーの一覧を読めなかった
RULE, LOCK, OBSERVE = "machine-lanes", "lock", "observe"
# 計画のステートマシンを読めないときに飛ばす例外
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
    """1 つの計画のレーン。`key` は計画の所在（ステートマシンの ID か、読めなければ `<作業ディレクトリ>:<計画>`）、
    `lanes` は（ラッパーの名前, 実行中の行を持つか）、`readable` はステートマシンを読めたか。"""

    key: str
    lanes: tuple[tuple[str, bool], ...] = ()
    readable: bool = True


@dataclass(frozen=True)
class Count:
    """マシンの数え方の結果。`total` は数えたラッパーの数、`lanes` は数えたラッパー、`unreadable` は読めない計画。"""

    total: int
    lanes: tuple[str, ...]
    unreadable: tuple[str, ...]


class PlanPlace(NamedTuple):
    """マシンの計画の索引の 1 件（計画のステートマシンの ID・作業ディレクトリ・計画の名前）。"""

    plan_id: str
    cwd: str
    plan: str


def entry_key(row: Mapping[str, Any]) -> str:
    """登録簿の行の計画の所在（読めない計画の検証に使う）。"""
    return f"{row['cwd']}:{row['plan']}"


def _conductor(row: Mapping[str, Any]) -> bool:
    return row.get("role") == launch.ROLE and bool(row.get("plan")) and bool(row.get("cwd"))


def count(plans: Sequence[PlanLanes], listed: Mapping[str, Mapping[str, Any]], skip: str) -> Count:
    """稼働中のレーンと動いているラッパーを 1 本ずつ数える。`listed` はラッパーの一覧（名前 → 行）、`skip` は起動しようとしている
    レーンのラッパー（数えない。空なら今動いている分のすべて）。"""
    live = {name for p in plans if p.readable for name, running_row in p.lanes if running_row}
    running = {name for name, row in listed.items() if liveness.wrapper_running(row)}
    seen = (live | running) - {skip}
    unreadable = tuple(sorted({p.key for p in plans if not p.readable}))
    return Count(len(seen), tuple(sorted(seen)), unreadable)


def admit(total: int, cap: int) -> bool:
    """起動してよいか（数えた本数にこの 1 本を足して上限以内）。"""
    return total + 1 <= cap


def waiting(reason: str, got: Optional[Count], cap: Optional[int]) -> dict[str, Any]:
    """起動せずに待たせた起動の結果（耐久ワークフローは失敗に数えない）。排他の待ちは数えていないので数と上限が null。"""
    total = got.total if got is not None else None
    body = {"reason": reason, "machine_lanes": total, "cap": cap, "unreadable": list(got.unreadable) if got is not None else []}
    detail = {
        RULE: f"マシンで稼働中のレーンとラッパー {total} 本にこのレーンを足すと上限 {cap} を超えるので起動を待つ",
        LOCK: "マシンの起動の排他を取れないので、次の読み直しまで起動を待つ",
        OBSERVE: f"ラッパーの一覧を読めず、自分の計画の稼働中のレーン {total} 本にこのレーンを足すと上限 {cap} を超えるので起動を待つ",
    }[reason]
    return {"ok": False, "waiting": body, "detail": detail}


def cap(environ: Optional[Mapping[str, str]] = None, state_dir: Optional[Path] = None) -> int:
    """マシン全体の稼働中の数の上限（`named_cap.read`。判定のたびに読み直す）。壊れた値は `NamedCapError`。"""
    where = identity.load().state_dir if state_dir is None else state_dir
    return named_cap.read(os.environ if environ is None else environ, where).value


# --- 読み取り（ステートマシン・登録簿）と排他 --------------------------------------------------------------


def lanes_of(view: PlanView, rows: Mapping[str, list[LaneRow]]) -> PlanLanes:
    """読んだ計画のレーン（ラッパーの名前・実行中の行を持つか）。"""
    lanes = tuple(
        (launch.wrapper_name(str(view.repo), view.name, name), any(r.state == RUNNING for r in rows[name])) for name in view.lanes
    )
    return PlanLanes(view.machine_id, lanes)


def discover(listed: Mapping[str, Mapping[str, Any]]) -> list[tuple[Path, str]]:
    """登録簿に conductor の役で載る計画の（作業ディレクトリ, 計画の名前）。同じ組は 1 つにする。"""
    found = {(str(row["cwd"]), str(row["plan"])) for row in listed.values() if _conductor(row)}
    return [(Path(cwd), plan) for cwd, plan in sorted(found)]


def read_lanes(cwd: Path, plan: str, skip: Sequence[str] = ()) -> Optional[PlanLanes]:
    """作業ディレクトリ `cwd` のチェックアウトのステートマシンから計画 `plan` のレーンを読む。計画のステートマシンの ID が `skip` に
    あれば None（呼び手が読んである）。読めなければ `readable` が偽の行。"""
    key = f"{cwd}:{plan}"
    try:
        root = command.store_root_of(cwd)
        url = store.default_url(root)
        path = store.sqlite_path(url)
        if path is not None and not path.exists():
            # ステートマシンの DB が無いチェックアウトの計画は読めない（DB を作らない）
            return PlanLanes(key, readable=False)
        with store.open_store(url, root) as st:
            pid = model.machine_id("plan", command.repo_id(cwd, st.root), plan)
            if pid in skip:
                return None
            with store.transaction(st) as conn:
                return lanes_of(*read_view(conn, pid))
    except _UNREADABLE:
        # 読めない計画は、動いているラッパーを 1 本ずつ数えて unreadable に載せる
        return PlanLanes(key, readable=False)


def _index_dir() -> Path:
    return identity.load().state_dir / INDEX_PATH


def index_plan(where: PlanPlace) -> None:
    """マシンの計画の索引に計画の所在を書く（一時ファイルからの改名。同じ中身なら書かない）。書けなければ OSError。"""
    path = _index_dir() / f"{where.plan_id.replace('/', '_').replace(':', '_')}.json"
    body = {"version": INDEX_VERSION, "plan_id": where.plan_id, "cwd": where.cwd, "plan": where.plan}
    if _read_index(path) == body:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(body, fh, ensure_ascii=False)
    os.replace(tmp, path)


def unindex_plan(plan_id: str) -> None:
    """マシンの計画の索引から計画を消す（無くてもよい）。"""
    (_index_dir() / f"{plan_id.replace('/', '_').replace(':', '_')}.json").unlink(missing_ok=True)


def indexed() -> list[PlanPlace]:
    """マシンの計画の索引に載る計画の所在。読めないファイルは飛ばす。"""
    root = _index_dir()
    found = [_read_index(path) for path in sorted(root.glob("*.json"))] if root.is_dir() else []
    return [PlanPlace(str(d["plan_id"]), str(d["cwd"]), str(d["plan"])) for d in found if d is not None]


def _read_index(path: Path) -> Optional[dict[str, Any]]:
    """索引のファイル 1 本。無い・読めない・形が違えば None（異常終了の残りは数えない・書き直す）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 無い・壊れた索引は読めないとして呼び手が飛ばす
        return None
    ok = isinstance(data, dict) and all(isinstance(data.get(k), str) for k in ("plan_id", "cwd", "plan"))
    return data if ok else None


def machine_plans(own: PlanLanes, listed: Mapping[str, Mapping[str, Any]], places: Optional[Sequence[PlanPlace]] = None) -> list[PlanLanes]:
    """自分の計画（読んである）と、登録簿の conductor の行の計画と、索引の計画のレーン。1 計画 1 回読み、所在で重複を除く。

    レーンに依らないので、レーンごとに数える呼び手はこれを 1 回だけ読んで `count` へ渡す。`places` を省けば索引を読む。
    """
    found = {(str(cwd), plan) for cwd, plan in discover(listed)}
    found |= {(p.cwd, p.plan) for p in (indexed() if places is None else places) if p.plan_id != own.key}
    others = [read_lanes(Path(cwd), plan, (own.key,)) for cwd, plan in sorted(found)]
    uniq: dict[str, PlanLanes] = {}
    for p in (own, *(p for p in others if p is not None)):
        uniq.setdefault(p.key, p)
    return list(uniq.values())


def machine_count(own: PlanLanes, listed: Mapping[str, Mapping[str, Any]], skip: str) -> Count:
    """自分の計画（読んである）と、マシンの計画の稼働中のレーンと、動いているラッパーを数える。"""
    return count(machine_plans(own, listed), listed, skip)


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


def lock_free() -> bool:
    """マシンの起動の排他を待たずに 1 回だけ取り、取れたらすぐ放して真を返す（`ndf lanes status` が待ちの理由を読む）。"""
    try:
        handle = durable.open_lock_file(lock_path())
    except OSError:  # noqa: NDF401 ファイル: 開けない排他は取れないとして待ちの理由を lock にする
        return False
    try:
        return locks.try_flock(handle, fcntl.LOCK_EX)
    finally:
        handle.close()


def admitted(
    own: PlanLanes,
    lane_wrapper: str,
    listed: Callable[[], Optional[Mapping[str, Mapping[str, Any]]]],
    go: Callable[[], dict[str, Any]],
    where: PlanPlace,
) -> dict[str, Any]:
    """マシンの起動の排他の中で上限を読んで数え、収まれば計画を索引に書いてから `go`（ラッパーを置き換えて起動する）を打つ。
    収まらなければ待たせる。上限の値が壊れていれば打ち直しても同じなので `final` の失敗にする。"""
    try:
        with admit_lock(limits.seconds(LOCK_POLICY)):
            try:
                limit = cap()
            except named_cap.NamedCapError as exc:
                return {"ok": False, "final": True, "detail": str(exc)}
            table = listed()
            # 一覧を読めなければ、ほかの計画を数えられないので自分の計画の実行中の行だけで決める
            got = machine_count(own, table, lane_wrapper) if table is not None else count([own], {}, lane_wrapper)
            if not admit(got.total, limit):
                return waiting(RULE if table is not None else OBSERVE, got, limit)
            try:
                index_plan(where)
            except OSError as exc:
                return {"ok": False, "detail": f"マシンの計画の索引を書けない: {exc}"}
            return go()
    except LockBusy:
        return waiting(LOCK, None, None)
