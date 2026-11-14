"""DBOS の殻。DBOS を import するのはこのモジュールだけである。

耐久ワークフローと耐久ステップを使う層は、DBOS の型と関数をここから受ける。判断の規則はここに置かず、
`ndf.core.loop` の判断の関数に置く。

記録の置き場（設計の決定 81〜86。issues/issue-1142-design-next.md の「DBOS の記録と進行表の置き場」）:

- チェックアウトの置き場 `<git の共通ディレクトリ>/<プラグイン名>/state/` は、先頭に形の番号 `format.json` を持つ。
  使うプロセスは隣の `state.lock` を共有で持ち、番号が古い置き場は排他の下で名前を変えて退避する（`prepare_store`）
- SQLite では、DBOS の記録を実行の鍵ごとのファイル `state/dbos/<鍵の名前>.sqlite` に置き、`run_lock` で開くプロセスを
  1 つに限る。URL（PostgreSQL）では 1 つの DB に置き、`application_name` で鍵ごとに分け、起こした鍵を台帳 `ndf_runs` に書く
- `executor_id` と `application_name` はどちらも鍵の名前（`run_name`）にする。DBOS のキューの取り出しは
  `application_name` で絞られるため、別の鍵のプロセスがよそのキューを流さない
- アプリの版は形の番号と鍵の名前 `format-<FORMAT>-<鍵の名前>` に固定し、NDF の版が上がっても途中の実行を回復する。
  DBOS の版名は DB 全体で一意のため、1 つの DB を鍵ごとに分ける URL でも衝突しないよう鍵の名前を入れる
- 終わった鍵のファイルは `dbos/retired/` へ、壊れたファイルは `dbos/broken/` へ移し、移してから `KEEP_DAYS` で消す。
  URL では、台帳の鍵の実行がすべて終わって `KEEP_DAYS` を過ぎたときだけ消す（`sweep`・`sweep_url`）

ロックを取る順序は `state.lock` → `run_lock` → `maint.lock` の 1 方向にする。`maint.lock` を持つ間は
`run_lock` を待たずに試すだけにする。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any, TypeVar

import dbos._dbos as _dbos_runtime
import sqlalchemy as sa
from dbos import DBOS, DBOSClient, DBOSConfig, SetEnqueueOptions, SetWorkflowID

# 置き場の先頭の形の番号のファイル名は、状態の置き場（identity）と同じ名前を使う
from ndf.identity import FORMAT_FILE

R = TypeVar("R")

log = logging.getLogger(__name__)

# DBOS の記録に載せるログの下限。標準出力は結果 JSON の 1 行に使うため、DBOS の INFO を出さない
LOG_LEVEL = "WARNING"

# 耐久ワークフローの中の DBOS の呼び出しの順の番号。アプリの版は `format-<FORMAT>-<鍵の名前>`（決定 84）
FORMAT = 1
# 耐久ワークフローの関数（`@durable.workflow` を付けた src の関数）のソースの sha256。
# 関数を変えたら、FORMAT を上げるか、呼び出しの順が変わらないと判断してこの値だけを書き直す
FORMAT_SOURCE_HASH = "1f008bfee90a33d69bd70f2dcbead91dee794d53cd67ad5b82fd603887c30b29"
# チェックアウトの置き場の `format.json` の番号（決定 85）
STORE_FORMAT = 1
# retired/・broken/ と URL の終わった実行を消すまでの日数（決定 83）
KEEP_DAYS = 14
# 鍵の名前の接頭辞。名前の形を決めるだけで、URL の片付けの絞り込みには使わない（絞るのは台帳）
RUN_PREFIX = "run-"
# URL のとき、NDF が起こした鍵を書く台帳の表。進行表と同じ DB に置き、DBOS の表ではない（I12）
RUNS_TABLE = "ndf_runs"
# 置き場のロックと、共有の目印の検査・走査のロック。どちらも state/ の親に置き、退避で動かない（I11）
STORE_LOCK = "state.lock"
MAINT_LOCK = "maint.lock"
# URL のときの鍵のロック（pg_advisory_lock の 2 引数の形）の第 1 引数。値は "NDFR" の 4 バイト
RUN_LOCK_SPACE = 0x4E444652
# quick_check と片付けの走査の間隔（決定 82・83）
CHECK_INTERVAL = timedelta(hours=24)
# DBOS を閉じる前に、走っている耐久ワークフローが記録を終えたかを見る間隔（秒）
SHUTDOWN_POLL_SECONDS = 0.05

DBOS_DIR = "dbos"
RETIRED_DIR = "retired"
BROKEN_DIR = "broken"
SWEPT_MARK = ".swept"
PROGRESS_LOCK = "progress.sqlite.lock"
# SQLite の本体に付いて動くファイルの接尾辞。退避では本体とそろえて移す
SIDE_SUFFIXES = ("-wal", "-shm", "-journal")
# 鍵ごとのファイルの本体・検査の目印・鍵のロックの接尾辞
DB_SUFFIX = ".sqlite"
CHECKED_SUFFIX = ".checked"
LOCK_SUFFIX = ".lock"
# SQLite の URL の接頭辞（この後ろがファイルの絶対パス）
SQLITE_PREFIX = "sqlite:///"
FINISHED = frozenset({"SUCCESS", "ERROR", "CANCELLED"})
WAITING = ("PENDING", "ENQUEUED", "DELAYED")
# 置き場の退避で番号を読み直す回数の上限（決定 85 の手順の (5)）
PREPARE_ATTEMPTS = 3


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


@dataclass(frozen=True)
class StoreReady:
    """用意した置き場。`retired_to` は退避した旧い置き場、`pending_count` はそこで待ちを抱えていた実行の数。"""

    root: Path
    retired_to: Path | None = None
    pending_count: int = 0


@dataclass
class SweepResult:
    """片付けの結果。`skipped` は走査しなかった（ほかが走査中・目印が新しい）こと、`busy` は開いている鍵の数。"""

    retired: list[Path] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: bool = False
    busy: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RunRecord:
    """耐久ワークフロー 1 つの記録。`run_name` は持ち主の鍵の名前（持ち主の無い行は None）、`updated_at` は最後に状態が変わった時刻、`app_version` は起こしたときのアプリの版。"""

    run_name: str | None
    workflow_id: str
    status: str
    updated_at: datetime | None
    parent_workflow_id: str | None = None
    app_version: str | None = None


@dataclass(frozen=True)
class LedgerRow:
    """台帳 `ndf_runs` の 1 行。鍵の名前・実行の鍵・起こした時刻。"""

    run_name: str
    run_key: str
    started_at: datetime


# 置き場のロックを共有で持ち続けるファイル（プロセスが終わるまで放さない）
_held_stores: dict[Path, IO[str]] = {}


# --- 耐久ワークフローと耐久ステップ -------------------------------------------------------


def workflow(fn: Callable[..., R]) -> Callable[..., R]:
    """関数を耐久ワークフローにする。中で呼んだ耐久ステップの結果が記録に残る。"""
    return DBOS.workflow()(fn)


def step(fn: Callable[..., R]) -> Callable[..., R]:
    """関数を耐久ステップにする。済んだ呼び出しは回復のときに流し直さず、記録した結果を返す。"""
    return DBOS.step()(fn)


def start(fn: Callable[..., R], workflow_id: str, *args: Any) -> R:
    """ID を決めて耐久ワークフローを流し、結果を返す。同じ ID が終わっていれば、記録した結果を返す。"""
    with SetWorkflowID(workflow_id):
        return fn(*args)


def spawn(fn: Callable[..., Any], workflow_id: str, *args: Any) -> None:
    """ID を決めて耐久ワークフローを裏で流し、終わりを待たずに戻る。耐久ワークフローの中で呼ぶと子になる。"""
    with SetWorkflowID(workflow_id):
        DBOS.start_workflow(fn, *args)


def queue(name: str) -> Any:
    """キューを登録して返す（DBOS を起こした後）。キューは鍵の `application_name` の持ち物になる。"""
    return DBOS.register_queue(name)


def enqueue(target: Any, fn: Callable[..., Any], workflow_id: str, *args: Any, delay_seconds: float | None = None) -> None:
    """耐久ワークフローをキュー `target` へ入れる。`delay_seconds` を渡すと、その秒数の後に取り出せるようになる。"""
    with SetWorkflowID(workflow_id), SetEnqueueOptions(delay_seconds=delay_seconds):
        target.enqueue(fn, *args)


def result(workflow_id: str) -> Any:
    """耐久ワークフローが終わるまで待ち、結果を返す（回復で流れている途中のものも待つ）。"""
    return DBOS.retrieve_workflow(workflow_id).get_result()


def status(workflow_id: str) -> str | None:
    """耐久ワークフローの状態（`PENDING`・`SUCCESS`・`ERROR`・`CANCELLED` など）。記録に無ければ None。"""
    found = DBOS.get_workflow_status(workflow_id)
    return None if found is None else found.status


def current_id() -> str | None:
    """流れている耐久ワークフローの ID。耐久ワークフローの外では None。"""
    return DBOS.workflow_id


def send(workflow_id: str, message: Any, topic: str) -> None:
    """耐久ワークフローへ話題 `topic` のメッセージを送る（DBOS を起こした後）。"""
    DBOS.send(workflow_id, message, topic)


def recv(topic: str, timeout_seconds: float) -> Any:
    """話題 `topic` のメッセージを待つ。期限までに来なければ None。耐久ワークフローの中でだけ呼ぶ。"""
    return DBOS.recv(topic, timeout_seconds=timeout_seconds)


# --- 鍵の名前と URL ---------------------------------------------------------------------


def run_name(run_key: str) -> str:
    """実行の鍵から記録の名前を求める。DBOS のアプリ名の規則（英小文字・数字・`-`）に収まり、同じ鍵には同じ名前。"""
    return RUN_PREFIX + hashlib.sha256(run_key.encode()).hexdigest()[:32]


def app_version(run_key: str) -> str:
    """実行の鍵のアプリの版。鍵ごとに 1 つで、NDF の版を上げても変わらない（決定 84）。

    DBOS の `application_versions.version_name` は DB 全体で一意なので、鍵の名前を入れて 1 つの DB の鍵どうしで分ける。
    """
    return f"format-{FORMAT}-{run_name(run_key)}"


def run_url(root: Path, run_key: str) -> str:
    """SQLite のときの、鍵ごとのファイルの URL。"""
    return _sqlite_url(_dbos_dir(root) / f"{run_name(run_key)}{DB_SUFFIX}")


def _dbos_dir(root: Path) -> Path:
    return root / DBOS_DIR


def _sqlite_path(database_url: str) -> Path | None:
    """SQLite の URL ならファイルのパス、そうでなければ None。"""
    return Path(database_url[len(SQLITE_PREFIX) :]) if database_url.startswith(SQLITE_PREFIX) else None


def _is_sqlite(database_url: str) -> bool:
    return _sqlite_path(database_url) is not None


def _sqlite_url(path: Path) -> str:
    return f"{SQLITE_PREFIX}{path}"


def _sa_url(database_url: str) -> str:
    """SQLAlchemy が psycopg 3 で開く形の URL にする（DBOS は `postgresql://` を受ける）。"""
    for scheme in ("postgresql://", "postgres://"):
        if database_url.startswith(scheme):
            return "postgresql+psycopg://" + database_url[len(scheme) :]
    return database_url


@contextmanager
def _engine(database_url: str) -> Iterator[sa.Engine]:
    """呼ぶたびに接続を溜めない Engine を作り、抜けるときに解放する。"""
    engine = sa.create_engine(_sa_url(database_url), poolclass=sa.NullPool)
    try:
        yield engine
    finally:
        engine.dispose()


# --- ロック -----------------------------------------------------------------------------


def _open_lock_file(path: Path) -> IO[str]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return open(path, "a")  # noqa: SIM115 - ロックを持つ間は開いたまま置く
    except OSError as exc:
        raise StoreConfigBroken(f"ロックのファイルを作れない: {path}: {exc}", (path,)) from exc


def _try_flock(handle: IO[str], mode: int) -> bool:
    try:
        fcntl.flock(handle, mode | fcntl.LOCK_NB)
    except BlockingIOError:  # noqa: NDF401 ファイル: ほかのプロセスがロックを持っていれば取れなかったと返す
        return False
    return True


def _lock(handle: IO[str], mode: int, wait: bool) -> bool:
    """`wait` なら取れるまで待って真、そうでなければ待たずに試して取れたかを返す。"""
    if wait:
        fcntl.flock(handle, mode)
        return True
    return _try_flock(handle, mode)


def store_root(name: str, cwd: Path) -> Path:
    """チェックアウトの置き場 `<git の共通ディレクトリ>/<name>/state`。どの worktree から打っても同じパス。"""
    out = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return Path(out).resolve() / name / "state"


def _store_lock_path(root: Path) -> Path:
    return root.parent / STORE_LOCK


@contextmanager
def store_lock(root: Path, exclusive: bool) -> Iterator[Path]:
    """置き場のロック（`state/` の隣の `state.lock`）。共有は待って取り、排他は待たずに試す（取れなければ `StoreInUse`）。"""
    path = _store_lock_path(root)
    handle = _open_lock_file(path)
    try:
        if exclusive:
            if not _try_flock(handle, fcntl.LOCK_EX):
                raise StoreInUse(f"置き場をほかのプロセスが使っている: {path}", (path,))
        else:
            fcntl.flock(handle, fcntl.LOCK_SH)
        yield path
    finally:
        handle.close()


@contextmanager
def maint_lock(root: Path, wait: bool) -> Iterator[Path | None]:
    """共有の目印の検査・控え・走査を 1 プロセスに限るロック。`wait` が偽で取れなければ None を渡す。"""
    path = root.parent / MAINT_LOCK
    handle = _open_lock_file(path)
    try:
        if not _lock(handle, fcntl.LOCK_EX, wait):
            yield None
            return
        yield path
    finally:
        handle.close()


def _lock_key(name: str) -> int:
    """鍵の名前から、PostgreSQL の advisory lock の第 2 引数（符号付き 32 ビット）を求める。"""
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:4], "big", signed=True)


@contextmanager
def run_lock(root: Path, run_key: str, database_url: str | None = None, wait: bool = True) -> Iterator[Any]:
    """実行の鍵の排他。SQLite は `dbos/<鍵の名前>.lock` の flock、PostgreSQL は DB 側の advisory lock。

    `wait` が偽で取れなければ None を渡す。呼び手は DBOS を起こす前に取り、閉じて退避するまで持つ（I6・I13）。
    """
    name = run_name(run_key)
    if database_url is not None and not _is_sqlite(database_url):
        with _pg_run_lock(database_url, name, wait) as held:
            yield held
    else:
        with _file_run_lock(root, name, wait) as held:
            yield held


@contextmanager
def _file_run_lock(root: Path, name: str, wait: bool) -> Iterator[Path | None]:
    path = _dbos_dir(root) / f"{name}{LOCK_SUFFIX}"
    while True:
        handle = _open_lock_file(path)
        if not _lock(handle, fcntl.LOCK_EX, wait):
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
def _pg_run_lock(database_url: str, name: str, wait: bool) -> Iterator[str | None]:
    with _engine(database_url) as engine, engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        params = {"space": RUN_LOCK_SPACE, "key": _lock_key(name)}
        if wait:
            conn.execute(sa.text("SELECT pg_advisory_lock(:space, :key)"), params)
        elif not conn.execute(sa.text("SELECT pg_try_advisory_lock(:space, :key)"), params).scalar():
            yield None
            return
        try:
            yield name
        finally:
            conn.execute(sa.text("SELECT pg_advisory_unlock(:space, :key)"), params)


# --- チェックアウトの置き場（決定 85） ---------------------------------------------------


def _read_format(root: Path) -> int | None:
    """置き場の形の番号。置き場が無いか空なら None、`format.json` が無くほかのファイルがあれば 0。"""
    if not root.is_dir() or not any(root.iterdir()):
        return None
    try:
        return int(json.loads((root / FORMAT_FILE).read_text(encoding="utf-8"))["format"])
    except (OSError, ValueError, KeyError, TypeError):  # noqa: NDF401 ファイル: 無い・読めない format.json は番号の無い置き場として退避する
        return 0


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


def _write_format(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / FORMAT_FILE).write_text(json.dumps({"format": STORE_FORMAT}) + "\n", encoding="utf-8")


def _retire_store(root: Path, found: int, now: datetime) -> Path:
    """旧い置き場の鍵のロックと進行表の DB のロックをすべて待たずに取り、名前を変えて退避する（I11）。"""
    old_locks = sorted(_dbos_dir(root).glob(f"*{LOCK_SUFFIX}")) + [p for p in (root / PROGRESS_LOCK,) if p.exists()]
    with ExitStack() as stack:
        busy = []
        for path in old_locks:
            handle = stack.enter_context(open(path, "a"))
            if not _try_flock(handle, fcntl.LOCK_EX):
                busy.append(path)
        if busy:
            raise StoreInUse(f"旧い置き場をほかのプロセスが使っている: {', '.join(map(str, busy))}", tuple(busy))
        target = _free_name(root.with_name(f"{root.name}.v{found}-{now:%Y%m%d}"), now)
        root.rename(target)
    _write_format(root)
    return target


def _hold_shared(root: Path) -> IO[str]:
    handle = _open_lock_file(_store_lock_path(root))
    fcntl.flock(handle, fcntl.LOCK_SH)
    return handle


def _refuse_newer(root: Path, found: int | None) -> None:
    if found is not None and found > STORE_FORMAT:
        raise StoreFormatNewer(f"置き場の形の番号 {found} が自分の {STORE_FORMAT} より新しい: {root}", (root / FORMAT_FILE,))


def _align_store(root: Path, now: datetime) -> Path | None:
    """置き場の排他を待たずに取り、番号を読み直して、無ければ作り、古ければ退避する。退避した先を返す。"""
    with store_lock(root, exclusive=True):
        found = _read_format(root)
        _refuse_newer(root, found)
        if found is None:
            _write_format(root)
            return None
        if found == STORE_FORMAT:
            return None
        return _retire_store(root, found, now)


def prepare_store(root: Path, now: datetime | None = None) -> StoreReady:
    """置き場の形の番号を確かめて用意し、置き場のロックを共有で持ったまま返す（プロセスが終わるまで放さない）。

    番号が無い・古い置き場は、共有を放して排他を待たずに取り、排他の下で番号を読み直してから退避する。
    共有で取り直して番号をもう一度確かめ、違えばやり直す（決定 85・I10・I11）。
    """
    now = now or datetime.now(UTC)
    if root in _held_stores:
        return StoreReady(root)
    retired_to: Path | None = None
    pending_count = 0
    busy: tuple[Path, ...] = (_store_lock_path(root),)
    attempt = 0
    while True:
        if _hold_if_current(root):
            return StoreReady(root, retired_to, pending_count)
        if attempt >= PREPARE_ATTEMPTS:
            raise StoreInUse(f"置き場の形の番号をそろえられない（ほかのプロセスが使っている）: {root}", busy)
        attempt += 1
        try:
            moved = _align_store(root, now)
        except StoreInUse as exc:
            busy = exc.paths
            time.sleep(0.05 * attempt)
            continue
        if moved is not None:
            retired_to = moved
            pending_count = _report_retired(root, moved)


def _hold_if_current(root: Path) -> bool:
    """置き場のロックを共有で取り、番号が自分と同じなら持ったまま真を返す。違えば放し、新しすぎれば `StoreFormatNewer`。"""
    shared = _hold_shared(root)
    found = _read_format(root)
    if found == STORE_FORMAT:
        _held_stores[root] = shared
        return True
    shared.close()
    _refuse_newer(root, found)
    return False


def _report_retired(root: Path, moved: Path) -> int:
    """退避した置き場で待ちを抱えていた実行を数え、ログに残して数を返す。"""
    pending_count = len(_pending_in(moved))
    log.warning("旧い置き場を退避した", extra={"from": str(root), "to": str(moved), "pending": pending_count})
    return pending_count


def release_store(root: Path) -> None:
    """`prepare_store` が持った置き場の共有のロックを放す（テストと、置き場を使い終えたプロセスが呼ぶ）。"""
    handle = _held_stores.pop(root, None)
    if handle is not None:
        handle.close()


# --- 鍵ごとのファイルの検査と退避（決定 82・83） -----------------------------------------


def _fresh(mark: Path, now: datetime) -> bool:
    try:
        return now.timestamp() - mark.stat().st_mtime < CHECK_INTERVAL.total_seconds()
    except FileNotFoundError:  # noqa: NDF401 ファイル: 目印が無ければ古いとして扱う
        return False


def _touch(mark: Path, now: datetime) -> None:
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.touch()
    os.utime(mark, (now.timestamp(), now.timestamp()))


def check_file(path: Path) -> bool:
    """SQLite のファイルに `PRAGMA quick_check` を打ち、1 行の `ok` が返れば真。ファイルが無ければ真。

    読み取り専用で開く。書ける接続で開いて閉じると、SQLite が壊れたファイルの `-wal` を消し、退避する証拠が欠ける。
    """
    if not path.exists():
        return True
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        try:
            return conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        finally:
            conn.close()
    except sqlite3.DatabaseError:  # noqa: NDF401 ファイル: 開けない・検査を通らないファイルは壊れているとして扱う
        return False


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


def _check_key_file(path: Path, now: datetime) -> None:
    """鍵ごとの目印が古いか無ければ `quick_check` を打つ。通らなければ broken/ へ移して `StoreBroken`（A4）。"""
    mark = path.with_suffix(CHECKED_SUFFIX)
    if _fresh(mark, now):
        return
    if check_file(path):
        _touch(mark, now)
        return
    moved = _move_out(path, path.parent / BROKEN_DIR, f"{path.stem}-{now:%Y%m%dT%H%M%S}{DB_SUFFIX}", now)
    raise StoreBroken(f"耐久の記録のファイルが壊れていたので退避した: {moved}", (moved,))


@contextmanager
def _client(database_url: str) -> Iterator[DBOSClient]:
    """DBOS を起こさずに記録を扱うクライアントを作り、抜けるときに破棄する。

    接続が切れたら待たずに例外にする（DBOS の既定は戻るまで待ち続け、片付けが `maint.lock` と鍵のロックを持ったまま止まる）。
    """
    client = DBOSClient(system_database_url=database_url, retry_connection_errors=False)
    try:
        yield client
    finally:
        client.destroy()


def _records(database_url: str, names: list[str] | None, statuses: list[str] | None = None) -> list[RunRecord]:
    """DBOS を起こさずに、`names`（None ならすべての鍵）の耐久ワークフローの記録を読む。

    DBOS の絞り込みは持ち主の無い（`application_name` が NULL の）行も返すため、`names` を渡したときは除く。
    """
    with _client(database_url) as client:
        found = client.list_workflows(application_name=names, status=statuses, load_input=False, load_output=False)
    return [
        RunRecord(
            run_name=w.application_name,
            workflow_id=w.workflow_id,
            status=w.status,
            updated_at=None if w.updated_at is None else datetime.fromtimestamp(w.updated_at / 1000, UTC),
            parent_workflow_id=w.parent_workflow_id,
            app_version=w.app_version,
        )
        for w in found
        if names is None or w.application_name in names
    ]


def workflows(database_url: str, run_key: str | None = None) -> list[RunRecord]:
    """DBOS を起こさずに、鍵 `run_key`（None ならすべて）の耐久ワークフロー（子を含む）の記録を読む。"""
    return _records(database_url, None if run_key is None else [run_name(run_key)])


def _retire_name(root: Path, name: str, now: datetime) -> Path | None:
    body = _dbos_dir(root) / f"{name}{DB_SUFFIX}"
    if not body.exists():
        _drop_marks(body)
        return None
    if any(r.status not in FINISHED for r in _records(_sqlite_url(body), None)):
        return None
    conn = sqlite3.connect(body, timeout=30)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return _move_out(body, _dbos_dir(root) / RETIRED_DIR, body.name, now)


def retire(root: Path, run_key: str, now: datetime | None = None) -> Path | None:
    """鍵のファイルの耐久ワークフローがすべて終わっていれば retired/ へ移し、移した先を返す。待ちがあれば何もしない。

    呼び手が `run_lock` を持ち、DBOS を閉じた後に呼ぶ。
    """
    return _retire_name(root, run_name(run_key), now or datetime.now(UTC))


@contextmanager
def _sweep_guard(root: Path, now: datetime) -> Iterator[bool]:
    """`maint_lock` を待たずに取り、目印 `dbos/.swept` を読み直して古ければ真を渡し、抜けるときに目印を更新する。"""
    with maint_lock(root, wait=False) as held:
        mark = _dbos_dir(root) / SWEPT_MARK
        if held is None or _fresh(mark, now):
            yield False
            return
        yield True
        _touch(mark, now)


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


def sweep(root: Path, now: datetime | None = None) -> SweepResult:
    """終わった鍵のファイルを retired/ へ移し、retired/・broken/ の `KEEP_DAYS` より古いファイルを消す（決定 83・A5）。

    24 時間に 1 回だけ走査する。開いているプロセスがいる鍵（`run_lock` を待たずに取れない鍵）は触らない。
    """

    def body(now: datetime, out: SweepResult) -> None:
        dbos_dir = _dbos_dir(root)
        if dbos_dir.is_dir():
            _sweep_live(root, now, out)
            _sweep_kept(dbos_dir, now, out)

    return _guarded_sweep(root, now, body, "片付けで読めないファイルがあった")


def _sweep_live(root: Path, now: datetime, out: SweepResult) -> None:
    _retire_finished(root, now, out)
    _drop_orphans(root)


def _retire_finished(root: Path, now: datetime, out: SweepResult) -> None:
    """開いているプロセスのいない鍵のファイルのうち、耐久ワークフローがすべて終わったものを retired/ へ移す。"""
    for body in sorted(_dbos_dir(root).glob(f"*{DB_SUFFIX}")):
        with _file_run_lock(root, body.stem, wait=False) as held:
            if held is None:
                out.busy += 1
                continue
            try:
                moved = _retire_name(root, body.stem, now)
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
        with _file_run_lock(root, lock.stem, wait=False) as held:
            if held is not None:
                lock.unlink(missing_ok=True)


def _sweep_kept(dbos_dir: Path, now: datetime, out: SweepResult) -> None:
    cutoff = (now - timedelta(days=KEEP_DAYS)).timestamp()
    for kept in (dbos_dir / RETIRED_DIR, dbos_dir / BROKEN_DIR):
        if not kept.is_dir():
            continue
        for path in sorted(kept.iterdir()):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                out.deleted.append(str(path))


# --- URL（PostgreSQL）の台帳と片付け（決定 83・I12・I13） ---------------------------------


def _runs_table(metadata: sa.MetaData) -> sa.Table:
    return sa.Table(
        RUNS_TABLE,
        metadata,
        sa.Column("run_name", sa.Text, primary_key=True),
        sa.Column("run_key", sa.Text, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    )


def _ensure_runs_table(engine: sa.Engine, table: sa.Table) -> None:
    """台帳の表を作る。別の鍵の起動が同時に作ると片方が重複で落ちるため、落ちても表があれば続ける。"""
    try:
        with engine.begin() as conn:
            table.create(conn, checkfirst=True)
    except sa.exc.DBAPIError:
        with engine.connect() as conn:
            if not sa.inspect(conn).has_table(RUNS_TABLE):
                raise


def _register_run(database_url: str, name: str, run_key: str, now: datetime) -> None:
    """台帳 `ndf_runs` へ鍵を 1 行書く（既にあれば書かない）。書けなければ `StoreBroken`。"""
    with _engine(database_url) as engine:
        table = _runs_table(sa.MetaData())
        try:
            _ensure_runs_table(engine, table)
            with engine.begin() as conn:
                _insert_run(conn, table, name, run_key, now)
        except sa.exc.SQLAlchemyError as exc:
            raise StoreBroken(f"台帳 {RUNS_TABLE} に鍵を書けない: {exc}") from exc


def _insert_run(conn: sa.Connection, table: sa.Table, name: str, run_key: str, now: datetime) -> None:
    """台帳に鍵 `name` の行が無ければ足す。"""
    if conn.execute(sa.select(table.c.run_name).where(table.c.run_name == name)).first() is None:
        conn.execute(table.insert().values(run_name=name, run_key=run_key, started_at=now))


def _ledger(database_url: str) -> list[LedgerRow]:
    """台帳の行（鍵の名前・実行の鍵・起こした時刻）。表が無ければ空。"""
    table = _runs_table(sa.MetaData())
    with _engine(database_url) as engine, engine.connect() as conn:
        if not sa.inspect(conn).has_table(RUNS_TABLE):
            return []
        return [LedgerRow(*row) for row in conn.execute(sa.select(table))]


def _forget_run(database_url: str, name: str) -> None:
    table = _runs_table(sa.MetaData())
    with _engine(database_url) as engine, engine.begin() as conn:
        conn.execute(table.delete().where(table.c.run_name == name))


def _expired(records: list[RunRecord], started_at: datetime, cutoff: datetime) -> bool:
    """鍵の実行がすべて終わり、どれも `cutoff` より前に終わったか。実行が無ければ台帳の時刻で決める。"""
    if not records:
        return started_at < cutoff
    return all(r.status in FINISHED and r.updated_at is not None and r.updated_at < cutoff for r in records)


def sweep_url(root: Path, database_url: str, now: datetime | None = None) -> SweepResult:
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
                _sweep_url_run(database_url, row.run_name, row.started_at, cutoff, out)
            except Exception as exc:  # noqa: BLE001 ネットワーク: 1 つの鍵を片付けられなくても止めず、warnings に載せる
                out.warnings.append(f"{row.run_name}: {exc}")

    return _guarded_sweep(root, now, body, "URL の片付けで読めない鍵があった")


def _sweep_url_run(database_url: str, name: str, started_at: datetime, cutoff: datetime, out: SweepResult) -> None:
    """鍵 1 つを、DB 側の鍵のロックを待たずに取れたときだけ、すべて終わって日数が過ぎていれば消す。"""
    with _pg_run_lock(database_url, name, wait=False) as held:
        if held is None:
            out.busy += 1
            return
        records = _records(database_url, [name])
        if not _expired(records, started_at, cutoff):
            return
        ids = [r.workflow_id for r in records]
        if ids:
            with _client(database_url) as client:
                client.delete_workflows(ids, delete_children=False)
        _forget_run(database_url, name)
        out.deleted.extend(ids)


# --- 待ちを抱えた実行の一覧（決定 86） ---------------------------------------------------


def _pending_in(root: Path) -> list[RunRecord]:
    found: list[RunRecord] = []
    unreadable: list[str] = []
    for body in sorted(_dbos_dir(root).glob(f"*{DB_SUFFIX}")):
        try:
            records = _records(_sqlite_url(body), None, list(WAITING))
        except Exception as exc:  # noqa: BLE001 ファイル: 読めないファイルは数えず、件数をまとめてログに残して残りを集める
            unreadable.append(f"{body}: {exc}")
            continue
        found.extend(replace(r, run_name=r.run_name or body.stem) for r in records)
    if unreadable:
        log.warning("待ちの一覧で読めないファイルがあった", extra={"count": len(unreadable), "sample": unreadable[:10]})
    return found


def pending(root_or_url: Path | str) -> list[RunRecord]:
    """待ちを抱えた（`PENDING`・`ENQUEUED`・`DELAYED`）実行を集める。置き場なら鍵ごとのファイル、URL なら台帳の鍵から。"""
    if isinstance(root_or_url, Path):
        return _pending_in(root_or_url)
    names = [row.run_name for row in _ledger(root_or_url)]
    return _records(root_or_url, names, list(WAITING)) if names else []


# --- 起動と停止 -------------------------------------------------------------------------


def launch(database_url: str, run_key: str) -> None:
    """DBOS を実行の鍵の名前と形の番号の版で起こし、同じ鍵の途中の耐久ワークフローを回復する（決定 81・84）。

    呼び手は `run_lock(root, run_key, database_url)` を持った状態で呼び、DBOS を閉じるまで持つ。
    SQLite のときは鍵ごとの目印が古ければ先に `quick_check` を打ち、壊れていれば broken/ へ移して `StoreBroken`。
    URL のときは先に台帳 `ndf_runs` へ鍵を書き、書けなければ起こさない。
    耐久ワークフローと耐久ステップの関数は、呼ぶ前に定義しておく（回復が名前で引くため）。
    """
    now = datetime.now(UTC)
    name = run_name(run_key)
    path = _sqlite_path(database_url)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _check_key_file(path, now)
    else:
        _register_run(database_url, name, run_key, now)
    config: DBOSConfig = {
        "name": name,
        "system_database_url": database_url,
        "executor_id": name,
        "application_version": app_version(run_key),
        "log_level": LOG_LEVEL,
    }
    DBOS(config=config)
    DBOS.launch()


def shutdown() -> None:
    """DBOS を止め、走っていた耐久ワークフローが結果を記録し終えてから閉じる。定義は残す。

    `DBOS.destroy` は走っているスレッドを待たない（`workflow_completion_timeout_sec` の既定は 0 で、
    executor を `wait=False` で閉じ、記録の DB の口 `_sys_db_field` を None にする）。先に閉じると、
    spawn した子や例外で残ったステップは副作用を終えても結果を記録できず、次の回復で同じ副作用を流し直す。
    そこで、キューの取り出しと回復の裏のスレッドを止めて新しい実行を始めさせず、走っている耐久ワークフローが
    すべて記録を終える（DBOS の走っている一覧から外れる）まで待ってから閉じる。閉じた後も executor の
    スレッドを join し、呼び手が鍵のロックを放す前に、一覧へ載る前だったスレッドも終わらせる。
    待つ時間は、待たなくてもプロセスの終わりで executor のスレッドを join する時間と同じである。
    `workflow_completion_timeout_sec` は秒の上限で 1 秒ごとに見るため使わない（上限を越えると先に閉じ、
    走っていなくても 1 秒待つ）。DBOS 3.4.0 は executor と走っている一覧の公開の取り出し口を持たない。
    """
    instance = getattr(_dbos_runtime, "_dbos_global_instance", None)
    executor: ThreadPoolExecutor | None = getattr(instance, "_executor_field", None)
    if instance is not None:
        for event in instance.background_thread_stop_events:
            event.set()
        while instance._active_workflows_set.activeList():
            time.sleep(SHUTDOWN_POLL_SECONDS)
    DBOS.destroy(destroy_registry=False)
    if executor is not None:
        executor.shutdown(wait=True)


@contextmanager
def session(root: Path, run_key: str, database_url: str | None = None) -> Iterator[SweepResult]:
    """1 つの実行の鍵で DBOS を使う区間。置き場を用意し、鍵のロックを取り、片付けてから起こす。

    抜けるときに DBOS を閉じ、SQLite なら鍵の耐久ワークフローがすべて終わっていれば retired/ へ移す。
    渡す値は起動の前の片付けの結果。
    """
    prepare_store(root)
    url = database_url or run_url(root, run_key)
    sqlite = _is_sqlite(url)
    with run_lock(root, run_key, database_url) as _held:
        swept = sweep(root) if sqlite else sweep_url(root, url)
        launch(url, run_key)
        try:
            yield swept
        finally:
            shutdown()
        if sqlite:
            retire(root, run_key)


def cancel_before_launch(database_url: str, workflow_id: str) -> None:
    """DBOS を起こさずに耐久ワークフローとその子を取り消す。

    `launch` の前に呼ぶ。起こした後に取り消しても、回復で流れ始めた耐久ステップは止まらない。
    """
    with _client(database_url) as client:
        client.cancel_workflow(workflow_id, cancel_children=True)
