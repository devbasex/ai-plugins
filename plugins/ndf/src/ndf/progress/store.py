"""ステートマシンの保存（SQLite と PostgreSQL）と、ステートマシンの DB の検査・バックアップ・戻し（#3344 の決定 81・82）。

- 接続先は URL で決める。`NDF_PROGRESS_DB` があればその値、無ければチェックアウトの保存先の
  `sqlite:///<保存先>/progress.sqlite`。ローカルは SQLite、Factory は PostgreSQL の URL を渡す
- ステートマシンの DB は DBOS の表を持たない。DBOS の記録は実行のキーごとに分かれる（`ndf.core.durable`）
- SQLite: 実ファイルへ接続する前に `<実ファイル>.lock` を共有で取り、全接続を閉じるまで持つ（`db_lock`）。
  WAL と `busy_timeout` 30 秒で開き、書くトランザクションは `BEGIN IMMEDIATE` で始める。目印 `progress.checked` が
  24 時間より古いか無ければ、`maint_lock` を待って取り、目印を読み直してから `quick_check` とバックアップを行う
- PostgreSQL: `PRAGMA`・`quick_check`・バックアップを行わない。状態を変える操作は `pg_try_advisory_lock` で 1 つに限る
- 表は 4 つ（ステートマシン・定義の改訂・進行の事象・表のバージョン）。事象と定義の改訂は追記だけで、更新も削除もしない

ロックを取る順序は `state.lock` → `<実ファイル>.lock` → `maint.lock` の 1 方向にする（`ndf.core.durable` の順序に従う）。
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Optional

import sqlalchemy as sa
from sqlalchemy import event as sa_event

from ndf.contracts import limits
from ndf.core import durable
from ndf.progress import backup as _backup
from ndf.progress import model, stages

# バックアップと戻しの名前は ndf.progress.backup が持つ。呼び手（command・テスト）は store から引く
from ndf.progress.backup import (  # noqa: F401
    BACKUP_DIR,
    BACKUP_PREFIX,
    BROKEN_MARK,
    RESTORING_SUFFIX,
    BackupFailed,
    _write_backup,
    backups,
    latest_backup,
)

log = logging.getLogger(__name__)

# 保存先の中の既定の DB のファイル名。ロックのファイル名（durable.PROGRESS_LOCK）はこれに .lock を足したもの
DB_NAME = "progress.sqlite"
# ステートマシンの DB の検査の目印（保存先の中）
CHECKED_MARK = "progress.checked"
# 書き込みの待ちの上限の方針（contracts/limits/）
BUSY_TIMEOUT = "progress.busy-timeout"
# SQLite のエラーの文言で取り合い（StoreBusy）と破損（StoreBroken）を見分ける語
BUSY_MARKERS = ("locked", "busy")
BROKEN_MARKERS = ("malformed", "not a database")
# 表のバージョン（ndf_progress_meta の schema_version）
SCHEMA_VERSION = 2
SCHEMA_VERSION_KEY = "schema_version"
# PostgreSQL のステートマシンの操作の排他（pg_try_advisory_lock の 2 引数の形）の第 1 引数。値は "NDFP" の 4 バイト。
# DBOS の記録のキーのロック（durable.RUN_LOCK_SPACE）と値の空間を分ける
PG_LOCK_SPACE = 0x4E444650
# 表を作るときに PostgreSQL のプロセスどうしを直列にする第 2 引数
PG_SCHEMA_KEY = 0

META = sa.MetaData()
MACHINES = sa.Table(
    "ndf_progress_machines",
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
    sa.Column("machine_id", sa.Text, primary_key=True),
    sa.Column("rev", sa.Integer, primary_key=True),
    sa.Column("definition", sa.JSON, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)
EVENTS = sa.Table(
    "ndf_progress_events",
    META,
    sa.Column("machine_id", sa.Text, primary_key=True),
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


class StoreUnreachable(durable.StoreStopped):
    """DB へ繋げない・使えない（接続・認証の失敗・途中の切断、ファイルを開けない・ディスクの誤り）。DB は壊れていない。"""

    kind = "store_unreachable"


class UnsupportedUrl(durable.StoreStopped):
    """ステートマシンの DB の URL の形を受けない（SQLite の URL のクエリ・メモリの DB）。何も開いていない。"""

    kind = "invalid"


class StoreNotReady(durable.StoreStopped):
    """読むだけで開く経路で、保存先か DB が用意・移行されていない（`open_existing`）。何も変えていない。"""

    kind = "precondition"


class RestoreUnsupported(durable.StoreStopped):
    """PostgreSQL の URL で戻しを求めた。バックアップと戻しは DB の運用で行う。"""

    kind = "restore_unsupported"


class Conflict(Exception):
    """事象の番号がぶつかった（同時の追記。I2）。読み直して判定し直す。"""


# --- 接続先とロック ------------------------------------------------------------------------


def sqlite_path(url: str) -> Optional[Path]:
    """SQLite の URL なら `os.path.realpath` で解いた実ファイルのパス、PostgreSQL の URL なら None。

    URL は SQLAlchemy の解析で読み、接続・検査・ロック・戻しが同じ実ファイルを指すようにする。接続は実ファイルの
    パスだけで開くため、SQLite の URL のクエリ（`?timeout=30` など）・メモリの DB・ファイルの無い URL は、何かを開く前に
    `UnsupportedUrl` で断る。
    """
    try:
        parsed = sa.engine.make_url(url)
    except sa.exc.ArgumentError as exc:
        # 本文は URL を含みうるので出さない
        raise UnsupportedUrl("ステートマシンの DB の URL を読めない") from exc
    if parsed.get_backend_name() != "sqlite":
        return None
    shown = parsed.render_as_string(hide_password=True)
    if parsed.query:
        raise UnsupportedUrl(f"ステートマシンの DB の SQLite の URL にクエリは書けない（ファイルのパスだけを書く）: {shown}")
    if not parsed.database or parsed.database == ":memory:":
        raise UnsupportedUrl(f"ステートマシンの DB の SQLite の URL にファイルのパスが無い: {shown}")
    return Path(os.path.realpath(parsed.database))


def default_url(root: Path) -> str:
    """`NDF_PROGRESS_DB` があればその値、無ければ保存先の既定の SQLite の URL。"""
    given = os.environ.get("NDF_PROGRESS_DB")
    return given if given else f"{durable.SQLITE_PREFIX}{root / DB_NAME}"


def _flock(handle: IO[str], exclusive: bool, path: Path) -> None:
    if not exclusive:
        fcntl.flock(handle, fcntl.LOCK_SH)
        return
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise durable.StoreInUse(f"ステートマシンの DB をほかのプロセスが開いている: {path}", (path,)) from exc


@contextmanager
def db_lock(path: Path, exclusive: bool) -> Iterator[Path]:
    """実ファイルの隣の `<実ファイル>.lock` を取る。共有は待って取り、排他は待たずに試す（取れなければ `StoreInUse`）。

    実ファイルが保存先の中でも外でも取る。どのチェックアウトから・どの URL の書き方で開いても同じパスに解ける（I11）。
    """
    lock = Path(f"{path}{durable.LOCK_SUFFIX}")
    handle = durable.open_lock_file(lock)
    try:
        _flock(handle, exclusive, lock)
        yield lock
    finally:
        handle.close()


# --- 開く --------------------------------------------------------------------------------


@dataclass
class Store:
    """開いたステートマシンの DB。`close` で全接続を閉じ、`<実ファイル>.lock` を放す。"""

    url: str
    root: Path
    engine: sa.Engine
    path: Optional[Path]
    warnings: list[str] = field(default_factory=list)
    stack: ExitStack = field(default_factory=ExitStack)
    # 保存先の閲覧範囲を開いた OS の利用者だけに限れたか（`restrict`）。偽なら判定の自由記述を伏せて書く（#3959）
    private: bool = False

    def close(self) -> None:
        self.engine.dispose()
        self.stack.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _sqlite_engine(path: Path) -> sa.Engine:
    """WAL と busy_timeout で開き、トランザクションを `BEGIN IMMEDIATE` で始める Engine（接続はまだしない）。"""
    busy = limits.seconds(BUSY_TIMEOUT)
    engine = sa.create_engine(f"{durable.SQLITE_PREFIX}{path}", connect_args={"timeout": busy}, poolclass=sa.NullPool)

    @sa_event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn: Any, _record: Any) -> None:
        # pysqlite の暗黙の BEGIN を止め、begin の事象で BEGIN IMMEDIATE を打つ（読んでから書く間の取り合いを避ける）
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute(f"PRAGMA busy_timeout={int(busy * 1000)}")
        cur.close()

    @sa_event.listens_for(engine, "begin")
    def _begin(conn: sa.Connection) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


@contextmanager
def _translated(store: Store) -> Iterator[None]:
    """SQLite の取り合いと破損、DB へ繋げない・切れたことを停止理由へ包み直す。破損を受けたら検査の目印を消す。"""
    try:
        yield
    except sa.exc.OperationalError as exc:
        text = str(exc.orig)
        if any(m in text for m in BUSY_MARKERS):
            raise StoreBusy(f"ステートマシンの DB の書き込みが {limits.seconds(BUSY_TIMEOUT):g} 秒を超えて待った: {text}") from exc
        # 接続の失敗・認証の失敗・途中の切断。接続 URL のパスワードを結果 JSON・ログへ出さない
        shown = store.engine.url.render_as_string(hide_password=True)
        raise StoreUnreachable(f"ステートマシンの DB へ繋げない・使えない（{shown}）: {type(exc.orig).__name__}") from exc
    except sa.exc.DatabaseError as exc:
        text = str(exc.orig)
        if any(m in text for m in BROKEN_MARKERS):
            (store.root / CHECKED_MARK).unlink(missing_ok=True)
            raise durable.StoreBroken(f"ステートマシンの DB が壊れている: {exc.orig}") from exc
        raise


def _read_version(engine: sa.Engine) -> Optional[int]:
    with engine.connect() as conn:
        if not sa.inspect(conn).has_table(VERSIONS.name):
            return None
        return conn.execute(sa.select(VERSIONS.c.value).where(VERSIONS.c.key == SCHEMA_VERSION_KEY)).scalar()


def _write_schema(engine: sa.Engine) -> None:
    """表の無い DB へ表を作り、表のバージョンを書く。"""
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(sa.text("SELECT pg_advisory_xact_lock(:space, :key)"), {"space": PG_LOCK_SPACE, "key": PG_SCHEMA_KEY})
        META.create_all(conn)
        conn.execute(sa.delete(VERSIONS).where(VERSIONS.c.key == SCHEMA_VERSION_KEY))
        conn.execute(sa.insert(VERSIONS).values(key=SCHEMA_VERSION_KEY, value=SCHEMA_VERSION))


def ensure_schema(store: Store, now: datetime) -> None:
    """表が無ければ作る。古いバージョンの表は変えずに止まり、新しいバージョンの表にも触れない。"""
    with _translated(store):
        found = _read_version(store.engine)
        if found is not None and found > SCHEMA_VERSION:
            # 接続 URL のパスワードを結果 JSON・ログへ出さない
            shown = store.engine.url.render_as_string(hide_password=True)
            raise durable.StoreFormatNewer(f"ステートマシンの表のバージョン {found} が自分の {SCHEMA_VERSION} より新しい: {shown}")
        if found == SCHEMA_VERSION:
            return
        if found is not None:
            # バージョン 1 の表は列の名前が古い。列を変える移行は持たず、backend によらず表を変えずに作り直す手順を示して止まる。
            # 既定の保存先の SQLite は保存先の形（STORE_FORMAT）の退避で表ごと移るため、ここへ来るのは NDF_PROGRESS_DB で
            # 保存先の外を指した SQLite と PostgreSQL である
            shown = store.engine.url.render_as_string(hide_password=True)
            raise durable.StoreBroken(
                f"ステートマシンの表のバージョン {found} は列の名前が古い。表 ndf_progress_* を消して作り直す: {shown}"
            )
        _write_schema(store.engine)


def _backup_or_warn(store: Store, now: datetime, force: bool) -> None:
    try:
        backup(store.engine, store.root, now, force=force)
    except BackupFailed as exc:
        log.warning("ステートマシンの DB のバックアップを書けなかった", extra={"root": str(store.root), "error": str(exc)})
        store.warnings.append(str(exc))


def _check_and_backup(store: Store, now: datetime) -> None:
    """目印が古ければ `maint_lock` を待って取り、目印を読み直してから `quick_check`・表の用意・バックアップ・目印の更新を行う。"""
    mark = store.root / CHECKED_MARK
    assert store.path is not None
    with durable.maint_lock(store.root, wait=True):
        if durable.is_fresh(mark, now):
            ensure_schema(store, now)
            return
        if not durable.check_file(store.path):
            latest = latest_backup(store.root)
            hint = f"。最新のバックアップ: {latest}。ndf progress restore --from {latest} で戻せる" if latest else "。バックアップが無い"
            raise durable.StoreBroken(f"ステートマシンの DB が quick_check を通らない: {store.path}{hint}", (store.path,))
        ensure_schema(store, now)
        _backup_or_warn(store, now, force=False)
        durable.touch_mark(mark, now)


# SQLite の副ファイルの接尾辞（WAL・共有メモリ・ロールバックの記録）
SIDE_SUFFIXES = ("-wal", "-shm", "-journal")
# 接続の `info` に置く、保存先の閲覧範囲を限れたか（`Store.private`）のキー
PRIVATE_KEY = "ndf_private"


def restrict(root: Path, path: Optional[Path]) -> bool:
    """接続の前に、保存先の閲覧範囲を開いた OS の利用者だけに限り、限れたかを返す（失敗しても開くことは止めない。#3959）。

    SQLite は保存先のディレクトリを 0700 にする。保存先の外の DB のファイル（`NDF_PROGRESS_DB`）は 0600 にし（無ければ 0600 で
    作る）、既にある副ファイルも 0600 にする。PostgreSQL（`path` が None）は限れない。"""
    if path is None:
        return False
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        if not path.is_relative_to(os.path.realpath(root)):
            os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
            for f in (path, *(Path(f"{path}{x}") for x in SIDE_SUFFIXES)):
                if f == path or f.exists():
                    os.chmod(f, 0o600)
    except OSError as exc:
        log.warning("ステートマシンの保存先の閲覧範囲を限れない", extra={"root": str(root), "error": type(exc).__name__})
        return False
    return True


def open_store(url: str, root: Path, now: Optional[datetime] = None) -> Store:
    """ステートマシンの DB を開き、表を用意して返す。呼び手は使い終えたら `close` する（`with` で使える）。

    SQLite: 実ファイルへ接続する前に `db_lock` を共有で取る。目印が古ければ検査とバックアップを行い、通らなければ何も書かずに
    `StoreBroken`。PostgreSQL: 検査もバックアップもせず、表を用意するだけ。
    """
    now = now or datetime.now(UTC)
    path = sqlite_path(url)
    stack = ExitStack()
    try:
        if path is None:
            store = Store(url, root, sa.create_engine(durable.sa_url(url), poolclass=sa.NullPool), None, stack=stack)
            ensure_schema(store, now)
            return store
        private = restrict(root, path)
        stack.enter_context(db_lock(path, exclusive=False))
        store = Store(url, root, _sqlite_engine(path), path, stack=stack, private=private)
        if durable.is_fresh(root / CHECKED_MARK, now):
            ensure_schema(store, now)
        else:
            _check_and_backup(store, now)
        return store
    except BaseException:
        stack.close()
        raise


# 読むだけの経路で、保存先か DB を用意・移行する必要があるときの停止理由（パスと接続先は載せない）
NOT_READY = "ステートマシンの保存先か DB が無いか、今の NDF の形にそろっていない。ndf progress のコマンドで用意してから打ち直す"


def open_existing(url: str, root: Path) -> Store:
    """既にある保存先と DB を、変えずに開く（判定の入力を読むだけの経路。#3959）。

    `open_store` と違い、保存先の作成・旧い形の退避・表の作成・`quick_check`・バックアップ・目印の更新をしない。
    SQLite の保存先が今の形でなければ、DB の実ファイルか今のバージョンの表が無ければ、何も書かずに `StoreNotReady`。
    表が新しすぎれば `StoreFormatNewer`。呼び手は使い終えたら `close` する（`with` で使える）。
    """
    path = sqlite_path(url)
    stack = ExitStack()
    try:
        if path is None:
            store = Store(url, root, sa.create_engine(durable.sa_url(url), poolclass=sa.NullPool), None, stack=stack)
        else:
            if not durable.hold_existing_store(root) or not path.is_file():
                raise StoreNotReady(NOT_READY)
            stack.enter_context(db_lock(path, exclusive=False))
            store = Store(url, root, _sqlite_engine(path), path, stack=stack)
        with _translated(store):
            found = _read_version(store.engine)
        if found is not None and found > SCHEMA_VERSION:
            shown = store.engine.url.render_as_string(hide_password=True)
            raise durable.StoreFormatNewer(f"ステートマシンの表のバージョン {found} が自分の {SCHEMA_VERSION} より新しい: {shown}")
        if found != SCHEMA_VERSION:
            raise StoreNotReady(NOT_READY)
        return store
    except BaseException:
        stack.close()
        raise


# --- バックアップと戻し --------------------------------------------------------------------------


def backup(engine: sa.Engine, root: Path, now: datetime, force: bool = False) -> Optional[Path]:
    """`ndf.progress.backup.backup` を、このモジュールの `_write_backup` で書き出して呼ぶ（差し替えはここで効く）。"""
    return _backup.backup(engine, root, now, force, write=_write_backup)


def restore(url: str, root: Path, source: Path, now: Optional[datetime] = None) -> Path:
    """バックアップからステートマシンの DB を戻し、退避した壊れた DB のパスを返す。自動では呼ばない（`ndf progress restore`）。

    対象は `sqlite_path(url)` の実ファイル。`state.lock` と `<実ファイル>.lock` を排他で取れたときだけ動く。
    バックアップを一時名へ写して `quick_check` を通してから、本体と -wal・-shm・-journal をそろえて退避し、一時名を本体の名前にする。
    """
    now = now or datetime.now(UTC)
    path = sqlite_path(url)
    if path is None:
        raise RestoreUnsupported("PostgreSQL のバックアップと戻しは DB の運用で行う")
    with durable.store_lock(root, exclusive=True), db_lock(path, exclusive=True):
        retired = _backup.replace_db(root, path, source, now)
        durable.touch_mark(root / CHECKED_MARK, now)
    return retired


# --- 読み書き ----------------------------------------------------------------------------


@contextmanager
def transaction(store: Store) -> Iterator[sa.Connection]:
    """1 つのトランザクション。例外で抜ければ何も残らない。接続に保存先の閲覧範囲（`PRIVATE_KEY`）を置く。"""
    with _translated(store), store.engine.begin() as conn:
        conn.info[PRIVATE_KEY] = store.private
        yield conn


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _event(row: Any) -> model.Event:
    draft = model.EventDraft(**{f.name: getattr(row, f.name) for f in fields(model.EventDraft)})
    return model.Event(row.seq, _utc(row.at), draft)


def read_events(conn: sa.Connection, machine_id: str, after: int = 0) -> list[model.Event]:
    """1 つのステートマシンの事象を番号の順に読む（`seq > after`）。"""
    q = sa.select(EVENTS).where(EVENTS.c.machine_id == machine_id, EVENTS.c.seq > after).order_by(EVENTS.c.seq)
    return [_event(r) for r in conn.execute(q)]


def read_machine(conn: sa.Connection, machine_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute(sa.select(MACHINES).where(MACHINES.c.id == machine_id)).first()
    return dict(row._mapping) if row is not None else None


def read_definition(conn: sa.Connection, machine_id: str) -> tuple[int, dict[str, Any]]:
    """最新の定義の改訂（改訂の番号, スナップショット）。"""
    q = sa.select(DEFINITIONS.c.rev, DEFINITIONS.c.definition).where(DEFINITIONS.c.machine_id == machine_id)
    row = conn.execute(q.order_by(DEFINITIONS.c.rev.desc()).limit(1)).first()
    if row is None:
        raise model.Refused(f"ステートマシン {machine_id} に定義の改訂が無い")
    return row.rev, row.definition


def _deduped(conn: sa.Connection, key: Optional[str]) -> Optional[int]:
    if key is None:
        return None
    return conn.execute(sa.select(EVENTS.c.seq).where(EVENTS.c.dedupe_key == key)).scalar()


def _insert(conn: sa.Connection, machine_id: str, seq: int, draft: model.EventDraft, at: datetime) -> None:
    values = {"machine_id": machine_id, "seq": seq, "at": at, **asdict(draft)}
    try:
        conn.execute(sa.insert(EVENTS).values(**values))
    except sa.exc.IntegrityError as exc:
        raise Conflict(f"ステートマシン {machine_id} の事象の番号 {seq} がぶつかった") from exc


def append_events(
    conn: sa.Connection, machine_id: str, drafts: list[model.EventDraft], expect_seq: int, now: Optional[datetime] = None
) -> list[int]:
    """事象を追記する唯一の関数。書く前に書き手の表と遷移を確かめる（I2・I6）。

    `expect_seq` は呼び手が読んだ最後の番号で、違えば `Conflict`。重複のキーが既にある下書きは書かずに今の番号を返す。
    """
    meta = read_machine(conn, machine_id)
    if meta is None:
        raise model.Refused(f"ステートマシン {machine_id} は無い")
    level, events = meta["level"], read_events(conn, machine_id)
    last = events[-1].seq if events else 0
    if last != expect_seq:
        raise Conflict(f"ステートマシン {machine_id} の最後の番号は {last}（読んだ番号 {expect_seq}）")
    ctx = _Append(conn, machine_id, level, events, now or datetime.now(UTC))
    kept = drafts if conn.info.get(PRIVATE_KEY) is True else [_withheld(d) for d in drafts]
    return _write_drafts(ctx, kept)


# 判定の出力（`model.MVV_KEY`）を payload に持つ事象の種類 → 出力を持つ object までの payload のキー
_MVV_HOLDERS = {
    model.rules()[("sprint", "step_recorded")].kind: ("report", "outputs"),
    stages.OUTPUTS_EVENT: ("outputs",),
    model.rules()[("sprint", "answered")].kind: (),
}


def _withheld(draft: model.EventDraft) -> model.EventDraft:
    """閲覧範囲を限れない保存先へ書く下書き。判定の出力を `model.mvv_stored_view` に通す（ほかの値は変えない。#3959）。"""
    keys = _MVV_HOLDERS.get(draft.kind)
    if keys is None or draft.payload is None:
        return draft

    def walk(part: Any, rest: tuple[str, ...]) -> Any:
        if not isinstance(part, dict):
            return part
        if not rest:
            return {**part, model.MVV_KEY: model.mvv_stored_view(part[model.MVV_KEY])} if model.MVV_KEY in part else part
        return {**part, rest[0]: walk(part[rest[0]], rest[1:])} if rest[0] in part else part

    return replace(draft, payload=walk(draft.payload, keys))


@dataclass(frozen=True)
class _Append:
    """1 回の追記の文脈。`events` は読んだ事象の並びで、書いた事象を足していく（呼び手と同じリストを共有する）。"""

    conn: sa.Connection
    machine_id: str
    level: str
    events: list[model.Event]
    at: datetime


def _write_drafts(ctx: _Append, drafts: list[model.EventDraft]) -> list[int]:
    """下書きを順に書き、番号を返す。重複のキーが既にある下書きは書かずに今の番号を返す。`events` へ書いた事象を足す。"""
    written = []
    for draft in drafts:
        known = _deduped(ctx.conn, draft.dedupe_key)
        written.append(known if known is not None else _write_draft(ctx, draft))
    return written


def _write_draft(ctx: _Append, draft: model.EventDraft) -> int:
    """1 件を確かめて次の番号で書き、`events` へ足して番号を返す。"""
    level, events = ctx.level, ctx.events
    model.check_event(level, model.rows_for(level, events, draft), model.table_state(level, events), draft)
    written_draft = model.recorded(level, draft)
    seq = (events[-1].seq if events else 0) + 1
    _insert(ctx.conn, ctx.machine_id, seq, written_draft, ctx.at)
    events.append(model.Event(seq, ctx.at, written_draft))
    return seq


def create_machine(
    conn: sa.Connection,
    key: tuple[str, str, str],
    parent_id: Optional[str],
    definition: Optional[dict[str, Any]],
    drafts: list[model.EventDraft],
) -> str:
    """ステートマシンを作り、定義があれば改訂 1 と最初の事象を書く。`key` は（階層, リポジトリ, 名前）。

    同じ識別子のステートマシンが既にあれば（中止したものを含む）`Refused` に今の識別子を入れて投げる（I1）。
    """
    level, repo, name = key
    tid = model.machine_id(level, repo, name)
    if read_machine(conn, tid) is not None:
        raise model.Refused(f"同じステートマシンが既にある: {tid}", current_id=tid)
    now = datetime.now(UTC)
    try:
        conn.execute(sa.insert(MACHINES).values(id=tid, level=level, repo=repo, name=name, parent_id=parent_id, created_at=now))
    except sa.exc.IntegrityError as exc:
        raise model.Refused(f"同じステートマシンが既にある: {tid}", current_id=tid) from exc
    if definition is not None:
        conn.execute(sa.insert(DEFINITIONS).values(machine_id=tid, rev=1, definition=definition, created_at=now))
    append_events(conn, tid, drafts, 0, now)
    return tid


def table_names(store: Store) -> list[str]:
    """DB にある表の名前（ステートマシンの DB が DBOS の表を持たないことを確かめるのに使う）。"""
    with store.engine.connect() as conn:
        return sorted(sa.inspect(conn).get_table_names())


# --- 状態を変える操作の排他 -------------------------------------------------------------------


def _pg_key(machine_id: str) -> int:
    return int.from_bytes(hashlib.sha256(machine_id.encode()).digest()[:4], "big", signed=True)


@contextmanager
def run_lock(store: Store, machine_id: str) -> Iterator[None]:
    """同じステートマシンの状態を変える操作（やり直し・中止・工程を流す）を 1 つに限る。取れなければ待たずに `Refused`。

    SQLite: `<実ファイル>.<ステートマシンのキー>.lock` の flock。PostgreSQL: 専用の接続の `pg_try_advisory_lock`（I14）。
    """
    lock = _pg_run_lock(store, machine_id) if store.path is None else _file_run_lock(store.path, machine_id)
    with lock:
        yield


def _run_lock_path(path: Path, machine_id: str) -> Path:
    """`<実ファイル>.<ステートマシンのキー>.lock`。"""
    return Path(f"{path}.{hashlib.sha256(machine_id.encode()).hexdigest()[:16]}{durable.LOCK_SUFFIX}")


@contextmanager
def _file_run_lock(path: Path, machine_id: str) -> Iterator[None]:
    handle = durable.open_lock_file(_run_lock_path(path, machine_id))
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise model.Refused(f"ステートマシン {machine_id} を変える操作が動いている") from exc
        yield
    finally:
        handle.close()


@contextmanager
def _pg_run_lock(store: Store, machine_id: str) -> Iterator[None]:
    params = {"space": PG_LOCK_SPACE, "key": _pg_key(machine_id)}
    with store.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        if not conn.execute(sa.text("SELECT pg_try_advisory_lock(:space, :key)"), params).scalar():
            raise model.Refused(f"ステートマシン {machine_id} を変える操作が動いている")
        try:
            yield
        finally:
            conn.execute(sa.text("SELECT pg_advisory_unlock(:space, :key)"), params)
