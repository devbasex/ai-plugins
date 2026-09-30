"""耐久の記録の包み（#1142 の決定 26〜28・34）。DBOS を import するのはこのモジュールだけである（I16）。

1 回の起動（`supervise.py run` 1 本・`queue` 1 本・`drive.py` 1 本・投稿キュー 1 つ）が、1 つのプロセスと
1 つの SQLite のファイル（耐久の記録）を持つ。使う側は耐久ワークフローと耐久ステップを装飾子で書き、
`launch` → `resolve` → `start` の順に打つ。落ちた後に同じコマンドを打ち直すと、`launch` が記録から
途中の耐久ワークフローを続け、記録のある耐久ステップは流し直さない。

    @durable.step
    def run_step(key, sid): ...

    @durable.workflow
    def plan_workflow(key): ...

    durable.launch("run", str(plan), keep=prefix)          # 実行の鍵の排他を取り、DBOS を起動する
    ref = durable.resolve(prefix, finished=lambda out: out["result"] in ("完了", "関門"))
    wid = durable.start(ref, plan_workflow, prefix)       # done なら何も始めない
    out = ref.output if ref.action == "done" else durable.output_of(wid)

- 置き場: `NDF_DBOS_DIR` → `${XDG_STATE_HOME}/ndf/dbos` → `~/.local/state/ndf/dbos`（`records_dir`）
- 実行の鍵: `<種類>-<識別の sha256 の先頭 12 字>`。ファイル名・executor_id・ファイルロック（`<鍵>.lock`）に使う（I17）
- 形式の版: `FORMAT`。耐久ワークフローの中の DBOS の呼び出しの順を変えたら上げる（I18・決定 28）
- 実行の回: 耐久ワークフローの ID は `<接頭辞>-<回>`。`resolve` が続けるか新しく始めるかを決める
- 止まりと続き: 耐久ワークフローの中で `pause`（イベントを立てて待つ）、外から `resume`（メッセージを送る）
- 抜け方: 止まりのまま抜けるときは `exit_leaving_pending`（`os._exit`。DBOS の後始末で待たない）

使う側は `deps.require(..., "durable")` を先に呼ぶ（グループ `durable` は dbos と filelock を持つ）。
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from dbos import DBOS, DBOSClient, SetEnqueueOptions, SetWorkflowID

import locks

FORMAT = "ndf-durable-1"
POLL_SECONDS = 0.2  # 耐久キューと通知の問い合わせの間隔（1 ステップの上乗せを 1 秒以内に収める）
KEEP_DAYS = 30  # 使われていない耐久の記録を消すまでの日数
PAUSE_SECONDS = 30 * 86400  # 止まり（pause）の待ちの上限
RUNNING = ("PENDING", "ENQUEUED")
FINISHED = ("SUCCESS", "ERROR", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED")
KIND_RE = re.compile(r"^[a-z][a-z0-9]*$")


class DurableError(RuntimeError):
    """耐久の記録を開けない・書けない・使い方の誤り。理由を文にして持つ。"""


@dataclass(frozen=True)
class Launched:
    key: str
    path: Path


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


# ---- 置き場と鍵 ----


def records_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    """耐久の記録の置き場（モジュールの docstring の順）。"""
    env = os.environ if env is None else env
    if env.get("NDF_DBOS_DIR"):
        return Path(env["NDF_DBOS_DIR"])
    base = env.get("XDG_STATE_HOME") or str(Path(env.get("HOME") or Path.home()) / ".local" / "state")
    return Path(base) / "ndf" / "dbos"


def key_hash(text: str, n: int = 12) -> str:
    """鍵に使う sha256 の先頭 `n` 字。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def launch_key(kind: str, identity: str) -> str:
    """実行の鍵（`<種類>-<12 字>`）。`kind` は英小文字と数字だけ。"""
    if not KIND_RE.match(kind):
        raise DurableError(f"耐久の記録の種類は英小文字と数字だけ: {kind!r}")
    return f"{kind}-{key_hash(identity)}"


def record_path(kind: str, identity: str) -> Path:
    """耐久の記録のファイル。無ければ DBOS を起動せずに「積んでいない」と答えられる。"""
    return records_dir() / f"{launch_key(kind, identity)}.sqlite"


def _url(path: Path) -> str:
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


def _kept(wid: str, keep: tuple[str, ...]) -> bool:
    return any(wid == k or wid.startswith(k + "-") for k in keep)


def cancel_others(path: Path, keep: Iterable[str]) -> list[str]:
    """`launch` の前に、`keep` のどの接頭辞にも当たらない途中の耐久ワークフローを子ごと止める。止めた ID を返す。

    `launch` の後に止めると、回復して流れ始めた耐久ステップが最大 1 回流れる。前ならロック（I17）で他に流す者が無い。"""
    keeps = tuple(keep)
    if not path.exists():
        return []
    client = DBOSClient(system_database_url=_url(path))
    try:
        rows = client.list_workflows(status=list(RUNNING), has_parent=False, load_input=False, load_output=False)
        stopped = [w.workflow_id for w in rows if not _kept(w.workflow_id, keeps)]
        for wid in stopped:
            client.cancel_workflow(wid, cancel_children=True)
    finally:
        client.destroy()
    return stopped


def _console_to_stderr() -> None:
    """DBOS のコンソールのログの出力先を今の sys.stderr へ向け直す。

    DBOS は出力先を最初の起動の sys.stderr に固定し、次の起動で flush する。1 つのプロセスで開き直すと
    （テストの capsys のように）閉じた出力先を flush して起動が落ちる。"""
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
) -> Launched:
    """実行の鍵の排他を取り、耐久の記録を開いて DBOS を起動する。1 つのプロセスで 1 回だけ（`close` の後は打てる）。

    - `queues`: 耐久キューの名前 → `register_queue` の引数（`worker_concurrency`・`concurrency`・`partition_concurrency` ほか）
    - `listen`: 待ち受けて取り出す耐久キューの名前。省けば `queues` のすべて、空なら取り出さない
    - `keep`: 続ける耐久ワークフローの接頭辞。渡せば、当たらない途中の耐久ワークフローを launch の前に子ごと止める
    - `lock_timeout`: 同じ実行の鍵の起動が動いている間、待つ秒（None は空くまで待つ）
    """
    if _STATE["launched"] is not None:
        raise DurableError("耐久の記録はこのプロセスで開いている（1 つの起動は 1 つの耐久の記録）")
    key = launch_key(kind, identity)
    directory = records_dir()
    path = directory / f"{key}.sqlite"
    stack = contextlib.ExitStack()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        stack.enter_context(locks.exclusive(directory / key, timeout=lock_timeout))
        purge_old(directory, skip=[key])
        if keep is not None:
            keeps = (keep,) if isinstance(keep, str) else tuple(keep)
            for wid in cancel_others(path, keeps):
                print(f"[ndf durable] 続けない途中の耐久ワークフローを止めた: {wid}", file=sys.stderr)
        config = {
            "name": "ndf",
            "system_database_url": _url(path),
            "log_level": "WARNING",
            "executor_id": key,
            "application_version": FORMAT,
            "notification_listener_polling_interval_sec": POLL_SECONDS,
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
        raise DurableError(f"耐久の記録 {path} を開けない: {exc}") from exc
    _STATE["stack"] = stack
    _STATE["launched"] = Launched(key, path)
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
    """止まり（pause）のまま耐久ワークフローを残してプロセスを抜ける。出力を流してから `os._exit` で抜ける。"""
    sys.stdout.flush()
    sys.stderr.flush()
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
    """耐久ワークフローの終わりを待って出力を返す（失敗は例外のまま上げる）。終わりは `POLL_SECONDS` の間隔で見る。"""
    return DBOS.retrieve_workflow(wid).get_result(polling_interval_sec=POLL_SECONDS)


def status(wid: str) -> Optional[str]:
    st = DBOS.get_workflow_status(wid)
    return None if st is None else st.status


def workflow_ids(prefix: str, statuses: Optional[Iterable[str]] = None) -> list[str]:
    """接頭辞の耐久ワークフローの ID（作った順）。`statuses` を渡せばその状態だけ。"""
    kw: dict[str, Any] = {"workflow_id_prefix": prefix, "load_input": False, "load_output": False}
    if statuses is not None:
        kw["status"] = list(statuses)
    return [w.workflow_id for w in DBOS.list_workflows(**kw)]


def current_id() -> str:
    """耐久ワークフローの中で、自分の ID。"""
    wid = DBOS.workflow_id
    if not wid:
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
