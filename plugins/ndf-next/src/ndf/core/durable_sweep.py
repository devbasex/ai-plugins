"""鍵ごとのファイルの検査の目印と退避、置き場（SQLite）と URL（PostgreSQL）の片付け（決定 82・83・I12・I13）。DBOS は import しない。

DBOS の記録を読む口と消す口（`Records`）は、DBOS を読める殻（`ndf.core.durable`）が渡す。殻は `sweep`・`sweep_url`・
`retire`・`SweepResult` を再び公開し、呼び手は殻から使う。
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ndf.core import durable_locks as locks
from ndf.core.durable_ledger import _forget_run, _ledger
from ndf.core.durable_locks import maint_lock, sa

# 殻（durable）から分けた後も、片付けのログは殻の logger に出す
log = logging.getLogger("ndf.core.durable")

# retired/・broken/ と URL の終わった実行を消すまでの日数（決定 83）
KEEP_DAYS = 14
# quick_check と片付けの走査の間隔（決定 82・83）
CHECK_INTERVAL = timedelta(hours=24)

DBOS_DIR = "dbos"
RETIRED_DIR = "retired"
BROKEN_DIR = "broken"
SWEPT_MARK = ".swept"
# SQLite の本体に付いて動くファイルの接尾辞。退避では本体とそろえて移す
SIDE_SUFFIXES = ("-wal", "-shm", "-journal")
# 鍵ごとのファイルの本体・検査の目印・鍵のロックの接尾辞
DB_SUFFIX = ".sqlite"
CHECKED_SUFFIX = ".checked"
LOCK_SUFFIX = ".lock"
FINISHED = frozenset({"SUCCESS", "ERROR", "CANCELLED"})
# 表の移行の前に取る鍵のファイルの控えの置き場（dbos/ の下）
BACKUP_DIR = "backup"


@dataclass(frozen=True)
class Records:
    """DBOS の記録を読む口（URL と鍵の名前の並び。None ならすべて）と、ID を並べて消す口。殻が渡す。"""

    read: Callable[[str, list[str] | None], list[Any]]
    delete: Callable[[str, list[str]], None]


@dataclass
class SweepResult:
    """片付けの結果。`skipped` は走査しなかった（ほかが走査中・目印が新しい）こと、`busy` は開いている鍵の数。"""

    retired: list[Path] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: bool = False
    busy: int = 0
    warnings: list[str] = field(default_factory=list)


def _dbos_dir(root: Path) -> Path:
    return root / DBOS_DIR


def _free_name(base: Path, now: datetime) -> Path:
    """`base` が無ければそのまま、あれば時刻を足し、それもあれば番号を足した名前。消さない。"""
    if not base.exists():
        return base
    stamped = base.with_name(base.name + now.strftime("T%H%M%S"))
    candidate, n = stamped, 1
    while candidate.exists():
        candidate = stamped.with_name(f"{stamped.name}-{n}")
        n += 1
    return candidate


# --- 鍵ごとのファイルの検査と退避（決定 82・83） -----------------------------------------


def is_fresh(mark: Path, now: datetime) -> bool:
    try:
        return now.timestamp() - mark.stat().st_mtime < CHECK_INTERVAL.total_seconds()
    except FileNotFoundError:  # noqa: NDF401 ファイル: 目印が無ければ古いとして扱う
        return False


def touch_mark(mark: Path, now: datetime) -> None:
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.touch()
    os.utime(mark, (now.timestamp(), now.timestamp()))


def _move_out(body: Path, dest_dir: Path, target_name: str, now: datetime) -> Path:
    """鍵のファイルの本体と `-wal`・`-shm`・`-journal` をそろえて移し、目印 `.checked` と最後に `.lock` を消す。

    移した時刻を更新時刻にし、`KEEP_DAYS` を移した時から数える。時刻は移す前に付ける。移した後に付けると、
    その間は元の古い時刻で退避先に見え、別の鍵の `sweep` が退避したばかりのファイルを消せる。
    `.lock` を消すのは呼び手が `run_lock` を持つときだけ。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = _free_name(dest_dir / target_name, now)
    stamp = (now.timestamp(), now.timestamp())
    for suffix in ("", *SIDE_SUFFIXES):
        src = body.with_name(body.name + suffix)
        if src.exists():
            os.utime(src, stamp)
            src.rename(target.with_name(target.name + suffix))
    _drop_marks(body)
    return target


def _drop_marks(body: Path) -> None:
    """鍵のファイルの目印 `.checked` と、最後に `.lock` を消す。"""
    body.with_suffix(CHECKED_SUFFIX).unlink(missing_ok=True)
    body.with_suffix(LOCK_SUFFIX).unlink(missing_ok=True)


def retire_name(root: Path, name: str, records: Records, now: datetime) -> Path | None:
    """鍵の名前 `name` のファイルの耐久ワークフローがすべて終わっていれば retired/ へ移し、移した先を返す。待ちがあれば何もしない。"""
    body = _dbos_dir(root) / f"{name}{DB_SUFFIX}"
    if not body.exists():
        _drop_marks(body)
        return None
    if any(r.status not in FINISHED for r in records.read(locks.sqlite_url(body), None)):
        return None
    conn = sqlite3.connect(body, timeout=30)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return _move_out(body, _dbos_dir(root) / RETIRED_DIR, body.name, now)


@contextmanager
def _sweep_guard(root: Path, now: datetime) -> Iterator[bool]:
    """`maint_lock` を待たずに取り、目印 `dbos/.swept` を読み直して古ければ真を渡し、抜けるときに目印を更新する。"""
    with maint_lock(root, wait=False) as held:
        mark = _dbos_dir(root) / SWEPT_MARK
        if held is None or is_fresh(mark, now):
            yield False
            return
        yield True
        touch_mark(mark, now)


def _guarded_sweep(root: Path, now: datetime | None, body: Callable[[datetime, SweepResult], None], message: str) -> SweepResult:
    """`_sweep_guard` が走査を許したときだけ `body` を流し、読めなかったものを `message` でログに残す。"""
    now = now or datetime.now(UTC)
    out = SweepResult()
    with _sweep_guard(root, now) as go:
        if not go:
            out.skipped = True
            return out
        body(now, out)
    if out.warnings:
        log.warning(message, extra={"count": len(out.warnings), "sample": out.warnings[:10]})
    return out


def sweep(root: Path, records: Records, now: datetime | None = None) -> SweepResult:
    """終わった鍵のファイルを retired/ へ移し、retired/・broken/ の `KEEP_DAYS` より古いファイルを消す（決定 83・A5）。

    24 時間に 1 回だけ走査する。開いているプロセスがいる鍵（`run_lock` を待たずに取れない鍵）は触らない。
    """

    def body(now: datetime, out: SweepResult) -> None:
        dbos_dir = _dbos_dir(root)
        if dbos_dir.is_dir():
            _sweep_live(root, records, now, out)
            _sweep_kept(dbos_dir, now, out)

    return _guarded_sweep(root, now, body, "片付けで読めないファイルがあった")


def _sweep_live(root: Path, records: Records, now: datetime, out: SweepResult) -> None:
    _retire_finished(root, records, now, out)
    _drop_orphans(root)


def _retire_finished(root: Path, records: Records, now: datetime, out: SweepResult) -> None:
    """開いているプロセスのいない鍵のファイルのうち、耐久ワークフローがすべて終わったものを retired/ へ移す。"""
    for body in sorted(_dbos_dir(root).glob(f"*{DB_SUFFIX}")):
        with locks.file_run_lock(_dbos_dir(root) / f"{body.stem}{LOCK_SUFFIX}", wait=False) as held:
            if held is None:
                out.busy += 1
                continue
            try:
                moved = retire_name(root, body.stem, records, now)
            except Exception as exc:  # noqa: BLE001 ファイル: 1 つのファイルを読めなくても片付けを止めず、warnings に載せる
                out.warnings.append(f"{body}: {exc}")
                continue
            if moved is not None:
                out.retired.append(moved)


def _drop_orphans(root: Path) -> None:
    """本体の無い `.checked` と、本体が無くだれも持たない `.lock` を消す。"""
    dbos_dir = _dbos_dir(root)
    for mark in dbos_dir.glob(f"*{CHECKED_SUFFIX}"):
        if not mark.with_suffix(DB_SUFFIX).exists():
            mark.unlink(missing_ok=True)
    for lock in dbos_dir.glob(f"*{LOCK_SUFFIX}"):
        if lock.with_suffix(DB_SUFFIX).exists():
            continue
        with locks.file_run_lock(_dbos_dir(root) / f"{lock.stem}{LOCK_SUFFIX}", wait=False) as held:
            if held is not None:
                lock.unlink(missing_ok=True)


def _sweep_kept(dbos_dir: Path, now: datetime, out: SweepResult) -> None:
    cutoff = (now - timedelta(days=KEEP_DAYS)).timestamp()
    for kept in (dbos_dir / RETIRED_DIR, dbos_dir / BROKEN_DIR, dbos_dir / BACKUP_DIR):
        if not kept.is_dir():
            continue
        for path in sorted(kept.iterdir()):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                out.deleted.append(str(path))


# --- URL（PostgreSQL）の台帳と片付け（決定 83・I12・I13） ---------------------------------


def _expired(records: list[Any], started_at: datetime, cutoff: datetime) -> bool:
    """鍵の実行がすべて終わり、どれも `cutoff` より前に終わったか。実行が無ければ台帳の時刻で決める。"""
    if not records:
        return started_at < cutoff
    return all(r.status in FINISHED and r.updated_at is not None and r.updated_at < cutoff for r in records)


def sweep_url(root: Path, database_url: str, records: Records, now: datetime | None = None) -> SweepResult:
    """台帳の鍵のうち、実行（子を含む）がすべて終わって `KEEP_DAYS` を過ぎた鍵の実行と台帳の行を消す（A6・A12）。

    目印と排他は `sweep` と同じ。鍵ごとに DB 側の鍵のロックを待たずに試し、取れない鍵は触らない（I13）。
    消すときは集めた ID をすべて並べて `delete_workflows` を呼び、`delete_children` には頼らない（I8）。
    """

    def body(now: datetime, out: SweepResult) -> None:
        try:
            rows = _ledger(database_url)
        except sa.exc.SQLAlchemyError as exc:
            out.warnings.append(f"台帳を読めない: {exc}")
            rows = []
        cutoff = now - timedelta(days=KEEP_DAYS)
        for row in rows:
            try:
                _sweep_url_run(database_url, row.run_name, row.started_at, cutoff, records, out)
            except Exception as exc:  # noqa: BLE001 ネットワーク: 1 つの鍵を片付けられなくても止めず、warnings に載せる
                out.warnings.append(f"{row.run_name}: {exc}")

    return _guarded_sweep(root, now, body, "URL の片付けで読めない鍵があった")


def _sweep_url_run(database_url: str, name: str, started_at: datetime, cutoff: datetime, records: Records, out: SweepResult) -> None:
    """鍵 1 つを、DB 側の鍵のロックを待たずに取れたときだけ、すべて終わって日数が過ぎていれば消す。"""
    with locks.pg_run_lock(database_url, name, wait=False) as held:
        if held is None:
            out.busy += 1
            return
        found = records.read(database_url, [name])
        if not _expired(found, started_at, cutoff):
            return
        ids = [r.workflow_id for r in found]
        if ids:
            records.delete(database_url, ids)
        _forget_run(database_url, name)
        out.deleted.extend(ids)
