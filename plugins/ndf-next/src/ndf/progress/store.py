"""進行表の保存（SQLite と PostgreSQL）と、進行表の DB の検査・控え・戻し（#3344 の決定 81・82）。

- 接続先は URL で決める。`NDF_PROGRESS_DB` があればその値、無ければチェックアウトの置き場の
  `sqlite:///<置き場>/progress.sqlite`。ローカルは SQLite、Factory は PostgreSQL の URL を渡す
- 進行表の DB は DBOS の表を持たない。DBOS の記録は実行の鍵ごとに分かれる（`ndf.core.durable`）
- SQLite: 実ファイルへ接続する前に `<実ファイル>.lock` を共有で取り、全接続を閉じるまで持つ（`db_lock`）。
  WAL と `busy_timeout` 30 秒で開き、書くトランザクションは `BEGIN IMMEDIATE` で始める。目印 `progress.checked` が
  24 時間より古いか無ければ、`maint_lock` を待って取り、目印を読み直してから `quick_check` と控えを行う
- PostgreSQL: `PRAGMA`・`quick_check`・控えを行わない。状態を変える操作は `pg_try_advisory_lock` で 1 つに限る
- 表は 4 つ（進行表・定義の改訂・進行の事象・表の版）。事象と定義の改訂は追記だけで、更新も削除もしない

ロックを取る順序は `state.lock` → `<実ファイル>.lock` → `maint.lock` の 1 方向にする（`ndf.core.durable` の順序に従う）。
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Optional

import sqlalchemy as sa
from sqlalchemy import event as sa_event

from ndf.core import durable
from ndf.progress import model

log = logging.getLogger(__name__)

# 置き場の中の既定の DB のファイル名。ロックのファイル名（durable.PROGRESS_LOCK）はこれに .lock を足したもの
DB_NAME = "progress.sqlite"
# 進行表の DB の検査の目印（置き場の中）
CHECKED_MARK = "progress.checked"
BACKUP_DIR = "backup"
BACKUP_PREFIX = "progress-"
# 残す控えの本数（8 本目を取ると最も古い 1 本を消す）
KEEP_BACKUPS = 7
BUSY_TIMEOUT_SECONDS = 30
# 表の版（ndf_progress_meta の schema_version）
SCHEMA_VERSION = 1
SCHEMA_VERSION_KEY = "schema_version"
# PostgreSQL の進行表の操作の排他（pg_try_advisory_lock の 2 引数の形）の第 1 引数。値は "NDFP" の 4 バイト。
# DBOS の記録の鍵のロック（durable.RUN_LOCK_SPACE）と値の空間を分ける
PG_LOCK_SPACE = 0x4E444650
# 表を作るときに PostgreSQL のプロセスどうしを直列にする第 2 引数
PG_SCHEMA_KEY = 0
# 戻すときの一時名の接尾辞と、退避の名前の印
RESTORING_SUFFIX = ".restoring"
BROKEN_MARK = ".broken-"

META = sa.MetaData()
TABLES = sa.Table(
    "ndf_progress_tables",
    META,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("level", sa.Text, nullable=False),
    sa.Column("repo", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("parent_id", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)
DEFINITIONS = sa.Table(
    "ndf_progress_definitions",
    META,
    sa.Column("table_id", sa.Text, primary_key=True),
    sa.Column("rev", sa.Integer, primary_key=True),
    sa.Column("definition", sa.JSON, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)
EVENTS = sa.Table(
    "ndf_progress_events",
    META,
    sa.Column("table_id", sa.Text, primary_key=True),
    sa.Column("seq", sa.Integer, primary_key=True),
    sa.Column("rev", sa.Integer, nullable=True),
    sa.Column("row_key", sa.Text, nullable=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=True),
    sa.Column("actor", sa.Text, nullable=False),
    sa.Column("reason", sa.Text, nullable=True),
    sa.Column("payload", sa.JSON, nullable=True),
    sa.Column("dedupe_key", sa.Text, nullable=True, unique=True),
    sa.Column("at", sa.DateTime(timezone=True), nullable=False),
)
VERSIONS = sa.Table(
    "ndf_progress_meta",
    META,
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("value", sa.Integer, nullable=False),
)


class StoreBusy(durable.StoreStopped):
    """SQLite の `database is locked` が `busy_timeout` を超えた。"""

    kind = "store_busy"


class RestoreUnsupported(durable.StoreStopped):
    """PostgreSQL の URL で戻しを求めた。控えと戻しは DB の運用で行う。"""

    kind = "restore_unsupported"


class Conflict(Exception):
    """事象の番号がぶつかった（同時の追記。I2）。読み直して判定し直す。"""


class BackupFailed(Exception):
    """控えを書けなかった（容量不足など）。書きかけの控えは消してある。"""


# --- 接続先とロック ------------------------------------------------------------------------


def sqlite_path(url: str) -> Optional[Path]:
    """SQLite の URL なら `os.path.realpath` で解いた実ファイルのパス、PostgreSQL の URL なら None。"""
    if not url.startswith(durable.SQLITE_PREFIX):
        return None
    return Path(os.path.realpath(url[len(durable.SQLITE_PREFIX) :]))


def default_url(root: Path) -> str:
    """`NDF_PROGRESS_DB` があればその値、無ければ置き場の既定の SQLite の URL。"""
    given = os.environ.get("NDF_PROGRESS_DB")
    return given if given else f"{durable.SQLITE_PREFIX}{root / DB_NAME}"


def _sa_url(url: str) -> str:
    """SQLAlchemy が psycopg 3 で開く形の URL にする。"""
    for scheme in ("postgresql://", "postgres://"):
        if url.startswith(scheme):
            return "postgresql+psycopg://" + url[len(scheme) :]
    return url


def _open_lock(path: Path) -> IO[str]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return open(path, "a")  # noqa: SIM115 - ロックを持つ間は開いたまま置く
    except OSError as exc:
        raise durable.StoreConfigBroken(f"ロックのファイルを作れない: {path}: {exc}", (path,)) from exc


def _flock(handle: IO[str], exclusive: bool, path: Path) -> None:
    if not exclusive:
        fcntl.flock(handle, fcntl.LOCK_SH)
        return
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise durable.StoreInUse(f"進行表の DB をほかのプロセスが開いている: {path}", (path,)) from exc


@contextmanager
def db_lock(path: Path, exclusive: bool) -> Iterator[Path]:
    """実ファイルの隣の `<実ファイル>.lock` を取る。共有は待って取り、排他は待たずに試す（取れなければ `StoreInUse`）。

    実ファイルが置き場の中でも外でも取る。どのチェックアウトから・どの URL の書き方で開いても同じパスに解ける（I11）。
    """
    lock = Path(f"{path}{durable.LOCK_SUFFIX}")
    handle = _open_lock(lock)
    try:
        _flock(handle, exclusive, lock)
        yield lock
    finally:
        handle.close()


def _fresh(mark: Path, now: datetime) -> bool:
    try:
        return now.timestamp() - mark.stat().st_mtime < durable.CHECK_INTERVAL.total_seconds()
    except FileNotFoundError:  # noqa: NDF401 ファイル: 目印が無ければ古いとして扱う
        return False


def _touch(mark: Path, now: datetime) -> None:
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.touch()
    os.utime(mark, (now.timestamp(), now.timestamp()))


# --- 開く --------------------------------------------------------------------------------


@dataclass
class Store:
    """開いた進行表の DB。`close` で全接続を閉じ、`<実ファイル>.lock` を放す。"""

    url: str
    root: Path
    engine: sa.Engine
    path: Optional[Path]
    warnings: list[str] = field(default_factory=list)
    stack: ExitStack = field(default_factory=ExitStack)

    def close(self) -> None:
        self.engine.dispose()
        self.stack.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _sqlite_engine(path: Path) -> sa.Engine:
    """WAL と busy_timeout で開き、トランザクションを `BEGIN IMMEDIATE` で始める Engine（接続はまだしない）。"""
    engine = sa.create_engine(f"{durable.SQLITE_PREFIX}{path}", connect_args={"timeout": BUSY_TIMEOUT_SECONDS}, poolclass=sa.NullPool)

    @sa_event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn: Any, _record: Any) -> None:
        # pysqlite の暗黙の BEGIN を止め、begin の事象で BEGIN IMMEDIATE を打つ（読んでから書く間の取り合いを避ける）
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_SECONDS * 1000}")
        cur.close()

    @sa_event.listens_for(engine, "begin")
    def _begin(conn: sa.Connection) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


@contextmanager
def _translated(root: Path) -> Iterator[None]:
    """SQLite の取り合いと破損を止まった理由へ包み直す。破損を受けたら検査の目印を消す。"""
    try:
        yield
    except sa.exc.OperationalError as exc:
        text = str(exc.orig)
        if "locked" in text or "busy" in text:
            raise StoreBusy(f"進行表の DB の書き込みが {BUSY_TIMEOUT_SECONDS} 秒を超えて待った: {text}") from exc
        raise
    except sa.exc.DatabaseError as exc:
        if "malformed" in str(exc.orig) or "not a database" in str(exc.orig):
            (root / CHECKED_MARK).unlink(missing_ok=True)
            raise durable.StoreBroken(f"進行表の DB が壊れている: {exc.orig}") from exc
        raise


def _read_version(engine: sa.Engine) -> Optional[int]:
    with engine.connect() as conn:
        if not sa.inspect(conn).has_table(VERSIONS.name):
            return None
        return conn.execute(sa.select(VERSIONS.c.value).where(VERSIONS.c.key == SCHEMA_VERSION_KEY)).scalar()


def _write_schema(engine: sa.Engine, found: Optional[int]) -> None:
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(sa.text("SELECT pg_advisory_xact_lock(:space, :key)"), {"space": PG_LOCK_SPACE, "key": PG_SCHEMA_KEY})
        META.create_all(conn)
        if found is None:
            conn.execute(sa.delete(VERSIONS).where(VERSIONS.c.key == SCHEMA_VERSION_KEY))
            conn.execute(sa.insert(VERSIONS).values(key=SCHEMA_VERSION_KEY, value=SCHEMA_VERSION))
        else:
            conn.execute(sa.update(VERSIONS).where(VERSIONS.c.key == SCHEMA_VERSION_KEY).values(value=SCHEMA_VERSION))


def ensure_schema(store: Store, now: datetime) -> None:
    """表が無ければ作り、表の版を上げる。版を上げる前は（SQLite なら）日時に依らず控えを取る。"""
    with _translated(store.root):
        found = _read_version(store.engine)
        if found is not None and found > SCHEMA_VERSION:
            raise durable.StoreFormatNewer(f"進行表の表の版 {found} が自分の {SCHEMA_VERSION} より新しい: {store.url}")
        if found == SCHEMA_VERSION:
            return
        if found is not None and store.path is not None:
            _backup_or_warn(store, now, force=True)
        _write_schema(store.engine, found)


def _backup_or_warn(store: Store, now: datetime, force: bool) -> None:
    try:
        backup(store.engine, store.root, now, force=force)
    except BackupFailed as exc:
        log.warning("進行表の DB の控えを書けなかった", extra={"root": str(store.root), "error": str(exc)})
        store.warnings.append(str(exc))


def _check_and_backup(store: Store, now: datetime) -> None:
    """目印が古ければ `maint_lock` を待って取り、目印を読み直してから `quick_check`・表の用意・控え・目印の更新を行う。"""
    mark = store.root / CHECKED_MARK
    assert store.path is not None
    with durable.maint_lock(store.root, wait=True):
        if _fresh(mark, now):
            ensure_schema(store, now)
            return
        if not durable.check_file(store.path):
            latest = latest_backup(store.root)
            hint = f"。最新の控え: {latest}。ndf progress restore --from {latest} で戻せる" if latest else "。控えが無い"
            raise durable.StoreBroken(f"進行表の DB が quick_check を通らない: {store.path}{hint}", (store.path,))
        ensure_schema(store, now)
        _backup_or_warn(store, now, force=False)
        _touch(mark, now)


def open_store(url: str, root: Path, now: Optional[datetime] = None) -> Store:
    """進行表の DB を開き、表を用意して返す。呼び手は使い終えたら `close` する（`with` で使える）。

    SQLite: 実ファイルへ接続する前に `db_lock` を共有で取る。目印が古ければ検査と控えを行い、通らなければ何も書かずに
    `StoreBroken`。PostgreSQL: 検査も控えもせず、表を用意するだけ。
    """
    now = now or datetime.now(UTC)
    path = sqlite_path(url)
    stack = ExitStack()
    try:
        if path is None:
            store = Store(url, root, sa.create_engine(_sa_url(url), poolclass=sa.NullPool), None, stack=stack)
            ensure_schema(store, now)
            return store
        stack.enter_context(db_lock(path, exclusive=False))
        store = Store(url, root, _sqlite_engine(path), path, stack=stack)
        if _fresh(root / CHECKED_MARK, now):
            ensure_schema(store, now)
        else:
            _check_and_backup(store, now)
        return store
    except BaseException:
        stack.close()
        raise


# --- 控えと戻し --------------------------------------------------------------------------


def _backups(root: Path) -> list[Path]:
    """控えを古い順に（名前は UTC の日時を持つため、名前の順が時刻の順）。"""
    return sorted((root / BACKUP_DIR).glob(f"{BACKUP_PREFIX}*{durable.DB_SUFFIX}"))


def latest_backup(root: Path) -> Optional[Path]:
    found = _backups(root)
    return found[-1] if found else None


def _write_backup(engine: sa.Engine, dest: Path) -> None:
    """SQLite の backup API で写し、写しを WAL でない形にする（戻した後に -wal・-shm を残さない）。"""
    raw = engine.raw_connection()
    try:
        target = sqlite3.connect(dest)
        try:
            raw.driver_connection.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
        finally:
            target.close()
    finally:
        raw.close()


def backup(engine: sa.Engine, root: Path, now: datetime, force: bool = False) -> Optional[Path]:
    """`backup/progress-<UTC の日時>.sqlite` を書き、新しい 7 本を残して古い控えを消す。

    最新の控えが 24 時間以内なら取らずに None（`force` なら日時に依らず取る）。書けなければ書きかけを消して
    `BackupFailed` を投げ、古い控えは消さない。SQLite のときだけ呼ぶ。
    """
    latest = latest_backup(root)
    if not force and latest is not None and _fresh(latest, now):
        return None
    dest = root / BACKUP_DIR / f"{BACKUP_PREFIX}{now:%Y%m%dT%H%M%S%fZ}{durable.DB_SUFFIX}"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_backup(engine, dest)
    except (OSError, sqlite3.Error) as exc:
        dest.unlink(missing_ok=True)
        raise BackupFailed(f"進行表の DB の控えを書けない: {dest}: {exc}") from exc
    os.utime(dest, (now.timestamp(), now.timestamp()))
    for old in _backups(root)[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    return dest


def _with_suffixes(path: Path) -> list[tuple[str, Path]]:
    return [(s, Path(f"{path}{s}")) for s in ("", *durable.SIDE_SUFFIXES)]


def _broken_name(path: Path, now: datetime) -> Path:
    base = path.with_name(f"{path.name}{BROKEN_MARK}{now:%Y%m%dT%H%M%SZ}")
    candidate, n = base, 1
    while any(p.exists() for _, p in _with_suffixes(candidate)):
        candidate = base.with_name(f"{base.name}-{n}")
        n += 1
    return candidate


def _retire_db(path: Path, now: datetime) -> Path:
    """本体と -wal・-shm・-journal をそろえて `<実ファイル>.broken-<日時>`（と同じ接尾辞）へ移す。"""
    target = _broken_name(path, now)
    for suffix, src in _with_suffixes(path):
        if src.exists():
            os.replace(src, Path(f"{target}{suffix}"))
    return target


def _next_backup(root: Path, source: Path) -> Optional[Path]:
    older = [p for p in _backups(root) if p.resolve() != source.resolve()]
    return older[-1] if older else None


def restore(url: str, root: Path, source: Path, now: Optional[datetime] = None) -> Path:
    """控えから進行表の DB を戻し、退避した壊れた DB のパスを返す。自動では呼ばない（`ndf progress restore`）。

    対象は `sqlite_path(url)` の実ファイル。`state.lock` と `<実ファイル>.lock` を排他で取れたときだけ動く。
    控えを一時名へ写して `quick_check` を通してから、本体と -wal・-shm・-journal をそろえて退避し、一時名を本体の名前にする。
    """
    now = now or datetime.now(UTC)
    path = sqlite_path(url)
    if path is None:
        raise RestoreUnsupported("PostgreSQL の控えと戻しは DB の運用で行う")
    with durable.store_lock(root, exclusive=True), db_lock(path, exclusive=True):
        temp = path.with_name(f".{path.name}{RESTORING_SUFFIX}")
        shutil.copyfile(source, temp)
        if not durable.check_file(temp):
            temp.unlink(missing_ok=True)
            raise durable.StoreBroken(f"控え {source} が quick_check を通らない。次に新しい控え: {_next_backup(root, source)}", (source,))
        retired = _retire_db(path, now)
        left = [str(p) for s, p in _with_suffixes(path) if s and p.exists()]
        if left:
            raise durable.StoreBroken(f"退避の後に残ったファイルがある: {', '.join(left)}", (path,))
        os.replace(temp, path)
        _touch(root / CHECKED_MARK, now)
    return retired


# --- 読み書き ----------------------------------------------------------------------------


@contextmanager
def transaction(store: Store) -> Iterator[sa.Connection]:
    """1 つのトランザクション。例外で抜ければ何も残らない。"""
    with _translated(store.root), store.engine.begin() as conn:
        yield conn


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _event(row: Any) -> model.Event:
    draft = model.EventDraft(
        kind=row.kind,
        actor=row.actor,
        row_key=row.row_key,
        state=row.state,
        reason=row.reason,
        payload=row.payload,
        dedupe_key=row.dedupe_key,
        rev=row.rev,
    )
    return model.Event(row.seq, _utc(row.at), draft)


def read_events(conn: sa.Connection, table_id: str, after: int = 0) -> list[model.Event]:
    """1 つの進行表の事象を番号の順に読む（`seq > after`）。"""
    q = sa.select(EVENTS).where(EVENTS.c.table_id == table_id, EVENTS.c.seq > after).order_by(EVENTS.c.seq)
    return [_event(r) for r in conn.execute(q)]


def read_table(conn: sa.Connection, table_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute(sa.select(TABLES).where(TABLES.c.id == table_id)).first()
    return dict(row._mapping) if row is not None else None


def read_definition(conn: sa.Connection, table_id: str) -> tuple[int, dict[str, Any]]:
    """最新の定義の改訂（改訂の番号, スナップショット）。"""
    q = sa.select(DEFINITIONS.c.rev, DEFINITIONS.c.definition).where(DEFINITIONS.c.table_id == table_id)
    row = conn.execute(q.order_by(DEFINITIONS.c.rev.desc()).limit(1)).first()
    if row is None:
        raise model.Refused(f"進行表 {table_id} に定義の改訂が無い")
    return row.rev, row.definition


def _deduped(conn: sa.Connection, key: Optional[str]) -> Optional[int]:
    if key is None:
        return None
    return conn.execute(sa.select(EVENTS.c.seq).where(EVENTS.c.dedupe_key == key)).scalar()


def _insert(conn: sa.Connection, table_id: str, seq: int, draft: model.EventDraft, at: datetime) -> None:
    values = {
        "table_id": table_id,
        "seq": seq,
        "rev": draft.rev,
        "row_key": draft.row_key,
        "kind": draft.kind,
        "state": draft.state,
        "actor": draft.actor,
        "reason": draft.reason,
        "payload": draft.payload,
        "dedupe_key": draft.dedupe_key,
        "at": at,
    }
    try:
        conn.execute(sa.insert(EVENTS).values(**values))
    except sa.exc.IntegrityError as exc:
        raise Conflict(f"進行表 {table_id} の事象の番号 {seq} がぶつかった") from exc


def append_events(
    conn: sa.Connection, table_id: str, drafts: list[model.EventDraft], expect_seq: int, now: Optional[datetime] = None
) -> list[int]:
    """事象を追記する唯一の関数。書く前に書き手の表と遷移を確かめる（I2・I6）。

    `expect_seq` は呼び手が読んだ最後の番号で、違えば `Conflict`。重複の鍵が既にある下書きは書かずに今の番号を返す。
    """
    meta = read_table(conn, table_id)
    if meta is None:
        raise model.Refused(f"進行表 {table_id} は無い")
    level, events = meta["level"], read_events(conn, table_id)
    last = events[-1].seq if events else 0
    if last != expect_seq:
        raise Conflict(f"進行表 {table_id} の最後の番号は {last}（読んだ番号 {expect_seq}）")
    whole = model.table_state(level, events)
    at = now or datetime.now(UTC)
    written = []
    for draft in drafts:
        known = _deduped(conn, draft.dedupe_key)
        if known is not None:
            written.append(known)
            continue
        model.check_event(level, model.rows_for(level, events, draft), whole, draft)
        written_draft = model.recorded(level, draft)
        last += 1
        _insert(conn, table_id, last, written_draft, at)
        events.append(model.Event(last, at, written_draft))
        whole = model.table_state(level, events)
        written.append(last)
    return written


def create_table(
    conn: sa.Connection,
    key: tuple[str, str, str],
    parent_id: Optional[str],
    definition: Optional[dict[str, Any]],
    drafts: list[model.EventDraft],
) -> str:
    """進行表を作り、定義があれば改訂 1 と最初の事象を書く。`key` は（階層, リポジトリ, 名前）。

    同じ識別子の進行表が既にあれば（中止したものを含む）`Refused` に今の識別子を入れて投げる（I1）。
    """
    level, repo, name = key
    tid = model.table_id(level, repo, name)
    if read_table(conn, tid) is not None:
        raise model.Refused(f"同じ進行表が既にある: {tid}", current_id=tid)
    now = datetime.now(UTC)
    try:
        conn.execute(sa.insert(TABLES).values(id=tid, level=level, repo=repo, name=name, parent_id=parent_id, created_at=now))
    except sa.exc.IntegrityError as exc:
        raise model.Refused(f"同じ進行表が既にある: {tid}", current_id=tid) from exc
    if definition is not None:
        conn.execute(sa.insert(DEFINITIONS).values(table_id=tid, rev=1, definition=definition, created_at=now))
    append_events(conn, tid, drafts, 0, now)
    return tid


def table_names(store: Store) -> list[str]:
    """DB にある表の名前（進行表の DB が DBOS の表を持たないことを確かめるのに使う）。"""
    with store.engine.connect() as conn:
        return sorted(sa.inspect(conn).get_table_names())


# --- 状態を変える操作の排他 -------------------------------------------------------------------


def _pg_key(table_id: str) -> int:
    return int.from_bytes(hashlib.sha256(table_id.encode()).digest()[:4], "big", signed=True)


@contextmanager
def run_lock(store: Store, table_id: str) -> Iterator[None]:
    """同じ進行表の状態を変える操作（やり直し・中止・工程を流す）を 1 つに限る。取れなければ待たずに `Refused`。

    SQLite: `<実ファイル>.<進行表の鍵>.lock` の flock。PostgreSQL: 専用の接続の `pg_try_advisory_lock`（I14）。
    """
    if store.path is None:
        with _pg_run_lock(store, table_id):
            yield
        return
    lock = Path(f"{store.path}.{hashlib.sha256(table_id.encode()).hexdigest()[:16]}{durable.LOCK_SUFFIX}")
    handle = _open_lock(lock)
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise model.Refused(f"進行表 {table_id} を変える操作が動いている") from exc
        yield
    finally:
        handle.close()


@contextmanager
def _pg_run_lock(store: Store, table_id: str) -> Iterator[None]:
    params = {"space": PG_LOCK_SPACE, "key": _pg_key(table_id)}
    with store.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        if not conn.execute(sa.text("SELECT pg_try_advisory_lock(:space, :key)"), params).scalar():
            raise model.Refused(f"進行表 {table_id} を変える操作が動いている")
        try:
            yield
        finally:
            conn.execute(sa.text("SELECT pg_advisory_unlock(:space, :key)"), params)
