"""DBOS の殻。DBOS を import するのはこのモジュールだけである。

耐久ワークフローと耐久ステップを使う層は、DBOS の型と関数をここから受ける。判断の規則はここに置かず、
`ndf.core.loop` の判断の関数に置く。

DBOS は起こすか記録を読むときに初めて import する（`_load`。約 0.3 秒の CPU を DBOS を開かない呼び出しで使わない。B3）。
先に付けた `workflow`・`step` は `_Deferred` が覚え、`launch` の前の `_load` が DBOS のデコレータを当てる。

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
`run_lock` を待たずに試すだけにする。待つロックはすべて `LOCK_WAIT_SECONDS` を上限にし、当たれば持ち主
（排他で持ったプロセスがロックのファイルに書いたホスト・pid・時刻、PostgreSQL では接続の `application_name`）を
添えて `StoreInUse` で止まる。PostgreSQL の接続は keepalive を持ち、NDF が開く接続は idle_session_timeout も持つ。

耐久の記録へ書く値（ワークフローの入力・ステップの出力・メッセージ・例外）は、serializer（`_RedactingSerializer`）が
秘密の環境変数の値とトークンの形を伏せてから書く。規則は `ndf.adapters.run_log.redact` と同じものを使う。

DBOS の版と表の版（SQLite の `dbos_migrations.version`）は、置き場の `format.json` に形の番号と並べて書く。
表の版が古い鍵のファイルは、DBOS が一方向に移す前に `dbos/backup/<鍵の名前>-schema<版>.sqlite` へ控えを取る。
控えは `KEEP_DAYS` で消える。DBOS の版を戻す手順:

1. 戻す鍵を使うプロセス（`ndf plan next`・`queue`）を止める。`ndf plan pending` で待ちを抱えた鍵を確かめる
2. `pyproject.toml` と `uv.lock` の DBOS を前の版へ戻す
3. 戻す鍵のファイル `dbos/<鍵の名前>.sqlite` と `-wal`・`-shm` を消し、控えを `dbos/<鍵の名前>.sqlite` の名前で置く
4. 次の `ndf plan next` が前の版で控えの時点から回復する。控えの後に進んだステップは流し直しになる
5. PostgreSQL の URL では控えを取らない。DBOS の版を上げる前に `pg_dump` で DB の控えを取り、戻すときは `pg_restore` で戻す
"""

from __future__ import annotations

import copy
import fcntl
import functools
import hashlib
import importlib.metadata
import json
import logging
import os
import sqlite3
import subprocess
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, TypeVar

from ndf.adapters import run_log
from ndf.contracts import limits
from ndf.core import durable_locks as locks
from ndf.core import durable_sweep as sweeping
from ndf.core.durable_ledger import (
    RUNS_TABLE as RUNS_TABLE,
    LedgerRow as LedgerRow,
    _forget_run as _forget_run,
    _ledger as _ledger,
    _register_run as _register_run,
)
from ndf.core.durable_locks import (
    MAINT_LOCK as MAINT_LOCK,
    RUN_LOCK_SPACE as RUN_LOCK_SPACE,
    SQLITE_PREFIX as SQLITE_PREFIX,
    STORE_LOCK as STORE_LOCK,
    StoreBroken,
    StoreConfigBroken as StoreConfigBroken,
    StoreFormatNewer,
    StoreInUse,
    StoreStopped as StoreStopped,
    maint_lock as maint_lock,
    open_lock_file as open_lock_file,
    sa as sa,
    sa_url as sa_url,
    store_lock as store_lock,
)
from ndf.core.durable_sweep import (
    BACKUP_DIR as BACKUP_DIR,
    BROKEN_DIR as BROKEN_DIR,
    CHECK_INTERVAL as CHECK_INTERVAL,
    CHECKED_SUFFIX as CHECKED_SUFFIX,
    DB_SUFFIX as DB_SUFFIX,
    DBOS_DIR as DBOS_DIR,
    FINISHED as FINISHED,
    KEEP_DAYS as KEEP_DAYS,
    LOCK_SUFFIX as LOCK_SUFFIX,
    RETIRED_DIR as RETIRED_DIR,
    SIDE_SUFFIXES as SIDE_SUFFIXES,
    SWEPT_MARK as SWEPT_MARK,
    SweepResult as SweepResult,
    _dbos_dir,
    _free_name,
    _move_out,
    is_fresh as is_fresh,
    touch_mark as touch_mark,
)

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
# 鍵の名前の接頭辞。名前の形を決めるだけで、URL の片付けの絞り込みには使わない（絞るのは台帳）
RUN_PREFIX = "run-"
# DBOS を閉じる前に、走っている耐久ワークフローが記録を終えたかを見る間隔（秒）
SHUTDOWN_POLL_SECONDS = 0.05
# 耐久の記録への問い合わせの間隔の宣言。DBOS の通知の見回り（DBOS の既定は 1 秒）にも当てる。閉じるときに見回りの
# スレッドの眠りの残りを待つため、既定のままでは 1 回の起動ごとに最大 1 秒が上乗せになる（B3）。
# 見回りは待っている recv が無ければ DB を読まない
POLL = "durable.poll"

PROGRESS_LOCK = "progress.sqlite.lock"
WAITING = ("PENDING", "ENQUEUED", "DELAYED")
# 置き場の退避で番号を読み直す回数の上限（決定 85 の手順の (5)）
PREPARE_ATTEMPTS = 3
# 置き場の format.json に並べる DBOS の記録の鍵（B12）。記録の置き場（durable_sweep.DBOS_DIR）と同じ名前を使う
FORMAT_DBOS_KEY = DBOS_DIR
# 耐久の記録に書く値の serializer の名前。DBOS の既定（py_pickle）と別の名前にしないと、DBOS が既定の serializer を使う
SERIALIZER_NAME = "ndf_redacted_pickle"
# 入れてある DBOS の版と、SQLite の表の版（DBOS が `dbos_migrations.version` に書く、移行の数）
DBOS_VERSION = importlib.metadata.version("dbos")


@dataclass(frozen=True)
class StoreReady:
    """用意した置き場。`retired_to` は退避した旧い置き場、`pending_count` はそこで待ちを抱えていた実行の数。"""

    root: Path
    retired_to: Path | None = None
    pending_count: int = 0


@dataclass(frozen=True)
class RunRecord:
    """耐久ワークフロー 1 つの記録。`run_name` は持ち主の鍵の名前（持ち主の無い行は None）、`updated_at` は最後に状態が変わった時刻、`app_version` は起こしたときのアプリの版。"""

    run_name: str | None
    workflow_id: str
    status: str
    updated_at: datetime | None
    parent_workflow_id: str | None = None
    app_version: str | None = None


# 置き場のロックを共有で持ち続けるファイル（プロセスが終わるまで放さない）
_held_stores: dict[Path, IO[str]] = {}


# --- 耐久ワークフローと耐久ステップ -------------------------------------------------------


def _redact(value: Any, secrets: list[str]) -> Any:
    """入れ子の文字列・辞書・配列・タプル・例外の引数の中の秘密を伏せる。型（タプル・名前つきタプル・例外）は保つ。

    型ごとの伏せ方は `_REDACTORS` の並びの順に isinstance で引く（子の型も親の型の伏せ方で伏せる）。どれにも当たらなければそのまま。
    """
    for kind, redact in _REDACTORS:
        if isinstance(value, kind):
            return redact(value, secrets)
    return value


def _redact_tuple(value: tuple, secrets: list[str]) -> tuple:
    items = [_redact(v, secrets) for v in value]
    return type(value)(*items) if hasattr(value, "_fields") else tuple(items)


def _redact_exception(value: BaseException, secrets: list[str]) -> BaseException:
    masked = copy.copy(value)
    masked.args = _redact(value.args, secrets)
    return masked


_REDACTORS: tuple[tuple[type, Callable[[Any, list[str]], Any]], ...] = (
    (str, run_log.redact),
    (dict, lambda value, secrets: {k: _redact(v, secrets) for k, v in value.items()}),
    (list, lambda value, secrets: [_redact(v, secrets) for v in value]),
    (tuple, _redact_tuple),
    (BaseException, _redact_exception),
)


def _serializer_class(base: type) -> type:
    class _RedactingSerializer(base):  # type: ignore[valid-type,misc]
        """耐久の記録（ワークフローの入力・ステップの出力・メッセージ・例外）へ書く値の秘密を伏せてから pickle にする（B8）。

        DBOS が記録へ書く値はすべてここを通るため、殻の 1 か所で伏せる。伏せるのは記録だけで、流れている呼び出しへは
        元の値が渡る。回復で記録から読んだ値は伏せた後の値になるので、秘密はワークフローの入力にせず、ステップの中で
        環境変数から読む。読むのは既定の pickle と同じ形。
        """

        def serialize(self, data: Any) -> str:
            return str(super().serialize(_redact(data, run_log.secret_values())))

        def name(self) -> str:
            return SERIALIZER_NAME

    return _RedactingSerializer


class _Deferred:
    """DBOS を読む前に付けた `workflow`・`step`。`_load` が DBOS のデコレータを当て、呼ぶとそれへ渡す。"""

    def __init__(self, fn: Callable[..., Any], kind: str) -> None:
        functools.update_wrapper(self, fn)
        self.fn = fn
        self.kind = kind
        self.real: Callable[..., Any] | None = None

    def bind(self) -> None:
        if self.real is None:
            self.real = getattr(DBOS, self.kind)()(self.fn)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        _load()
        assert self.real is not None
        return self.real(*args, **kwargs)


_deferred: list[_Deferred] = []
# `_load` が結ぶ名前。モジュールの属性として読まれたときも `__getattr__` が `_load` を呼んでから返す
_LAZY_NAMES = frozenset({"DBOS", "DBOSClient", "SetEnqueueOptions", "SetWorkflowID", "_dbos_runtime", "DBOS_SCHEMA"})
_loaded = False

if TYPE_CHECKING:
    import dbos._dbos as _dbos_runtime
    from dbos import DBOS, DBOSClient, DBOSConfig, SetEnqueueOptions, SetWorkflowID


def _load() -> None:
    """DBOS を import して名前をこのモジュールに結び、先に付けた `workflow`・`step` に DBOS のデコレータを当てる（1 回だけ）。"""
    global _loaded, _dbos_runtime, DBOS, DBOSClient, SetEnqueueOptions, SetWorkflowID, DBOS_SCHEMA, _serializer_type
    if _loaded:
        return
    import dbos._dbos as _dbos_runtime
    import dbos._migration as migration
    from dbos import DBOS, DBOSClient, SetEnqueueOptions, SetWorkflowID
    from dbos._serialization import DefaultSerializer

    # 入れてある DBOS の SQLite の表の版（DBOS が `dbos_migrations.version` に書く、移行の数）と、記録へ書く serializer の型
    DBOS_SCHEMA = len(migration.sqlite_migrations)
    _serializer_type = _serializer_class(DefaultSerializer)
    _loaded = True
    for item in _deferred:
        item.bind()


def _schema() -> int:
    _load()
    return int(globals()["DBOS_SCHEMA"])


def _serializer() -> Any:
    """秘密を伏せてから記録へ書く serializer（B8）。"""
    _load()
    return globals()["_serializer_type"]()


def __getattr__(name: str) -> Any:
    if name in _LAZY_NAMES:
        _load()
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _decorate(fn: Callable[..., R], kind: str) -> Callable[..., R]:
    if _loaded:
        return getattr(DBOS, kind)()(fn)  # type: ignore[no-any-return]
    item = _Deferred(fn, kind)
    _deferred.append(item)
    return item


def _target(fn: Callable[..., Any]) -> Callable[..., Any]:
    """DBOS へ渡す関数（先に付けたデコレータなら、DBOS のデコレータを当てた方）。"""
    _load()
    return fn.real if isinstance(fn, _Deferred) and fn.real is not None else fn


def workflow(fn: Callable[..., R]) -> Callable[..., R]:
    """関数を耐久ワークフローにする。中で呼んだ耐久ステップの結果が記録に残る。"""
    return _decorate(fn, "workflow")


def step(fn: Callable[..., R]) -> Callable[..., R]:
    """関数を耐久ステップにする。済んだ呼び出しは回復のときに流し直さず、記録した結果を返す。"""
    return _decorate(fn, "step")


def start(fn: Callable[..., R], workflow_id: str, *args: Any) -> R:
    """ID を決めて耐久ワークフローを流し、結果を返す。同じ ID が終わっていれば、記録した結果を返す。"""
    real = _target(fn)
    with SetWorkflowID(workflow_id):
        return real(*args)  # type: ignore[no-any-return]


def spawn(fn: Callable[..., Any], workflow_id: str, *args: Any) -> None:
    """ID を決めて耐久ワークフローを裏で流し、終わりを待たずに戻る。耐久ワークフローの中で呼ぶと子になる。"""
    real = _target(fn)
    with SetWorkflowID(workflow_id):
        DBOS.start_workflow(real, *args)


def queue(name: str) -> Any:
    """キューを登録して返す（DBOS を起こした後）。キューは鍵の `application_name` の持ち物になる。"""
    _load()
    return DBOS.register_queue(name)


def enqueue(target: Any, fn: Callable[..., Any], workflow_id: str, *args: Any, delay_seconds: float | None = None) -> None:
    """耐久ワークフローをキュー `target` へ入れる。`delay_seconds` を渡すと、その秒数の後に取り出せるようになる。"""
    real = _target(fn)
    with SetWorkflowID(workflow_id), SetEnqueueOptions(delay_seconds=delay_seconds):
        target.enqueue(real, *args)


def result(workflow_id: str) -> Any:
    """耐久ワークフローが終わるまで待ち、結果を返す（回復で流れている途中のものも待つ）。"""
    _load()
    return DBOS.retrieve_workflow(workflow_id).get_result()


def status(workflow_id: str) -> str | None:
    """耐久ワークフローの状態（`PENDING`・`SUCCESS`・`ERROR`・`CANCELLED` など）。記録に無ければ None。"""
    _load()
    found = DBOS.get_workflow_status(workflow_id)
    return None if found is None else found.status


def current_id() -> str | None:
    """流れている耐久ワークフローの ID。耐久ワークフローの外では None。"""
    _load()
    return DBOS.workflow_id


def send(workflow_id: str, message: Any, topic: str) -> None:
    """耐久ワークフローへ話題 `topic` のメッセージを送る（DBOS を起こした後）。"""
    _load()
    DBOS.send(workflow_id, message, topic)


def recv(topic: str, timeout_seconds: float) -> Any:
    """話題 `topic` のメッセージを待つ。期限までに来なければ None。耐久ワークフローの中でだけ呼ぶ。"""
    _load()
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
    return locks.sqlite_url(_dbos_dir(root) / f"{run_name(run_key)}{DB_SUFFIX}")


# --- ロック -----------------------------------------------------------------------------


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


@contextmanager
def run_lock(root: Path, run_key: str, database_url: str | None = None, wait: bool = True) -> Iterator[Any]:
    """実行の鍵の排他。SQLite は `dbos/<鍵の名前>.lock` の flock、PostgreSQL は DB 側の advisory lock。

    `wait` が偽で取れなければ None を渡す。呼び手は DBOS を起こす前に取り、閉じて退避するまで持つ（I6・I13）。
    """
    name = run_name(run_key)
    if database_url is not None and not locks.is_sqlite(database_url):
        with locks.pg_run_lock(database_url, name, wait) as held:
            yield held
    else:
        with locks.file_run_lock(_dbos_dir(root) / f"{name}{LOCK_SUFFIX}", wait) as held:
            yield held


# --- チェックアウトの置き場（決定 85） ---------------------------------------------------


def _read_format(root: Path) -> int | None:
    """置き場の形の番号。置き場が無いか空なら None、`format.json` が無くほかのファイルがあれば 0。"""
    if not root.is_dir() or not any(root.iterdir()):
        return None
    try:
        return int(json.loads((root / FORMAT_FILE).read_text(encoding="utf-8"))["format"])
    except (OSError, ValueError, KeyError, TypeError):  # noqa: NDF401 ファイル: 無い・読めない format.json は番号の無い置き場として退避する
        return 0


def _write_format(root: Path) -> None:
    """`format.json` に置き場の形の番号と、使う DBOS の版と表の版を書く（一時の名前から置き換える。B12）。"""
    root.mkdir(parents=True, exist_ok=True)
    body = {"format": STORE_FORMAT, FORMAT_DBOS_KEY: _dbos_record()}
    tmp = root / f".{FORMAT_FILE}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(body) + "\n", encoding="utf-8")
    os.replace(tmp, root / FORMAT_FILE)


def _dbos_record() -> dict[str, Any]:
    _load()
    return {"version": DBOS_VERSION, "schema": _schema()}


def _record_dbos(root: Path) -> None:
    """`format.json` の DBOS の版と表の版が今のものと違えば書き直す。番号は変えない。"""
    try:
        found = json.loads((root / FORMAT_FILE).read_text(encoding="utf-8")).get(FORMAT_DBOS_KEY)
    except (OSError, ValueError, AttributeError):
        found = None
    if found != _dbos_record():
        _write_format(root)


def _retire_store(root: Path, found: int, now: datetime) -> Path:
    """旧い置き場の鍵のロックと進行表の DB のロックをすべて待たずに取り、名前を変えて退避する（I11）。"""
    old_locks = sorted(_dbos_dir(root).glob(f"*{LOCK_SUFFIX}")) + [p for p in (root / PROGRESS_LOCK,) if p.exists()]
    with ExitStack() as stack:
        busy = []
        for path in old_locks:
            handle = stack.enter_context(open(path, "a"))
            if not locks.try_flock(handle, fcntl.LOCK_EX):
                busy.append(path)
        if busy:
            raise StoreInUse(f"旧い置き場をほかのプロセスが使っている: {', '.join(map(str, busy))}", tuple(busy))
        target = _free_name(root.with_name(f"{root.name}.v{found}-{now:%Y%m%d}"), now)
        root.rename(target)
    _write_format(root)
    return target


def _hold_shared(root: Path) -> IO[str]:
    path = locks.store_lock_path(root)
    handle = locks.open_lock_file(path)
    try:
        locks.lock(handle, fcntl.LOCK_SH, True, path)
    except StoreInUse:
        handle.close()
        raise
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
    busy: tuple[Path, ...] = (locks.store_lock_path(root),)
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
    """置き場のロックを共有で取り、番号が自分と同じなら持ったまま真を返す。違えば放し、新しすぎれば `StoreFormatNewer`。

    持ったときは、`format.json` の DBOS の版と表の版の記録を今のものにそろえる（B12）。
    """
    shared = _hold_shared(root)
    found = _read_format(root)
    if found == STORE_FORMAT:
        _held_stores[root] = shared
        _record_dbos(root)
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


def _check_key_file(path: Path, now: datetime) -> None:
    """鍵ごとの目印が古いか無ければ `quick_check` を打つ。通らなければ broken/ へ移して `StoreBroken`（A4）。"""
    mark = path.with_suffix(CHECKED_SUFFIX)
    if is_fresh(mark, now):
        return
    if check_file(path):
        touch_mark(mark, now)
        return
    moved = _move_out(path, path.parent / BROKEN_DIR, f"{path.stem}-{now:%Y%m%dT%H%M%S}{DB_SUFFIX}", now)
    raise StoreBroken(f"耐久の記録のファイルが壊れていたので退避した: {moved}", (moved,))


def schema_of(path: Path) -> int | None:
    """鍵のファイルの表の版（`dbos_migrations.version`）。ファイルが無ければ None、表が無ければ 0。"""
    if not path.exists():
        return None
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        row = conn.execute("SELECT version FROM dbos_migrations").fetchone()
    except sqlite3.OperationalError:  # noqa: NDF401 ファイル: 表の無いファイルは移行の前（版 0）として扱う
        return 0
    finally:
        conn.close()
    return int(row[0]) if row else 0


def _backup_before_migration(path: Path, now: datetime) -> Path | None:
    """鍵のファイルの表の版が今の DBOS より古ければ、DBOS が移行する前に控えを取って控えのパスを返す（B12）。

    新しければ開かずに `StoreFormatNewer`（古い DBOS は新しい表を読めない）。同じか、ファイル・表が無ければ None。
    """
    found = schema_of(path)
    schema = _schema()
    if not found or found == schema:
        return None
    if found > schema:
        raise StoreFormatNewer(f"耐久の記録の表の版 {found} が入れてある DBOS {DBOS_VERSION} の {schema} より新しい: {path}", (path,))
    target = _free_name(path.parent / BACKUP_DIR / f"{path.stem}-schema{found}{DB_SUFFIX}", now)
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(path, timeout=30)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    log.warning("耐久の記録の表を移す前に控えを取った", extra={"path": str(path), "backup": str(target), "from": found, "to": schema})
    return target


@contextmanager
def _client(database_url: str) -> Iterator[DBOSClient]:
    """DBOS を起こさずに記録を扱うクライアントを作り、抜けるときに破棄する。

    接続が切れたら待たずに例外にする（DBOS の既定は戻るまで待ち続け、片付けが `maint.lock` と鍵のロックを持ったまま止まる）。
    """
    _load()
    client = DBOSClient(system_database_url=database_url, retry_connection_errors=False, serializer=_serializer())
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


# --- 片付け（本体は durable_sweep。DBOS の記録を読む口と消す口をここで渡す） -----------------------


def _delete_workflows(database_url: str, ids: list[str]) -> None:
    """集めた ID をすべて並べて消す。`delete_children` には頼らない（I8）。"""
    with _client(database_url) as client:
        client.delete_workflows(ids, delete_children=False)


_RECORDS = sweeping.Records(read=_records, delete=_delete_workflows)


def retire(root: Path, run_key: str, now: datetime | None = None) -> Path | None:
    """鍵のファイルの耐久ワークフローがすべて終わっていれば retired/ へ移し、移した先を返す。待ちがあれば何もしない。

    呼び手が `run_lock` を持ち、DBOS を閉じた後に呼ぶ。
    """
    return sweeping.retire_name(root, run_name(run_key), _RECORDS, now or datetime.now(UTC))


def sweep(root: Path, now: datetime | None = None) -> SweepResult:
    """終わった鍵のファイルを retired/ へ移し、古い退避を消す（`durable_sweep.sweep`）。"""
    return sweeping.sweep(root, _RECORDS, now)


def sweep_url(root: Path, database_url: str, now: datetime | None = None) -> SweepResult:
    """台帳の鍵のうち、すべて終わって日数が過ぎた鍵の実行と台帳の行を消す（`durable_sweep.sweep_url`）。"""
    return sweeping.sweep_url(root, database_url, _RECORDS, now)


# --- 待ちを抱えた実行の一覧（決定 86） ---------------------------------------------------


def _pending_in(root: Path) -> list[RunRecord]:
    found: list[RunRecord] = []
    unreadable: list[str] = []
    for body in sorted(_dbos_dir(root).glob(f"*{DB_SUFFIX}")):
        try:
            records = _records(locks.sqlite_url(body), None, list(WAITING))
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
    表の版が今の DBOS より古い鍵のファイルは、DBOS が移す前に `dbos/backup/` へ控えを取る（B12）。
    URL のときは先に台帳 `ndf_runs` へ鍵を書き、書けなければ起こさない。DBOS の接続に keepalive を付ける（B11）。
    記録へ書く値は `_RedactingSerializer` が秘密を伏せてから書く（B8）。
    耐久ワークフローと耐久ステップの関数は、呼ぶ前に定義しておく（回復が名前で引くため）。
    """
    _load()
    now = datetime.now(UTC)
    name = run_name(run_key)
    path = locks.sqlite_path(database_url)
    config: DBOSConfig = {
        "name": name,
        "system_database_url": database_url,
        "executor_id": name,
        "application_version": app_version(run_key),
        "log_level": LOG_LEVEL,
        "serializer": _serializer(),
        "notification_listener_polling_interval_sec": limits.value(POLL, "interval"),
    }
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _check_key_file(path, now)
        _backup_before_migration(path, now)
    else:
        _register_run(database_url, name, run_key, now)
        # DBOS の接続には idle_session_timeout を付けない。LISTEN の接続は待つ間ずっと idle で、切られると通知を落とす
        config["db_engine_kwargs"] = {"connect_args": {"application_name": name, **locks.PG_KEEPALIVE}}
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
    _load()
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
    sqlite = locks.is_sqlite(url)
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
