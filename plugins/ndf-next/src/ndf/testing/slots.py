"""テストの枠（#2711）。重い処理を作業マシンの上限の本数に収め、メモリが下限を割ったら待つか止める。

枠は番号付きのカーネルのロック（`slot-<番号>.lock`）で作る（決定 1）。持ち主のプロセスが消えるとカーネルが
ロックを解くため、生死の判定も持ち主の番号も要らない。ロックの記述子は子へ受け継がせない。

置き場の形:

    <枠の置き場>/
    ├── slot-0.lock  slot-1.lock …   # 枠 1 つにつき 1 つ。消さない
    ├── slot-0.json  slot-1.json …   # 今の持ち主の記述（started_at・worktree・pid）
    ├── stop.json                    # 最後にメモリで止めた時刻
    └── events.jsonl                 # 枠の記録（重い処理 1 本の終わりに 1 行）

使い方（実行の関数は `Popen` の前後だけを包む）:

    with slots.gate(command, cwd) as held:          # 取れるまで待つ。諦めたら Stopped
        proc = subprocess.Popen(command, env={**env, **held.env}, start_new_session=True,
                                preexec_fn=held.preexec)    # exec の前に OOM の優先度を上げる
        held.watch(proc)                            # メモリの監視を始める
        proc.wait()
                                                    # 出るときに監視が止めていれば Stopped
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from filelock import FileLock, Timeout

from ndf import identity
from ndf.testing import limits as limits_mod
from ndf.testing import machine, records, weight

# 記録の結果（FINISHED など）は records の名前を slots からも読めるように再び公開する
from ndf.testing.records import FINISHED, GAVE_UP, INTERRUPTED, MEMORY_STOPPED, RECORD_LOCK_SECONDS, _lock, _replace_text

log = logging.getLogger(__name__)

# 枠を持つ実行の子孫が受け継ぐ環境変数（入れ子の実行は枠を取らず待たない。I3）
HELD_ENV = "NDF_TEST_SLOT_HELD"
# CI で動いていることを示す環境変数。CI では枠を取らない（決定 12）
CI_ENV = "GITHUB_ACTIONS"
# 枠の置き場を別の開発コンテナと共有するときの環境変数（決定 8）
SLOTS_DIR_ENV = "NDF_TEST_SLOTS_DIR"
SLOTS_DIR_NAME = "test-slots"
# 枠のファイルの名前の頭（`slot-<番号>.lock`・`slot-<番号>.json`）
SLOT_PREFIX = "slot-"
# 枠とメモリを確かめる間隔
POLL_SECONDS = 2.0
# 1 回の下限割れで止めた後、次に止めるまで空ける秒（I6）
STOP_INTERVAL_SECONDS = 30.0
# 止めたグループが SIGTERM で終わらないときに SIGKILL を送るまでの秒
KILL_GRACE_SECONDS = 10.0
STOP_FILE = "stop.json"

DIRECT = "direct"

ReadMemory = Callable[[], "int | None"]


class Stopped(Exception):
    """待ちを諦めたか、メモリで止めた。落ちたテストとして数えない（終了コードは `command` が止まった理由の表の種類から引く。I9）。"""

    def __init__(self, reason: str, waited_seconds: float, available_mib: int | None):
        self.reason = reason
        self.waited_seconds = waited_seconds
        self.available_mib = available_mib
        super().__init__(self.summary())

    def summary(self) -> str:
        memory = "測れない" if self.available_mib is None else f"{self.available_mib} MiB"
        if self.reason == GAVE_UP:
            return f"テストの枠を {self.waited_seconds:.0f} 秒待って諦めた（使えるメモリ: {memory}）"
        return f"使えるメモリが下限を割ったのでテストを止めた（{memory}、待った秒: {self.waited_seconds:.0f}）"


# プロセスの中の待ちの帳簿（monotonic の始まりと終わり）。待ちをテストの時間に数えないために引く（I4）
_ledger: list[tuple[float, float]] = []
_ledger_lock = threading.Lock()


def _note_wait(start: float, end: float) -> None:
    with _ledger_lock:
        _ledger.append((start, end))


def waited_since(started: float) -> float:
    """`started`（`time.monotonic()` の値）より後にこのプロセスが枠とメモリを待った秒。"""
    with _ledger_lock:
        return sum(max(0.0, end - max(start, started)) for start, end in _ledger if end > started)


def slots_dir(environ: Mapping[str, str] | None = None) -> Path:
    """枠の置き場。`NDF_TEST_SLOTS_DIR` があればそこ、無ければ状態の置き場の下。"""
    env = os.environ if environ is None else environ
    if env.get(SLOTS_DIR_ENV):
        return Path(env[SLOTS_DIR_ENV])
    return identity.prepare_state_dir(identity.load()).path / SLOTS_DIR_NAME


def _try_lock(path: Path) -> FileLock | None:
    lock = _lock(path)
    try:
        lock.acquire(timeout=0)
    except Timeout:  # noqa: NDF401 ファイル: ほかの実行がロックを持っていれば枠は空いていない
        return None
    return lock


def _write_json(path: Path, data: Mapping[str, object]) -> None:
    _replace_text(path, json.dumps(data, ensure_ascii=False) + "\n")


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 持ち主の記述が無いか書きかけなら記述が無いとして扱う
        return None
    return data if isinstance(data, dict) else None


def _owner_value(path: Path, key: str) -> object:
    """記述のファイル（JSON のオブジェクト）の `key` の値。ファイルか鍵が無ければ None。"""
    data = _read_json(path)
    return None if data is None else data.get(key)


@dataclass
class _MemoryMonitor:
    """枠を持つ実行のメモリの監視。下限を割ったら、最後に始めた実行だけを前の停止から 30 秒空けて止める。"""

    directory: Path
    floor_mib: int
    poll: float
    read_memory: ReadMemory
    on_stop: Callable[[int], None]
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def start(self, proc: subprocess.Popen, index: int, started_at: float) -> None:
        self._thread = threading.Thread(target=self._run, args=(proc, index, started_at), name="ndf-test-slot-monitor", daemon=True)
        self._thread.start()

    def _run(self, proc: subprocess.Popen, index: int, started_at: float) -> None:
        while not self._stop.wait(self.poll):
            if proc.poll() is not None:
                return
            available = self.read_memory()
            if available is None or available >= self.floor_mib:
                continue
            if not self._is_last_started(index, started_at) or not self._claim_stop(index):
                continue
            self.on_stop(available)
            log.warning("使えるメモリが下限を割ったのでテストを止める", extra={"available_mib": available, "slot": index})
            _stop_group(proc)
            return

    def _is_last_started(self, own_index: int, started_at: float) -> bool:
        """置き場の全ての持ち主の記述を読み、自分より後に始めた生きた実行が無ければ真（決定 6・決定 14）。"""
        mine = (started_at, own_index)
        for path in self.directory.glob(f"{SLOT_PREFIX}*.json"):
            index = _slot_index(path)
            if index is None or index == own_index:
                continue
            started = _owner_value(path, "started_at")
            if not isinstance(started, (int, float)) or (started, index) <= mine:
                continue
            probe = _try_lock(_slot_path(self.directory, index, ".lock"))
            if probe is None:
                return False  # 後に始めた実行が生きている
            probe.release()
        return True

    def _claim_stop(self, index: int) -> bool:
        """前の停止から 30 秒経っていれば停止の時刻を書いて真を返す（I6）。"""
        try:
            with _lock(self.directory / (STOP_FILE + ".lock")).acquire(timeout=RECORD_LOCK_SECONDS):
                last = _owner_value(self.directory / STOP_FILE, "at")
                now = time.time()
                if isinstance(last, (int, float)) and now - last < STOP_INTERVAL_SECONDS:
                    return False
                _write_json(self.directory / STOP_FILE, {"at": now, "slot": index})
                return True
        except (OSError, Timeout):  # noqa: NDF401 ファイル: 停止の時刻を読み書きできないときは止めない
            return False

    def finish(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()


@dataclass
class Held:
    """枠を持つ実行。重くない・入れ子・CI・置き場を使えないときは何もしない形（`index` が None）で返る。"""

    env: dict[str, str] = field(default_factory=dict)
    waited_seconds: float = 0.0
    index: int | None = None
    note: str = ""
    measured: bool = True
    memory_stopped: bool = False
    available_mib: int | None = None
    started_at: float = 0.0
    _monitor: _MemoryMonitor | None = None

    @property
    def preexec(self) -> Callable[[], None] | None:
        """`Popen` の `preexec_fn` へ渡す関数。子が exec する前にテストの OOM の優先度を上げる（決定 7）。

        Popen の後に親から書くと、書くより先に fork した子孫（`cd sub && pytest -n 4` の pytest など）が
        既定の優先度のまま残るため、exec の前に子自身が書く。何もしない形では None。
        """
        return None if self.index is None else machine.raise_own_oom_score

    def watch(self, proc: subprocess.Popen) -> None:
        """メモリの監視を始める。何もしない形と、監視しない形では何もしない。"""
        if self.index is None or not self.measured:
            return
        assert self._monitor is not None
        self._monitor.start(proc, self.index, self.started_at)

    def _run_without_slot(self, note: str) -> None:
        """枠を取らずに走らせる形にする（監視もしない）。"""
        self.index, self._monitor = None, None
        self.note = note

    def _stopped_by_memory(self, available: int) -> None:
        self.available_mib = available
        self.memory_stopped = True

    def _finish(self) -> None:
        if self._monitor is not None:
            self._monitor.finish()


def _slot_path(directory: Path, index: int | None, suffix: str = "") -> Path:
    return directory / f"{SLOT_PREFIX}{index}{suffix}"


def _slot_index(path: Path) -> int | None:
    digits = path.stem.removeprefix(SLOT_PREFIX)
    return int(digits) if digits.isdigit() else None


def _stop_group(proc: subprocess.Popen) -> None:
    """自分の起こしたテストだけを止める。テストが自分と同じグループにいれば、グループでなくテストへ送る（I5）。

    猶予の間に終わったかは、グループ全体で見る（先頭の `bash -c` が先に終わっても、SIGTERM を無視する子孫が
    残っていれば SIGKILL を送る）。
    """
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:  # noqa: NDF401 外部コマンド: テストが既に終わっていれば止める先が無い
        return
    _end_group(proc, pgid, signal.SIGTERM)


def end_group(proc: subprocess.Popen) -> None:
    """`start_new_session` で起こした子のグループ全体が終わるのを猶予まで待ち、残れば SIGKILL を送る。

    シグナルを転送した後に呼ぶ。先頭が刈り取られた後でもグループの番号（先頭の pid）で残りを見る。
    """
    _end_group(proc, proc.pid, None)


def _end_group(proc: subprocess.Popen, pgid: int, first: int | None) -> None:
    """`first` を送り（None なら送らない）、猶予の間にグループが終わらなければ SIGKILL を送る。"""
    own_group = pgid == os.getpgrp()

    def send(sig: int) -> None:
        try:
            proc.send_signal(sig) if own_group else os.killpg(pgid, sig)
        except ProcessLookupError:  # noqa: S110 外部コマンド: グループが既に終わっていれば送る先が無い
            pass

    def alive() -> bool:
        leader_alive = proc.poll() is None  # 先頭を刈り取り、ゾンビをグループに数えない
        return leader_alive if own_group else _group_alive(pgid)

    if first is not None:
        send(first)
    deadline = time.monotonic() + KILL_GRACE_SECONDS
    while alive():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            send(signal.SIGKILL)
            return
        time.sleep(min(0.1, remaining))


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:  # noqa: NDF401 外部コマンド: グループのプロセスが残っていない
        return False
    except PermissionError:  # noqa: NDF401 外部コマンド: 送る権限の無いプロセスが残っている（生きている）
        return True
    return True


def _owner(started_at: float | None, cwd: Path) -> dict[str, object]:
    return {"started_at": started_at, "worktree": str(cwd), "pid": os.getpid()}


@dataclass(frozen=True)
class SlotContext:
    """1 本の実行が枠を取るときに使う値の組（置き場・作業場所・上限・確かめる間隔・メモリの読み取り）。"""

    directory: Path
    cwd: Path
    limits: limits_mod.Limits
    poll: float
    read_memory: ReadMemory
    interrupted: Callable[[], bool] = lambda: False


@dataclass(frozen=True)
class GateOptions:
    """`gate` の周りの値（環境変数・置き場・確かめる間隔・メモリの読み取り・中断の知らせ）。テストが差し替える。

    `environ` と `directory` は None なら今の環境変数とそこから決まる置き場を使う。
    """

    environ: Mapping[str, str] | None = None
    directory: Path | None = None
    poll: float = POLL_SECONDS
    read_memory: ReadMemory = machine.available_mib
    interrupted: Callable[[], bool] = lambda: False


@contextmanager
def gate(
    command: str | Sequence[str],
    cwd: Path,
    *,
    kind: str | None = None,
    suites: Sequence[Mapping[str, object]] | None = None,
    limits: limits_mod.Limits | None = None,
    options: GateOptions | None = None,
) -> Iterator[Held]:
    """重い処理なら枠とメモリを待って取り、出るときに返して記録を足す。

    `kind` を渡すと判定を飛ばしてその種類で枠を取る（`None` なら宣言から判定する）。
    `options.interrupted` が真を返したら、枠・メモリの待ちを抜けて `KeyboardInterrupt` を送る（記録は interrupted）。
    誤った設定は `limits.SlotConfigError`、待ちの諦めとメモリの停止は `Stopped` を送る。
    """
    options = GateOptions() if options is None else options
    env = os.environ if options.environ is None else options.environ
    if env.get(HELD_ENV) or env.get(CI_ENV):
        yield Held()  # 入れ子（I3）と CI（決定 12）
        return
    cwd = Path(cwd)
    kind = kind or weight.classify(command, limits_mod.load_suites(cwd) if suites is None else suites)
    if kind is None:
        yield Held()
        return
    limits = limits or limits_mod.load_limits(cwd)
    try:
        directory = options.directory or slots_dir(env)
        directory.mkdir(parents=True, exist_ok=True)
    except (OSError, identity.IdentityError) as exc:
        # 置き場を使えなくてもテストは走らせ、結果を変えない（I8）
        log.warning("枠の置き場を使えないので枠を取らずに走らせる", extra={"error": str(exc)})
        yield Held(note=f"枠の置き場を使えないので枠を取らずに走らせた: {exc}")
        return
    yield from _held(SlotContext(directory, cwd, limits, options.poll, options.read_memory, options.interrupted), kind)


def _record(directory: Path, cwd: Path, kind: str, held: Held, result: str) -> None:
    row = {
        "at": records._now_iso(time.time()),
        "worktree": str(cwd),
        "kind": kind,
        "waited_seconds": round(held.waited_seconds, 1),
        "available_mib": held.available_mib,
        "measured": held.measured,
        "oom_kill": machine.oom_kill_count(),
        "result": result,
    }
    try:
        records.append_event(directory, row)
    except (OSError, Timeout) as exc:
        # 記録を書けなくても結果を変えない（I8）
        log.warning("枠の記録を書けない", extra={"error": str(exc), "result": result})


def _acquire(ctx: SlotContext, held: Held) -> FileLock | None:
    """空いた枠を取り、メモリが下限以上になるまで待つ。待ちの上限は枠待ちとメモリ待ちの合計で数える。

    待ちの途中で例外（シグナルの中断・置き場へ書けない）が出たら、取った枠を返してから送り直す。
    """
    start = time.monotonic()
    try:
        lock, ready = _wait_for_slot(ctx, held, start + ctx.limits.wait_limit_seconds)
        if ready:
            return lock
        if lock is not None:
            _release(ctx.directory, held, lock)  # 期限まで待ってもメモリが足りなかった
        return None
    finally:
        end = time.monotonic()
        held.waited_seconds = end - start
        _note_wait(start, end)


def _wait_for_slot(ctx: SlotContext, held: Held, deadline: float) -> tuple[FileLock | None, bool]:
    """期限まで枠とメモリを待ち、取った枠（無ければ None）と走らせてよいかを返す。

    待ちの途中で例外が出たら、取った枠を返してから送り直す。
    """
    lock: FileLock | None = None
    try:
        while True:
            if ctx.interrupted():
                raise KeyboardInterrupt  # シグナルを受けた（受けたのが待ちに入る前でも、ここで抜ける）
            if lock is None:
                lock = _take_free_slot(ctx.directory, ctx.cwd, ctx.limits.max_runs, held)
            if lock is not None and _memory_ready(held, ctx.read_memory, ctx.limits.memory_floor_mib):
                return lock, True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return lock, False
            time.sleep(min(ctx.poll, remaining))
    except BaseException:
        if lock is not None:
            _release(ctx.directory, held, lock, quiet=True)
        raise


def _take_free_slot(directory: Path, cwd: Path, max_runs: int, held: Held) -> FileLock | None:
    """番号の小さい方から空いた枠を取り、持ち主を仮に書く。空きが無ければ None。"""
    for index in range(max_runs):
        lock = _try_lock(_slot_path(directory, index, ".lock"))
        if lock is not None:
            held.index = index
            try:
                _write_json(_slot_path(directory, index, ".json"), _owner(None, cwd))
            except BaseException:
                lock.release()
                raise
            return lock
    return None


def _memory_ready(held: Held, read_memory: ReadMemory, floor_mib: int) -> bool:
    """メモリを測って `held` に書き、走らせてよければ真。"""
    held.available_mib = read_memory()
    if held.available_mib is None:
        held.measured = False  # 測れなければ待たず、監視もしない（I8）
        return True
    return held.available_mib >= floor_mib


def _release(directory: Path, held: Held, lock: FileLock, *, quiet: bool = False) -> None:
    """持ち主の記述を消して枠を返す。`quiet` なら記述を消せなくても送らない（別の例外の後始末のとき）。"""
    try:
        _slot_path(directory, held.index, ".json").unlink(missing_ok=True)
    except OSError:
        if not quiet:
            raise
    finally:
        lock.release()


def _take_slot(ctx: SlotContext, held: Held) -> FileLock | None:
    """枠を取り、持ち主の記述に始めた時刻を書く。諦めたら None。

    置き場へ書けない（ロックや持ち主の記述を作れない）ときは、取りかけた枠を返し、`held` を枠の無い形にして
    `note` に理由を書いて None を返す。
    """
    try:
        lock = _acquire(ctx, held)
        if lock is None:
            return None
        held.started_at = time.time()
        try:
            _write_json(_slot_path(ctx.directory, held.index, ".json"), _owner(held.started_at, ctx.cwd))
        except BaseException:
            _release(ctx.directory, held, lock, quiet=True)
            raise
        return lock
    except OSError as exc:
        log.warning("枠の置き場へ書けないので枠を取らずに走らせる", extra={"error": str(exc)})
        held._run_without_slot(f"枠の置き場へ書けないので枠を取らずに走らせた: {exc}")
        return None


def _new_held(ctx: SlotContext) -> Held:
    """メモリの監視を組み込んだ、まだ枠を持たない `Held`。"""
    held = Held()
    held._monitor = _MemoryMonitor(ctx.directory, ctx.limits.memory_floor_mib, ctx.poll, ctx.read_memory, on_stop=held._stopped_by_memory)
    return held


def _record_without_slot(ctx: SlotContext, kind: str, held: Held, result: str) -> None:
    """枠を取らずに終わった実行（中断・諦め）を、枠の番号を持たない形で記録する。"""
    held.index = None
    _record(ctx.directory, ctx.cwd, kind, held, result)


def _held(ctx: SlotContext, kind: str) -> Iterator[Held]:
    directory, cwd = ctx.directory, ctx.cwd
    held = _new_held(ctx)
    try:
        lock = _take_slot(ctx, held)
    except KeyboardInterrupt:
        # 枠・メモリの待ちの間にシグナルで中断された（取りかけた枠は返してある）
        _record_without_slot(ctx, kind, held, INTERRUPTED)
        raise
    if lock is None:
        if held.note:
            yield held  # 置き場へ書けない。枠を取らずに走らせる（I8）
            return
        _record_without_slot(ctx, kind, held, GAVE_UP)
        raise Stopped(GAVE_UP, held.waited_seconds, held.available_mib)
    held.env = {HELD_ENV: str(_slot_path(directory, held.index))}
    result = INTERRUPTED
    try:
        yield held
        result = FINISHED
    finally:
        held._finish()
        if held.memory_stopped:
            result = MEMORY_STOPPED
        else:
            held.available_mib = ctx.read_memory()
        _release(directory, held, lock)
        _record(directory, cwd, kind, held, result)
    if result == MEMORY_STOPPED:
        raise Stopped(MEMORY_STOPPED, held.waited_seconds, held.available_mib)
