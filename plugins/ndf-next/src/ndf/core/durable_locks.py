"""DBOS の殻が使うロックと、記録の DB（SQLite・PostgreSQL）への接続。DBOS は import しない。

- 待つロックはすべて `LOCK_WAIT_SECONDS` を上限にし、当たれば持ち主を添えて `StoreInUse` で止まる（B11）。
  排他で取ったプロセスは、ロックのファイルに持ち主（ホスト・pid・時刻）を書く。PostgreSQL の advisory lock は、
  接続の `application_name` に持ち主を書く
- PostgreSQL の接続は keepalive と idle_session_timeout を持つ。鍵のロックを持つ接続は間隔ごとに問い合わせを打ち、
  idle_session_timeout にロックを放させない
- 置き場を使えずに止まった例外（`StoreStopped` とその子）の `kind` は、止まった理由の表の語である
"""

from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import logging
import os
import socket
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import IO, TYPE_CHECKING, Any

log = logging.getLogger(__name__)


def _lazy(name: str) -> ModuleType:
    """属性を初めて読むときに import するモジュール（標準の `importlib.util.LazyLoader`）。読み込み済みならそれを返す。

    SQLAlchemy の import は約 0.1 秒の CPU を使い、SQLite で DBOS を開かない呼び出し（`plan wait` など）には要らない（B3）。
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.find_spec(name)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(name)
    loader = importlib.util.LazyLoader(spec.loader)
    spec.loader = loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


if TYPE_CHECKING:
    import sqlalchemy as sa
else:
    # 殻のモジュール（durable・durable_ledger）もこれを使う
    sa = _lazy("sqlalchemy")

# 置き場のロックと、共有の目印の検査・走査のロック。どちらも state/ の親に置き、退避で動かない（I11）
STORE_LOCK = "state.lock"
MAINT_LOCK = "maint.lock"
# URL のときの鍵のロック（pg_advisory_lock の 2 引数の形）の第 1 引数。値は "NDFR" の 4 バイト
RUN_LOCK_SPACE = 0x4E444652
# SQLite の URL の接頭辞（この後ろがファイルの絶対パス）
SQLITE_PREFIX = "sqlite:///"
# ロックを待つ上限の秒と、待つ間に試す間隔の秒。上限に当たると持ち主を添えて `StoreInUse`（B11）
LOCK_WAIT_SECONDS = 300.0
LOCK_POLL_SECONDS = 0.05
# PostgreSQL の接続の keepalive（相手が消えた接続を 60 秒ほどで切る）と、NDF の接続の idle_session_timeout（ミリ秒）
PG_KEEPALIVE = {"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3}
PG_IDLE_SESSION_MS = 600_000
# 鍵のロックを持つ PostgreSQL の接続が、idle_session_timeout に切られないよう打つ問い合わせの間隔の秒
PG_HEARTBEAT_SECONDS = 60.0


class StoreStopped(Exception):
    """置き場を使えずに止まった。`kind` は止まった理由の表の語、`paths` は関わったパス。"""

    kind = "store_broken"

    def __init__(self, message: str, paths: tuple[Path, ...] = ()) -> None:
        super().__init__(message)
        self.paths = paths


class StoreInUse(StoreStopped):
    """排他が要る操作で、ほかのプロセスがロックを持っていた。何も変えていない。"""

    kind = "store_in_use"


class StoreBroken(StoreStopped):
    """記録のファイルが壊れていた・台帳に書けなかった。"""

    kind = "store_broken"


class StoreFormatNewer(StoreStopped):
    """置き場の形の番号が自分より大きい。何も書いていない。"""

    kind = "format_newer"


class StoreConfigBroken(StoreStopped):
    """ロックのファイルを作れない（置き場が書き込めない）。"""

    kind = "config_broken"


def sqlite_path(database_url: str) -> Path | None:
    """SQLite の URL ならファイルのパス、そうでなければ None。"""
    return Path(database_url[len(SQLITE_PREFIX) :]) if database_url.startswith(SQLITE_PREFIX) else None


def is_sqlite(database_url: str) -> bool:
    return sqlite_path(database_url) is not None


def sqlite_url(path: Path) -> str:
    return f"{SQLITE_PREFIX}{path}"


def sa_url(database_url: str) -> str:
    """SQLAlchemy が psycopg 3 で開く形の URL にする（DBOS は `postgresql://` を受ける）。"""
    for scheme in ("postgresql://", "postgres://"):
        if database_url.startswith(scheme):
            return "postgresql+psycopg://" + database_url[len(scheme) :]
    return database_url


def pg_connect_args(application_name: str | None = None) -> dict[str, Any]:
    """NDF が開く PostgreSQL の接続の引数。keepalive と idle_session_timeout を持つ（B11）。"""
    args: dict[str, Any] = {**PG_KEEPALIVE, "options": f"-c idle_session_timeout={PG_IDLE_SESSION_MS}"}
    if application_name is not None:
        args["application_name"] = application_name
    return args


@contextmanager
def open_engine(database_url: str, application_name: str | None = None) -> Iterator[sa.Engine]:
    """呼ぶたびに接続を溜めない Engine を作り、抜けるときに解放する。PostgreSQL では keepalive と idle_session_timeout を付ける。"""
    connect_args = {} if is_sqlite(database_url) else pg_connect_args(application_name)
    engine = sa.create_engine(sa_url(database_url), poolclass=sa.NullPool, connect_args=connect_args)
    try:
        yield engine
    finally:
        engine.dispose()


def open_lock_file(path: Path) -> IO[str]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return open(path, "a")  # noqa: SIM115 - ロックを持つ間は開いたまま置く
    except OSError as exc:
        raise StoreConfigBroken(f"ロックのファイルを作れない: {path}: {exc}", (path,)) from exc


def try_flock(handle: IO[str], mode: int) -> bool:
    try:
        fcntl.flock(handle, mode | fcntl.LOCK_NB)
    except BlockingIOError:  # noqa: NDF401 ファイル: ほかのプロセスがロックを持っていれば取れなかったと返す
        return False
    return True


def lock(handle: IO[str], mode: int, wait: bool, path: Path) -> bool:
    """`wait` なら `LOCK_WAIT_SECONDS` まで待って真、そうでなければ待たずに試して取れたかを返す。

    待って取れなければ持ち主（排他で持ったプロセスがファイルに書いたホスト・pid・時刻）を添えて `StoreInUse`。
    排他で取れたら、自分を持ち主としてファイルに書く（B11）。
    """
    got = try_flock(handle, mode)
    if wait and not got:
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while not got and time.monotonic() < deadline:
            time.sleep(LOCK_POLL_SECONDS)
            got = try_flock(handle, mode)
        if not got:
            raise StoreInUse(f"ロックを {LOCK_WAIT_SECONDS:g} 秒待っても取れない: {path}（持ち主: {lock_owner(path)}）", (path,))
    if got and mode == fcntl.LOCK_EX:
        write_owner(handle)
    return got


def _owner_text() -> str:
    return json.dumps({"host": socket.gethostname(), "pid": os.getpid(), "at": datetime.now(UTC).isoformat(timespec="seconds")})


def write_owner(handle: IO[str]) -> None:
    """排他で持ったロックのファイルを、自分の持ち主の記録 1 行で置き換える（追記の口で開いているので先に空にする）。"""
    handle.truncate(0)
    handle.write(_owner_text() + "\n")
    handle.flush()


def lock_owner(path: Path) -> str:
    """ロックのファイルが持つ持ち主の記録（ホスト・pid・時刻）。無い・読めなければその旨。"""
    try:
        found = json.loads(path.read_text(encoding="utf-8").strip() or "null")
    except (OSError, ValueError):
        found = None
    if not isinstance(found, dict):
        return "記録が無い"
    return f"ホスト {found.get('host')}・pid {found.get('pid')}・時刻 {found.get('at')}"


def store_lock_path(root: Path) -> Path:
    return root.parent / STORE_LOCK


@contextmanager
def store_lock(root: Path, exclusive: bool) -> Iterator[Path]:
    """置き場のロック（`state/` の隣の `state.lock`）。共有は上限まで待って取り、排他は待たずに試す（取れなければ `StoreInUse`）。"""
    path = store_lock_path(root)
    handle = open_lock_file(path)
    try:
        if exclusive:
            if not try_flock(handle, fcntl.LOCK_EX):
                raise StoreInUse(f"置き場をほかのプロセスが使っている: {path}（持ち主: {lock_owner(path)}）", (path,))
            write_owner(handle)
        else:
            lock(handle, fcntl.LOCK_SH, True, path)
        yield path
    finally:
        handle.close()


@contextmanager
def maint_lock(root: Path, wait: bool) -> Iterator[Path | None]:
    """共有の目印の検査・控え・走査を 1 プロセスに限るロック。`wait` が偽で取れなければ None を渡す。

    `wait` が真なら上限まで待ち、取れなければ持ち主を添えて `StoreInUse`。
    """
    path = root.parent / MAINT_LOCK
    handle = open_lock_file(path)
    try:
        if not lock(handle, fcntl.LOCK_EX, wait, path):
            yield None
            return
        yield path
    finally:
        handle.close()


def lock_key(name: str) -> int:
    """鍵の名前から、PostgreSQL の advisory lock の第 2 引数（符号付き 32 ビット）を求める。"""
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:4], "big", signed=True)


@contextmanager
def file_run_lock(path: Path, wait: bool) -> Iterator[Path | None]:
    """鍵のロックのファイル `path` の flock。`wait` が偽で取れなければ None を渡す。"""
    while True:
        handle = open_lock_file(path)
        try:
            got = lock(handle, fcntl.LOCK_EX, wait, path)
        except StoreInUse:
            handle.close()
            raise
        if not got:
            handle.close()
            yield None
            return
        # 片付けが .lock を消した後に取ったロックは、別のプロセスが作り直したファイルと食い違うので開き直す
        try:
            same = os.stat(path).st_ino == os.fstat(handle.fileno()).st_ino
        except FileNotFoundError:
            same = False
        if same:
            break
        handle.close()
    try:
        yield path
    finally:
        handle.close()


@contextmanager
def pg_run_lock(database_url: str, name: str, wait: bool) -> Iterator[str | None]:
    """DB 側の鍵の advisory lock。待つときは `lock_timeout` を上限にし、当たれば持ち主を添えて `StoreInUse`（B11）。

    接続の `application_name` に持ち主（ホスト・pid）を書き、ほかのプロセスが `pg_stat_activity` から読めるようにする。
    持つ間は `PG_HEARTBEAT_SECONDS` ごとに問い合わせを打ち、idle_session_timeout にロックを放させない。
    """
    owner = f"ndf-lock {socket.gethostname()} {os.getpid()}"
    with open_engine(database_url, owner) as engine, engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        params = {"space": RUN_LOCK_SPACE, "key": lock_key(name)}
        if wait:
            conn.execute(sa.text(f"SET lock_timeout = '{int(LOCK_WAIT_SECONDS * 1000)}ms'"))
            try:
                conn.execute(sa.text("SELECT pg_advisory_lock(:space, :key)"), params)
            except sa.exc.OperationalError as exc:
                raise StoreInUse(
                    f"鍵 {name} のロックを {LOCK_WAIT_SECONDS:g} 秒待っても取れない（持ち主: {_pg_lock_owner(database_url, params)}）"
                ) from exc
            conn.execute(sa.text("SET lock_timeout = 0"))
        elif not conn.execute(sa.text("SELECT pg_try_advisory_lock(:space, :key)"), params).scalar():
            yield None
            return
        stop = threading.Event()
        beat = threading.Thread(target=_heartbeat, args=(conn, stop), daemon=True)
        beat.start()
        try:
            yield name
        finally:
            stop.set()
            beat.join()
            conn.execute(sa.text("SELECT pg_advisory_unlock(:space, :key)"), params)


def _heartbeat(conn: sa.Connection, stop: threading.Event) -> None:
    """ロックを持つ接続へ間隔ごとに問い合わせを打つ。接続が切れたら打つのをやめる（ロックは DB 側で放れている）。"""
    while not stop.wait(PG_HEARTBEAT_SECONDS):
        try:
            conn.execute(sa.text("SELECT 1"))
        except sa.exc.SQLAlchemyError as exc:
            log.warning("鍵のロックの接続が切れた", extra={"error": str(exc)})
            return


def _pg_lock_owner(database_url: str, params: dict[str, int]) -> str:
    """advisory lock を持つ接続の `application_name`・相手の番地・接続の時刻。読めなければその旨。"""
    query = sa.text(
        "SELECT a.application_name, a.client_addr, a.backend_start FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
        "WHERE l.locktype = 'advisory' AND l.granted AND l.classid = :space AND l.objid = :objid AND l.objsubid = 2"
    )
    try:
        with open_engine(database_url) as engine, engine.connect() as conn:
            row = conn.execute(query, {"space": params["space"], "objid": params["key"] & 0xFFFFFFFF}).first()
    except sa.exc.SQLAlchemyError as exc:
        return f"読めない: {exc}"
    if row is None:
        return "記録が無い"
    return f"{row[0]}・番地 {row[1]}・接続 {row[2]}"
