"""耐久の記録の包み（#1142 の決定 26〜28・34）。DBOS を import するのはこのモジュールだけである（I16）。

1 回の起動（`supervise.py run` 1 本・`queue` 1 本・`drive.py` 1 本・投稿キュー 1 つ）が、1 つのプロセスと
1 つの SQLite のファイル（耐久の記録）を持つ。使う側は耐久ワークフローと耐久ステップを装飾子で書き、
`launch` → `resolve` → `start` の順に打つ。落ちた後に同じコマンドを打ち直すと、`launch` が記録から
途中の耐久ワークフローを続け、記録のある耐久ステップは流し直さない（使い方の例は `supervise_lib/flow.py`）。

- 置き場: `NDF_DBOS_DIR` → `<チェックアウトの根>/dbos`（`records_dir`。根は `state_home.checkout_home`）
- 実行の鍵: `<種類>-<識別の sha256 の先頭 12 字>`。ファイル名・executor_id・ファイルロック（`<鍵>.lock`）に使う（I17）
- 形式の版: `FORMAT`。耐久ワークフローの中の DBOS の呼び出しの順を変えたら上げる（I18・決定 28）。
  途中の記録を続ける必要があるときは、上げずに `patched` で新旧の処理を分ける（#2616 の決定 2）
- 実行の回: 耐久ワークフローの ID は `<接頭辞>-<回>`。`resolve` が続けるか新しく始めるかを決める
- 止まりと続き: 耐久ワークフローの中で `pause`（イベントを立てて待つ）、外から `resume`（メッセージを送る）
- 抜け方: 止まりのまま抜けるときは `exit_leaving_pending`（`os._exit`。DBOS の後始末で待たない）
- 進行表のワークフロー（#3092）は `launch(..., url=<進行表の DB>, app_version=None)` で、進行表と同じ DB に記録を置き、
  アプリの版を DBOS がソースから計算する（決定 20・21）。外から `cancel_from`・`workflow_version_from` で読む

使う側は `deps.require(..., "durable")` を先に呼ぶ（グループ `durable` は dbos と filelock を持つ）。
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from dbos import DBOS, DBOSClient, SetEnqueueOptions, SetWorkflowID
from dbos._utils import GlobalParams  # 起動したアプリの版（公開の API が無い）

import limits
import locks
from durable_keys import KIND_RE, DurableError, key_hash, launch_key, purge_retired, record_path, records_dir, retire_record  # noqa: F401,E501  durable の名前として出す

FORMAT = "ndf-durable-1"
POLL_SECONDS = limits.value("durable.poll", "interval")  # 耐久キューと通知の問い合わせの間隔（1 ステップの上乗せを 1 秒以内に収める）
KEEP_DAYS = 30  # 使われていない耐久の記録を消すまでの日数
PAUSE_SECONDS = 30 * 86400  # 止まり（pause）の待ちの上限
RUNNING = ("PENDING", "ENQUEUED")
FINISHED = ("SUCCESS", "ERROR", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED")


@dataclass(frozen=True)
class Launched:
    key: str
    path: Optional[Path]  # `url` を渡した起動では None
    retired: Optional[Path] = None  # 退避した失われた計画の記録（`retire_if`。#2647 の決定 4）
    version: str = ""  # この起動のアプリの版（`app_version=None` なら DBOS がソースから計算した値。#3092 の決定 21）


@dataclass(frozen=True)
class WorkflowRef:
    """`resolve` の答え。`action` は `start`（新しい実行の回）・`continue`（途中の回を続ける）・`done`（記録した出力を返す）。"""

    id: str
    number: int
    action: str
    output: Any = None


@dataclass(frozen=True)
class Outcome:
    """`wait` の答え。`kind` は `event`（待ったイベントが立った）・`done`・`error`・`cancelled`。"""

    kind: str
    value: Any = None


_STATE: dict[str, Any] = {"launched": None, "stack": None, "queues": {}}


def _url(path: Path) -> str:  # 置き場と鍵は `durable_keys.py`（DBOS を読まずに記録の有無を答えるため分けた）
    return f"sqlite:///{path}"


# ---- 装飾子 ----


def workflow(fn: Optional[Callable] = None, *, name: Optional[str] = None) -> Any:
    """耐久ワークフローの装飾子（`@durable.workflow` / `@durable.workflow(name=...)`）。"""

    def wrap(f: Callable) -> Callable:
        return DBOS.workflow(name=name)(f)

    return wrap(fn) if fn is not None else wrap


def step(fn: Optional[Callable] = None, *, name: Optional[str] = None) -> Any:
    """耐久ステップの装飾子（`@durable.step` / `@durable.step(name=...)`）。記録のある耐久ステップは続けるときに流れない。"""

    def wrap(f: Callable) -> Callable:
        return DBOS.step(name=name)(f)

    return wrap(fn) if fn is not None else wrap


# ---- 起動と後始末 ----


def purge_old(directory: Path, days: float = KEEP_DAYS, skip: Iterable[str] = (), now: Optional[float] = None) -> list[str]:
    """最終の更新から `days` 日を過ぎた、使われていない（ロックの取れる）耐久の記録を `-wal`・`-shm`・`.lock` ごと消す。消した鍵を返す。"""
    now = time.time() if now is None else now
    skipped, removed = set(skip), []
    purge_retired(directory, days, now)  # 退避した記録も同じ日数で消す（#2647 の決定 4）
    for db in sorted(Path(directory).glob("*.sqlite")):
        key = db.stem
        if key in skipped:
            continue
        files = [db, db.with_name(f"{key}.sqlite-wal"), db.with_name(f"{key}.sqlite-shm")]
        try:
            newest = max(f.stat().st_mtime for f in files if f.exists())
        except (OSError, ValueError):
            continue
        if now - newest < days * 86400:
            continue
        held = locks.try_exclusive(db.with_name(key))
        if held is None:
            continue
        with held:
            for f in files:
                with contextlib.suppress(FileNotFoundError):
                    f.unlink()
        with contextlib.suppress(FileNotFoundError):
            locks.lock_path(db.with_name(key)).unlink()
        removed.append(key)
    return removed


def cancel_others(path: Path, keep: Iterable[str]) -> list[str]:
    """`launch` の前に（後だと回復した耐久ステップが最大 1 回流れる）、`keep` のどの接頭辞にも当たらない途中の
    耐久ワークフローを子ごと止める。止めた ID を返す。"""
    keeps = tuple(keep)
    if not path.exists():
        return []
    client = DBOSClient(system_database_url=_url(path))
    try:
        rows = client.list_workflows(status=list(RUNNING), has_parent=False, load_input=False, load_output=False)
        stopped = [w.workflow_id for w in rows if not any(w.workflow_id == k or w.workflow_id.startswith(k + "-") for k in keeps)]
        for wid in stopped:
            client.cancel_workflow(wid, cancel_children=True)
    finally:
        client.destroy()
    return stopped


def _console_to_stderr() -> None:
    """DBOS のコンソールのログの出力先を今の sys.stderr へ向け直す（DBOS は最初の起動の出力先に固定し、開き直すと
    閉じた出力先を flush して落ちる。テストの capsys など）。"""
    for h in logging.getLogger("dbos").handlers:
        if h.name == "__dbos_console_log_handler__" and isinstance(h, logging.StreamHandler):
            with h.lock:  # setStream は前の出力先を flush するため、閉じた出力先では使えない
                h.stream = sys.stderr


def launch(
    kind: str,
    identity: str,
    queues: Optional[Mapping[str, Mapping[str, Any]]] = None,
    listen: Optional[Iterable[str]] = None,
    keep: Optional[str | Iterable[str]] = None,
    lock_timeout: Optional[float] = None,
    retire_if: Optional[Callable[[], bool]] = None,
    url: Optional[str] = None,
    app_version: Optional[str] = FORMAT,
) -> Launched:
    """実行の鍵の排他を取り、耐久の記録を開いて DBOS を起動する。1 つのプロセスで 1 回だけ（`close` の後は打てる）。
    - `queues`: 耐久キューの名前 → `register_queue` の引数（`worker_concurrency`・`concurrency`・`partition_concurrency` ほか）
    - `listen`: 待ち受けて取り出す耐久キューの名前。省けば `queues` のすべて、空なら取り出さない
    - `keep`: 続ける耐久ワークフローの接頭辞。渡せば、当たらない途中の耐久ワークフローを launch の前に子ごと止める
    - `lock_timeout`: 同じ実行の鍵の起動が動いている間、待つ秒（None は空くまで待つ）
    - `retire_if`: 排他の中で打ち、真なら記録を `<鍵>.sqlite.retired-<時刻>` へ退避して新しい記録を開く
    - `url`: 記録の置き場の URL。渡せば鍵ごとのファイルとロックを使わず、`purge_old`・`retire_if`・`keep` を当てない
    - `app_version`: None なら DBOS がワークフローのソースから版を計算する（`enable_patching` も偽。`patched` は使えない）
    """
    if _STATE["launched"] is not None:
        raise DurableError("耐久の記録はこのプロセスで開いている（1 つの起動は 1 つの耐久の記録）")
    key = launch_key(kind, identity)
    directory = records_dir()
    path = None if url else directory / f"{key}.sqlite"
    stack, retired = contextlib.ExitStack(), None
    try:
        if path is not None:
            directory.mkdir(parents=True, exist_ok=True)
            stack.enter_context(locks.exclusive(directory / key, timeout=lock_timeout))
            purge_old(directory, skip=[key])
            retired = retire_record(path) if retire_if is not None and path.exists() and retire_if() else None
        if keep is not None and path is not None:
            keeps = (keep,) if isinstance(keep, str) else tuple(keep)
            for wid in cancel_others(path, keeps):
                print(f"[ndf durable] 続けない途中の耐久ワークフローを止めた: {wid}", file=sys.stderr)
        config = {
            "name": "ndf",
            "system_database_url": url or _url(path),
            "log_level": "WARNING",
            "executor_id": key,
            "enable_patching": app_version is not None,
            "notification_listener_polling_interval_sec": POLL_SECONDS,
            **({"application_version": app_version} if app_version is not None else {}),
        }
        _console_to_stderr()
        DBOS(config=config)  # type: ignore[arg-type]
        stack.callback(DBOS.destroy, destroy_registry=False)
        specs = dict(queues or {})
        DBOS.listen_queues(list(specs) if listen is None else list(listen))
        DBOS.launch()
        # DBOS 3.1.0 は耐久キューの登録にシステムデータベースを読むため、launch の後に登録する
        _STATE["queues"] = {n: DBOS.register_queue(n, polling_interval_sec=POLL_SECONDS, **dict(o)) for n, o in specs.items()}
    except locks.LockTimeout as exc:
        stack.close()
        raise DurableError(f"同じ実行の鍵 {key} の起動が動いている: {exc}") from None
    except Exception as exc:
        stack.close()
        raise DurableError(f"耐久の記録 {path or '（URL の DB）'} を開けない: {exc}") from exc
    _STATE["stack"] = stack
    _STATE["launched"] = Launched(key, path, retired, GlobalParams.app_version)
    return _STATE["launched"]


def launched() -> Optional[Launched]:
    return _STATE["launched"]


def close() -> None:
    """DBOS を止めて実行の鍵の排他を外す（テストと、1 つのプロセスで記録を開き直すとき）。"""
    stack = _STATE["stack"]
    _STATE.update(launched=None, stack=None, queues={})
    if stack is not None:
        stack.close()


def exit_leaving_pending(code: int) -> None:
    """止まり（pause）のまま耐久ワークフローを残し、出力を流してから `os._exit` で抜ける。`os._exit` は atexit を
    通らないため、持ち主の段（`lease_stage`）が取った鍵を先に返す（#2342）。"""
    if (stage := sys.modules.get("lease_stage")) is not None:
        stage.release_held()
    for f in (sys.stdout, sys.stderr):
        f.flush()
    os._exit(code)


# ---- 実行の回 ----


def resolve(prefix: str, finished: Callable[[Any], bool] = lambda _out: False) -> WorkflowRef:
    """同じ接頭辞の最後の実行の回を見て、続けるか新しく始めるかを決める（設計の「実行の回の選び方」の表）。"""
    rows = DBOS.list_workflows(workflow_id_prefix=prefix + "-", load_input=False, load_output=True)
    pat = re.compile(re.escape(prefix) + r"-(\d+)$")
    runs = {int(m.group(1)): w for w in rows if (m := pat.match(w.workflow_id))}
    if not runs:
        return WorkflowRef(f"{prefix}-1", 1, "start")
    n = max(runs)
    last = runs[n]
    fresh = WorkflowRef(f"{prefix}-{n + 1}", n + 1, "start")
    if last.app_version != FORMAT:
        print(
            f"[ndf durable] {last.workflow_id} は形式の版 {last.app_version} の記録のため続けず、実行の回 {n + 1} を頭から流す",
            file=sys.stderr,
        )
        return fresh
    if last.status in RUNNING:
        return WorkflowRef(last.workflow_id, n, "continue")
    if last.status == "SUCCESS" and finished(last.output):
        return WorkflowRef(last.workflow_id, n, "done", last.output)
    return fresh


def start(ref: WorkflowRef, func: Callable, *args: Any, **kwargs: Any) -> str:
    """`ref` が `start` なら `ref.id` で耐久ワークフローを始める。`continue` は `launch` が回復したものを使い、`done` は何もしない。ID を返す。"""
    if ref.action == "start":
        with SetWorkflowID(ref.id):
            DBOS.start_workflow(func, *args, **kwargs)
    return ref.id


def submit(
    queue: str,
    func: Callable,
    *args: Any,
    id: Optional[str] = None,
    partition: Optional[str] = None,
    priority: Optional[int] = None,
    **kwargs: Any,
) -> str:
    """耐久キュー `queue`（`launch` の `queues` に渡したもの）へ耐久ワークフローを入れる。ID を返す。"""
    q = _STATE["queues"].get(queue)
    if q is None:
        raise DurableError(f"耐久キュー {queue} を launch で登録していない")
    opts = {k: v for k, v in (("queue_partition_key", partition), ("priority", priority)) if v is not None}
    with contextlib.ExitStack() as stack:
        if id is not None:
            stack.enter_context(SetWorkflowID(id))
        if opts:
            stack.enter_context(SetEnqueueOptions(**opts))
        return q.enqueue(func, *args, **kwargs).workflow_id


def output_of(wid: str) -> Any:
    """耐久ワークフローの終わりを待って出力を返す（失敗は例外のまま上げる。`POLL_SECONDS` の間隔で見る）。"""
    return DBOS.retrieve_workflow(wid).get_result(polling_interval_sec=POLL_SECONDS)


def status(wid: str) -> Optional[str]:
    return getattr(DBOS.get_workflow_status(wid), "status", None)


def workflow_ids(prefix: str, statuses: Optional[Iterable[str]] = None) -> list[str]:
    """接頭辞の耐久ワークフローの ID（作った順）。`statuses` を渡せばその状態だけ。"""
    only = {} if statuses is None else {"status": list(statuses)}
    return [w.workflow_id for w in DBOS.list_workflows(workflow_id_prefix=prefix, load_input=False, load_output=False, **only)]


def times(prefix: str) -> dict[str, tuple[float, Optional[float], Optional[str]]]:
    """接頭辞の耐久ワークフローの ID → (作った秒, 最後に耐久キューから出した秒か None, 耐久キューの名前か None)（#2998）。"""
    rows = DBOS.list_workflows(workflow_id_prefix=prefix, load_input=False, load_output=False)
    return {w.workflow_id: (w.created_at / 1000, w.dequeued_at and w.dequeued_at / 1000, w.queue_name) for w in rows if w.created_at}


def current_id() -> str:
    """耐久ワークフローの中で、自分の ID。"""
    if not (wid := DBOS.workflow_id):
        raise DurableError("耐久ワークフローの外で current_id を呼んだ")
    return wid


# ---- 止まりと続き ----


def pause(seq: int, event: str = "pause", topic: str = "resume", timeout: float = PAUSE_SECONDS, **fields: Any) -> Optional[dict]:
    """耐久ワークフローの中で、イベント `event` に `{"seq": seq, **fields}` を立て、`topic` のメッセージを待つ。
    `seq` より小さい番号のメッセージ（前の止まりへの続き）は読み捨てる。待ちの上限を過ぎたら None。"""
    DBOS.set_event(event, {"seq": seq, **fields})
    while True:
        msg = DBOS.recv(topic, timeout_seconds=timeout)
        if msg is None:
            return None
        if not isinstance(msg, dict) or int(msg.get("seq", seq)) >= seq:
            return msg if isinstance(msg, dict) else {"seq": seq, "value": msg}


def resume(wid: str | WorkflowRef, seq: int, topic: str = "resume", **fields: Any) -> None:
    """止まり `seq` で待つ耐久ワークフローへ続きを送る。同じ止まりへの 2 度目の送信は数えない。"""
    target = wid.id if isinstance(wid, WorkflowRef) else wid
    DBOS.send(target, {"seq": seq, **fields}, topic, idempotency_key=f"{target}:{topic}:{seq}")


def event(wid: str, key: str = "pause") -> Any:
    """立っているイベントの値（無ければ None）。待たない。"""
    return DBOS.get_event(wid, key, timeout_seconds=0)


def resume_paused(ref: WorkflowRef, event: str = "pause") -> int:
    """`ref` が `continue` でイベント `event` が立っていれば、その `seq` へ続きを送って `seq` を返す。それ以外は 0。"""
    ev = DBOS.get_event(ref.id, event, timeout_seconds=0) if ref.action == "continue" else None
    if not isinstance(ev, dict):
        return 0
    seq = int(ev.get("seq") or 0)
    resume(ref, seq)
    return seq


def patched(name: str) -> bool:
    """耐久ワークフローの中で打つ。この位置に別の記録があれば False（古い実行の打ち直し）、それ以外は印を記録して True。"""
    return DBOS.patch(name)


def recv(topic: str, timeout: float) -> Any:
    """耐久ワークフローの中で、話題 `topic` のメッセージを `timeout` 秒まで待つ（無ければ None）。"""
    return DBOS.recv(topic, timeout_seconds=timeout)


def set_event(key: str, value: Any) -> None:
    """耐久ワークフローの中で、イベント `key` を `value` にする。"""
    DBOS.set_event(key, value)


# ---- 外のプロセスから送る・読む（DBOS を起動せず、DBOSClient で記録のファイルを開く。#2436） ----


@contextlib.contextmanager
def open_client(kind: str, identity: str, start: Optional[Path] = None, url: Optional[str] = None) -> Any:
    """`record_path(kind, identity, start)` の SQLite（`url` を渡せばその DB）を開いた `DBOSClient`。無ければ `DurableError`。
    `start` は記録の置き場を決めるチェックアウト（省けば作業ディレクトリ。`records_dir`）。"""
    path = None if url else record_path(kind, identity, start)
    if path is not None and not path.exists():
        raise DurableError(f"耐久の記録 {path} が無い")
    try:
        c = DBOSClient(system_database_url=url or _url(path))
    except Exception as exc:  # noqa: BLE001  URL の DB へつなげない
        raise DurableError(f"耐久の記録を開けない: {type(exc).__name__}") from None
    try:
        yield c
    finally:
        c.destroy()


def send_to(kind: str, identity: str, wid: str, msg: Any, topic: str, key: str, start: Optional[Path] = None, url: Optional[str] = None) -> None:
    """外から耐久ワークフロー `wid` へメッセージを送る。同じ `key` の 2 度目は数えない。"""
    with open_client(kind, identity, start, url) as c:
        c.send(wid, msg, topic, idempotency_key=key)


def event_from(kind: str, identity: str, wid: str, key: str, timeout: float = 0, start: Optional[Path] = None) -> Any:
    """外から耐久ワークフロー `wid` のイベント `key` を読む。`timeout` 秒待ち、無ければ None。"""
    with open_client(kind, identity, start) as c:
        return c.get_event(wid, key, timeout_seconds=timeout)


def status_from(kind: str, identity: str, wid: str, start: Optional[Path] = None, url: Optional[str] = None) -> Optional[str]:
    """外から耐久ワークフロー `wid` の状態を読む（記録が無い・耐久ワークフローが無いときは None）。"""
    got = workflow_version_from(url, wid) if url else None
    if url or not record_path(kind, identity, start).exists():
        return got[0] if got else None
    with open_client(kind, identity, start) as c:
        rows = c.list_workflows(workflow_ids=[wid], load_input=False, load_output=False)
        return rows[0].status if rows else None


def workflow_version_from(url: str, wid: str) -> Optional[tuple[str, str]]:
    """外から実行 `wid` の（状態, アプリの版）を読む（決定 21）。実行が無ければ None。"""
    with open_client("", "", url=url) as c:
        rows = c.list_workflows(workflow_ids=[wid], load_input=False, load_output=False)
        return (rows[0].status, rows[0].app_version) if rows else None


def cancel_from(url: str, wid: str) -> bool:
    """外のプロセスから途中の実行を取り消す。途中の実行を取り消せば真、無いか終わっていれば偽。"""
    with open_client("", "", url=url) as c:
        rows = c.list_workflows(workflow_ids=[wid], load_input=False, load_output=False)
        if not rows or rows[0].status not in RUNNING:
            return False
        c.cancel_workflow(wid)
        return True


def wait_from(
    kind: str,
    identity: str,
    wid: str,
    keys: Iterable[str],
    after: Mapping[str, int] | int = 0,
    timeout: Optional[float] = None,
    poll: float = limits.value("durable.wait-from-poll", "interval"),
    start: Optional[Path] = None,
) -> Outcome:
    """`wait` と同じ判定を外から行う。終わりか、`keys` のどれかのイベントの `seq` が `after` を超えるまで待つ。
    超えたイベントが複数あれば `seq` の最も小さいもの（`keys` の `seq` は同じ連番を共有する前提）を `Outcome` で返す。"""
    names = list(keys)
    marks = {k: (after.get(k, 0) if isinstance(after, Mapping) else after) for k in names}
    deadline = None if timeout is None else time.monotonic() + timeout
    with open_client(kind, identity, start) as c:
        while True:
            rows = c.list_workflows(workflow_ids=[wid], load_input=False, load_output=True)
            if not rows:
                return Outcome("error", f"耐久ワークフロー {wid} が無い")
            st = rows[0].status
            if st == "SUCCESS":
                return Outcome("done", rows[0].output)
            if st in FINISHED:
                return Outcome("cancelled" if st == "CANCELLED" else "error", st)
            hits = []
            for k in names:
                ev = c.get_event(wid, k, timeout_seconds=0)
                if isinstance(ev, dict) and int(ev.get("seq", 0)) > marks[k]:
                    hits.append((int(ev.get("seq", 0)), k, ev))
            if hits:
                _, k, ev = min(hits, key=lambda h: h[0])
                return Outcome("event", {"key": k, **ev})
            if deadline is not None and time.monotonic() >= deadline:
                return Outcome("timeout")
            time.sleep(poll)


def wait(wid: str, key: Optional[str] = None, after: int = 0, poll: float = POLL_SECONDS, timeout: Optional[float] = None) -> Outcome:
    """耐久ワークフローの終わりか、イベント `key` の `seq` が `after` を超えるまで待つ。`timeout` 秒を過ぎたら `Outcome("timeout")`。"""
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        st = status(wid)
        if st == "SUCCESS":
            return Outcome("done", output_of(wid))
        if st in FINISHED:
            kind = "cancelled" if st == "CANCELLED" else "error"
            try:
                output_of(wid)
            except Exception as exc:  # noqa: BLE001  失敗の理由を文にして返す
                return Outcome(kind, f"{type(exc).__name__}: {exc}")
            return Outcome(kind, st)
        if key is not None:
            ev = event(wid, key)
            if isinstance(ev, dict) and int(ev.get("seq", 0)) > after:
                return Outcome("event", ev)
        if deadline is not None and time.monotonic() >= deadline:
            return Outcome("timeout")
        time.sleep(poll)
