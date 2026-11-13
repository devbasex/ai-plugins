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
        proc = subprocess.Popen(command, env={**env, **held.env}, start_new_session=True)
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
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock, Timeout

from ndf import identity
from ndf.testing import limits as limits_mod
from ndf.testing import machine, weight

log = logging.getLogger(__name__)

# 枠を持つ実行の子孫が受け継ぐ環境変数（入れ子の実行は枠を取らず待たない。I3）
HELD_ENV = "NDF_TEST_SLOT_HELD"
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
# 記録を残す期間と行数（I11）
KEEP_DAYS = 30
KEEP_ROWS = 10_000
# 記録と停止の時刻のロックを待つ上限
RECORD_LOCK_SECONDS = 10.0

EVENTS_FILE = "events.jsonl"
STOP_FILE = "stop.json"

FINISHED = "finished"
GAVE_UP = "gave_up"
MEMORY_STOPPED = "memory_stopped"
INTERRUPTED = "interrupted"
DIRECT = "direct"

ReadMemory = Callable[[], "int | None"]


class Stopped(Exception):
    """待ちを諦めたか、メモリで止めた。落ちたテストとして数えない（終了コード 2。I9）。"""

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
    return identity.prepare_state_dir(identity.load()) / SLOTS_DIR_NAME


def _lock(path: Path) -> FileLock:
    # ソフトロックへ落とさない（カーネルのロックでないと持ち主の消滅で解けない）。ロックのファイルは消さない
    return FileLock(path, thread_local=False, fallback_to_soft=False, preserve_lock_file=True)


def _try_lock(path: Path) -> FileLock | None:
    lock = _lock(path)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return None
    return lock


def _replace_text(path: Path, text: str) -> None:
    """一時ファイルへ書いてから置き換える（読む側が書きかけを見ない）。"""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_json(path: Path, data: Mapping[str, object]) -> None:
    _replace_text(path, json.dumps(data, ensure_ascii=False) + "\n")


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _now_iso(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")


def _parse_iso(text: object) -> float | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def append_event(directory: Path, row: Mapping[str, object], now: float | None = None) -> None:
    """記録へ 1 行を足す。30 日より古い行と 10,000 行を超えた分は、足すときに古い方から消す（I11）。"""
    now = time.time() if now is None else now
    path = directory / EVENTS_FILE
    line = json.dumps(row, ensure_ascii=False)
    with _lock(directory / (EVENTS_FILE + ".lock")).acquire(timeout=RECORD_LOCK_SECONDS):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        cutoff = now - KEEP_DAYS * 86400
        kept = [ln for ln in lines if (_parse_iso(_loads(ln).get("at")) or now) >= cutoff]
        if len(kept) == len(lines) and len(lines) < KEEP_ROWS:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            return
        kept = (kept + [line])[-KEEP_ROWS:]
        _replace_text(path, "".join(ln + "\n" for ln in kept))


def _loads(line: str) -> dict[str, object]:
    try:
        data = json.loads(line)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def read_events(directory: Path) -> list[dict[str, object]]:
    try:
        lines = (directory / EVENTS_FILE).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [row for row in map(_loads, lines) if row]


def report(directory: Path, since_seconds: float, now: float | None = None, current_oom: int | None = None) -> dict[str, int | float]:
    """期間の中で待った・止めた・諦めた回数と秒、OOM Killer の回数の増分を返す。"""
    now = time.time() if now is None else now
    rows = _rows_since(directory, since_seconds, now)
    waited = [float(row.get("waited_seconds") or 0) for row in rows]
    return {
        "runs": len(rows),
        "waited_runs": sum(1 for w in waited if w > 0),
        "waited_seconds": round(sum(waited), 1),
        "memory_stopped": sum(1 for row in rows if row.get("result") == MEMORY_STOPPED),
        "gave_up": sum(1 for row in rows if row.get("result") == GAVE_UP),
        "oom_kill_increase": _oom_increase([row.get("oom_kill") for row in rows] + [current_oom]),
    }


def _rows_since(directory: Path, since_seconds: float, now: float) -> list[dict[str, object]]:
    """期間の中の記録の行を時刻の順に返す。時刻の読めない行は除く。"""
    rows = [(at, row) for row in read_events(directory) if (at := _parse_iso(row.get("at"))) is not None and at >= now - since_seconds]
    rows.sort(key=lambda item: item[0])
    return [row for _, row in rows]


def _oom_increase(counts: Sequence[object]) -> int:
    """OOM Killer の回数の並びの増分。真偽値を除いた整数だけを取る。"""
    values = [c for c in counts if isinstance(c, int) and not isinstance(c, bool)]
    # 隣どうしの差のうち正の値だけを足す（再起動で回数が減っても負にならない）
    return sum(max(0, b - a) for a, b in zip(values, values[1:]))


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
    _directory: Path | None = None
    _started_at: float = 0.0
    _floor_mib: int = 0
    _poll: float = POLL_SECONDS
    _read_memory: ReadMemory = machine.available_mib
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def watch(self, proc: subprocess.Popen) -> None:
        """テストの OOM の優先度を上げ、メモリの監視を始める。何もしない形では何もしない。"""
        if self.index is None:
            return
        machine.raise_oom_score(proc.pid)
        if not self.measured:
            return
        self._thread = threading.Thread(target=self._monitor, args=(proc,), name="ndf-test-slot-monitor", daemon=True)
        self._thread.start()

    def _monitor(self, proc: subprocess.Popen) -> None:
        while not self._stop.wait(self._poll):
            if proc.poll() is not None:
                return
            available = self._read_memory()
            if available is None or available >= self._floor_mib:
                continue
            if not self._is_last_started() or not self._claim_stop():
                continue
            self.available_mib = available
            self.memory_stopped = True
            log.warning("使えるメモリが下限を割ったのでテストを止める", extra={"available_mib": available, "slot": self.index})
            _stop_group(proc)
            return

    def _is_last_started(self) -> bool:
        """置き場の全ての持ち主の記述を読み、自分より後に始めた生きた実行が無ければ真（決定 6・決定 14）。"""
        assert self._directory is not None
        mine = (self._started_at, self.index or 0)
        for path in self._directory.glob(f"{SLOT_PREFIX}*.json"):
            index = _slot_index(path)
            if index is None or index == self.index:
                continue
            started = (_read_json(path) or {}).get("started_at")
            if not isinstance(started, (int, float)) or (started, index) <= mine:
                continue
            probe = _try_lock(_slot_path(self._directory, index, ".lock"))
            if probe is None:
                return False  # 後に始めた実行が生きている
            probe.release()
        return True

    def _claim_stop(self) -> bool:
        """前の停止から 30 秒経っていれば停止の時刻を書いて真を返す（I6）。"""
        assert self._directory is not None
        try:
            with _lock(self._directory / (STOP_FILE + ".lock")).acquire(timeout=RECORD_LOCK_SECONDS):
                last = (_read_json(self._directory / STOP_FILE) or {}).get("at")
                now = time.time()
                if isinstance(last, (int, float)) and now - last < STOP_INTERVAL_SECONDS:
                    return False
                _write_json(self._directory / STOP_FILE, {"at": now, "slot": self.index})
                return True
        except (OSError, Timeout):
            return False  # 停止の時刻を守れないときは止めない

    def _finish(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()


def _slot_path(directory: Path, index: int | None, suffix: str = "") -> Path:
    return directory / f"{SLOT_PREFIX}{index}{suffix}"


def _slot_index(path: Path) -> int | None:
    digits = path.stem.removeprefix(SLOT_PREFIX)
    return int(digits) if digits.isdigit() else None


def _stop_group(proc: subprocess.Popen) -> None:
    """自分の起こしたテストだけを止める。テストが自分と同じグループにいれば、グループでなくテストへ送る（I5）。"""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    own_group = pgid == os.getpgrp()

    def send(sig: int) -> None:
        try:
            proc.send_signal(sig) if own_group else os.killpg(pgid, sig)
        except ProcessLookupError:
            pass

    send(signal.SIGTERM)
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        send(signal.SIGKILL)


def _owner(started_at: float | None, cwd: Path) -> dict[str, object]:
    return {"started_at": started_at, "worktree": str(cwd), "pid": os.getpid()}


@contextmanager
def gate(
    command: str | Sequence[str],
    cwd: Path,
    *,
    kind: str | None = None,
    suites: Sequence[Mapping[str, object]] | None = None,
    limits: limits_mod.Limits | None = None,
    environ: Mapping[str, str] | None = None,
    directory: Path | None = None,
    poll: float = POLL_SECONDS,
    read_memory: ReadMemory = machine.available_mib,
) -> Iterator[Held]:
    """重い処理なら枠とメモリを待って取り、出るときに返して記録を足す。

    `kind` を渡すと判定を飛ばしてその種類で枠を取る（`None` なら宣言から判定する）。
    誤った設定は `limits.SlotConfigError`、待ちの諦めとメモリの停止は `Stopped` を送る。
    """
    env = os.environ if environ is None else environ
    if env.get(HELD_ENV) or env.get("GITHUB_ACTIONS"):
        yield Held()  # 入れ子（I3）と CI（決定 12）
        return
    cwd = Path(cwd)
    kind = kind or weight.classify(command, limits_mod.load_suites(cwd) if suites is None else suites)
    if kind is None:
        yield Held()
        return
    limits = limits or limits_mod.load_limits(cwd)
    try:
        directory = directory or slots_dir(env)
        directory.mkdir(parents=True, exist_ok=True)
    except (OSError, identity.IdentityError) as exc:
        # 置き場を使えなくてもテストは走らせ、結果を変えない（I8）
        log.warning("枠の置き場を使えないので枠を取らずに走らせる", extra={"error": str(exc)})
        yield Held(note=f"枠の置き場を使えないので枠を取らずに走らせた: {exc}")
        return
    yield from _held(directory, cwd, kind, limits, poll, read_memory)


def _record(directory: Path, cwd: Path, kind: str, held: Held, result: str) -> None:
    row = {
        "at": _now_iso(time.time()),
        "worktree": str(cwd),
        "kind": kind,
        "waited_seconds": round(held.waited_seconds, 1),
        "available_mib": held.available_mib,
        "measured": held.measured,
        "oom_kill": machine.oom_kill_count(),
        "result": result,
    }
    try:
        append_event(directory, row)
    except (OSError, Timeout) as exc:
        # 記録を書けなくても結果を変えない（I8）
        log.warning("枠の記録を書けない", extra={"error": str(exc), "result": result})


def _acquire(directory: Path, cwd: Path, limits: limits_mod.Limits, poll: float, read_memory: ReadMemory, held: Held) -> FileLock | None:
    """空いた枠を取り、メモリが下限以上になるまで待つ。待ちの上限は枠待ちとメモリ待ちの合計で数える。"""
    start = time.monotonic()
    deadline = start + limits.wait_limit_seconds
    lock: FileLock | None = None
    try:
        while True:
            if lock is None:
                lock = _take_free_slot(directory, cwd, limits.max_runs, held)
            if lock is not None and _memory_ready(held, read_memory, limits.memory_floor_mib):
                return lock
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if lock is not None:
                    _release(directory, held, lock)
                return None
            time.sleep(min(poll, remaining))
    finally:
        end = time.monotonic()
        held.waited_seconds = end - start
        _note_wait(start, end)


def _take_free_slot(directory: Path, cwd: Path, max_runs: int, held: Held) -> FileLock | None:
    """番号の小さい方から空いた枠を取り、持ち主を仮に書く。空きが無ければ None。"""
    for index in range(max_runs):
        lock = _try_lock(_slot_path(directory, index, ".lock"))
        if lock is not None:
            held.index = index
            _write_json(_slot_path(directory, index, ".json"), _owner(None, cwd))
            return lock
    return None


def _memory_ready(held: Held, read_memory: ReadMemory, floor_mib: int) -> bool:
    """メモリを測って `held` に書き、走らせてよければ真。"""
    held.available_mib = read_memory()
    if held.available_mib is None:
        held.measured = False  # 測れなければ待たず、監視もしない（I8）
        return True
    return held.available_mib >= floor_mib


def _release(directory: Path, held: Held, lock: FileLock) -> None:
    try:
        _slot_path(directory, held.index, ".json").unlink(missing_ok=True)
    finally:
        lock.release()


def _held(directory: Path, cwd: Path, kind: str, limits: limits_mod.Limits, poll: float, read_memory: ReadMemory) -> Iterator[Held]:
    held = Held(_directory=directory, _floor_mib=limits.memory_floor_mib, _poll=poll, _read_memory=read_memory)
    lock = _acquire(directory, cwd, limits, poll, read_memory, held)
    if lock is None:
        held.index = None
        _record(directory, cwd, kind, held, GAVE_UP)
        raise Stopped(GAVE_UP, held.waited_seconds, held.available_mib)
    held._started_at = time.time()
    _write_json(_slot_path(directory, held.index, ".json"), _owner(held._started_at, cwd))
    held.env = {HELD_ENV: str(_slot_path(directory, held.index))}
    result = INTERRUPTED
    try:
        yield held
        result = MEMORY_STOPPED if held.memory_stopped else FINISHED
    finally:
        held._finish()
        if held.memory_stopped:
            result = MEMORY_STOPPED
        if result != MEMORY_STOPPED:
            held.available_mib = read_memory()
        _release(directory, held, lock)
        _record(directory, cwd, kind, held, result)
    if result == MEMORY_STOPPED:
        raise Stopped(MEMORY_STOPPED, held.waited_seconds, held.available_mib)
