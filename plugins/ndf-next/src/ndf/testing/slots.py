"""テストの枠（#2711）。重い処理を作業マシンの枠に収め、メモリが下限を割ったら待つか止める。

枠は重い起動の 1 つの枠（`ndf.slot_pool`。LLM の CLI と同じ置き場を数える。#3368）で、テスト 1 本は
`launch.weight.test` の重みを取り、容量は `max_runs` 本のテストの重みの合計である。持ち主 1 つにつき番号付きの
カーネルのロック（`slot-<番号>.lock`）を持つ（決定 1）。持ち主のプロセスが消えるとカーネルがロックを解くため、
生死の判定も持ち主の番号も要らない。ロックの記述子は子へ受け継がせない。

置き場の形:

    <枠の置き場>/
    ├── slot-0.lock  slot-1.lock …   # 枠 1 つにつき 1 つ。消さない
    ├── slot-0.json  slot-1.json …   # 今の持ち主の記述（started_at・worktree・pid・weight_mib）
    ├── pool.lock                    # 枠を取る判定の排他
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

import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from filelock import Timeout

from ndf import identity, process_group, slot_pool
from ndf.testing import limits as limits_mod
from ndf.testing import machine, records, weight

# 記録の結果（FINISHED など）は records の名前を slots からも読めるように再び公開する
from ndf.testing.records import FINISHED, GAVE_UP, INTERRUPTED, MEMORY_STOPPED, RECORD_LOCK_SECONDS

log = logging.getLogger(__name__)

# 枠を持つ実行の子孫が受け継ぐ環境変数（入れ子の実行は枠を取らず待たない。I3）
HELD_ENV = "NDF_TEST_SLOT_HELD"
# CI で動いていることを示す環境変数。CI では枠を取らない（決定 12）
CI_ENV = "GITHUB_ACTIONS"
# 枠の置き場を別の開発コンテナと共有するときの環境変数（決定 8）
SLOTS_DIR_ENV = "NDF_TEST_SLOTS_DIR"
SLOTS_DIR_NAME = "test-slots"
# 枠とメモリを確かめる間隔
POLL_SECONDS = 2.0
# 1 回の下限割れで止めた後、次に止めるまで空ける秒（I6）
STOP_INTERVAL_SECONDS = 30.0
# テストの組を止める猶予と間隔の方針（contracts/limits.json）
STOP_TEST = "procs.stop-test"
STOP_FILE = "stop.json"

DIRECT = "direct"
# 枠の記録の結果の語 → 止まった理由の表の種類。待ちの上限で諦めた（枠を持つ実行は動いている）は wait_timeout、
# メモリの見張りで止めたは作業マシンの資源の待ちとして infra_wait（終了コードが落ちたテストの 1 と重ならない。I9）
CAUSE_OF = {GAVE_UP: "wait_timeout", MEMORY_STOPPED: "infra_wait"}

ReadMemory = Callable[[], "int | None"]


class Stopped(Exception):
    """待ちを諦めたか、メモリで止めた。落ちたテストとして数えない（終了コードは `command` が止まった理由の表の種類から引く。I9）。"""

    def __init__(self, reason: str, waited_seconds: float, available_mib: int | None, retired: Sequence[Mapping[str, str]] = ()):
        self.reason = reason
        self.waited_seconds = waited_seconds
        self.available_mib = available_mib
        # 枠の置き場を用意したときに退避した旧い状態の置き場（結果 JSON の `items` の形）
        self.retired = [dict(item) for item in retired]
        super().__init__(self.summary())

    @property
    def cause(self) -> str:
        """止まった理由の表の種類（`CAUSE_OF`）。"""
        return CAUSE_OF[self.reason]

    def summary(self) -> str:
        memory = "測れない" if self.available_mib is None else f"{self.available_mib} MiB"
        if self.reason == GAVE_UP:
            return f"テストの枠を {self.waited_seconds:.0f} 秒待って諦めた（使えるメモリ: {memory}）"
        return f"使えるメモリが下限を割ったのでテストを止めた（{memory}、待った秒: {self.waited_seconds:.0f}）"


class Interrupted(KeyboardInterrupt):
    """枠の置き場を用意したときに旧い状態の置き場を退避した後で、シグナルで中断された（待ちの間・子の実行中）。

    `KeyboardInterrupt` として扱える。退避した置き場（結果 JSON の `items` の形）を `retired` に持ち、中断で
    終わるときも呼び出し元が復旧先を引ける。
    """

    def __init__(self, retired: Sequence[Mapping[str, str]]):
        self.retired = [dict(item) for item in retired]
        super().__init__()


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


def slots_place(environ: Mapping[str, str] | None = None) -> tuple[Path, list[dict[str, str]]]:
    """枠の置き場と、そのために状態の置き場を用意したときに退避した旧い置き場（結果 JSON の `items` の形）。

    `NDF_TEST_SLOTS_DIR` があればそこで、状態の置き場に触れない（退避は空）。無ければ状態の置き場の下。
    エントリポイント（`ndf.cli`）も副命令の前に同じ用意をするが、ここでも用意する。`slots.gate` はエントリポイントを通らずにも呼べ、
    用意の済んだ置き場（`format.json` がある）では 2 度目の用意は何も退避しない。
    """
    env = os.environ if environ is None else environ
    if env.get(SLOTS_DIR_ENV):
        return Path(env[SLOTS_DIR_ENV]), []
    ready = identity.prepare_state_dir(identity.load())
    return ready.path / SLOTS_DIR_NAME, ready.items()


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
            process_group.stop_group(proc, grace_key=STOP_TEST)
            return

    def _is_last_started(self, own_index: int, started_at: float) -> bool:
        """置き場の全ての持ち主の記述を読み、自分より後に始めた生きた実行が無ければ真（決定 6・決定 14）。"""
        mine = (started_at, own_index)
        for path in self.directory.glob(f"{slot_pool.SLOT_PREFIX}*.json"):
            index = slot_pool.slot_index(path)
            if index is None or index == own_index:
                continue
            started = slot_pool.read_field(path, "started_at")
            if not isinstance(started, (int, float)) or (started, index) <= mine:
                continue
            if slot_pool.is_owned(self.directory, index):
                return False  # 後に始めた実行が生きている
        return True

    def _claim_stop(self, index: int) -> bool:
        """前の停止から 30 秒経っていれば停止の時刻を書いて真を返す（I6）。"""
        try:
            with slot_pool.lock(self.directory / (STOP_FILE + ".lock")).acquire(timeout=RECORD_LOCK_SECONDS):
                last = slot_pool.read_field(self.directory / STOP_FILE, "at")
                now = time.time()
                if isinstance(last, (int, float)) and now - last < STOP_INTERVAL_SECONDS:
                    return False
                slot_pool.write_json(self.directory / STOP_FILE, {"at": now, "slot": index})
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
    # 枠の置き場を用意したときに退避した旧い状態の置き場（結果 JSON の `items` の形）
    retired: list[dict[str, str]] = field(default_factory=list)
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


def _owner(started_at: float | None, cwd: Path) -> dict[str, object]:
    return {"started_at": started_at, "worktree": str(cwd), "pid": os.getpid()}


@dataclass(frozen=True)
class SlotContext:
    """1 本の実行が枠を取るときに使う値の組（置き場・作業場所・上限・`gate` の周りの値）。"""

    directory: Path
    cwd: Path
    limits: limits_mod.Limits
    options: GateOptions


@dataclass(frozen=True)
class GateOptions:
    """`gate` の周りの値（種類・宣言・上限・環境変数・置き場・確かめる間隔・メモリの読み取り・中断の知らせ）。テストが差し替える。

    `kind` を渡すと判定を飛ばしてその種類で枠を取る（None なら宣言から判定する）。`suites` と `limits` は None なら
    作業場所の宣言から読む。`environ` と `directory` は None なら今の環境変数とそこから決まる置き場を使う。
    """

    kind: str | None = None
    suites: Sequence[Mapping[str, object]] | None = None
    limits: limits_mod.Limits | None = None
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
    options: GateOptions | None = None,
) -> Iterator[Held]:
    """重い処理なら枠とメモリを待って取り、出るときに返して記録を足す。

    `options.kind` を渡すと判定を飛ばしてその種類で枠を取る（None なら宣言から判定する）。
    `options.interrupted` が真を返したら、枠・メモリの待ちを抜けて `KeyboardInterrupt` を送る（記録は interrupted）。
    旧い状態の置き場を退避した後の中断（待ちの間・子の実行中）は、退避を持つ `Interrupted` にして送る。
    誤った設定は `limits.SlotConfigError`、待ちの諦めとメモリの停止は `Stopped` を送る。
    """
    options = GateOptions() if options is None else options
    env = os.environ if options.environ is None else options.environ
    if _skip_gate(env):
        yield Held()
        return
    cwd = Path(cwd)
    suites = options.suites
    kind = options.kind or weight.classify(command, limits_mod.load_suites(cwd) if suites is None else suites)
    if kind is None:
        yield Held()
        return
    limits = options.limits or limits_mod.load_limits(cwd)
    prepared = _prepare_directory(options, env)
    retired = prepared.retired if isinstance(prepared, Held) else prepared[1]
    try:
        if isinstance(prepared, Held):
            yield prepared
            return
        yield from _held(SlotContext(prepared[0], cwd, limits, options), kind, retired)
    except KeyboardInterrupt as exc:
        if retired:
            raise Interrupted(retired) from exc
        raise


def _skip_gate(env: Mapping[str, str]) -> bool:
    """枠を取らない実行なら真。入れ子（I3）と CI（決定 12）。"""
    return bool(env.get(HELD_ENV) or env.get(CI_ENV))


def _prepare_directory(options: GateOptions, env: Mapping[str, str]) -> tuple[Path, list[dict[str, str]]] | Held:
    """枠の置き場と、用意したときに退避した旧い状態の置き場を返す。

    使えなければ、理由を `note` に書いた枠を取らない形の `Held` を返す（I8）。退避の後に使えなくなっても、
    退避した置き場はその `Held` の `retired` に残す（復旧先を呼び出し元が引ける）。
    """
    retired: list[dict[str, str]] = []
    try:
        if options.directory is None:
            directory, retired = slots_place(env)
        else:
            directory = options.directory
        directory.mkdir(parents=True, exist_ok=True)
    except (OSError, identity.IdentityError) as exc:
        if isinstance(exc, identity.IdentityError):
            # 退避の途中か後で失敗しても、それまでに退避した置き場を残す
            retired = exc.items()
        # 置き場を使えなくてもテストは走らせ、結果を変えない（I8）
        log.warning("枠の置き場を使えないので枠を取らずに走らせる", extra={"error": str(exc)})
        return Held(note=f"枠の置き場を使えないので枠を取らずに走らせた: {exc}", retired=retired)
    return directory, retired


def _record(directory: Path, cwd: Path, kind: str, held: Held, result: str) -> None:
    row = {
        "at": records.now_iso(time.time()),
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


def _acquire(ctx: SlotContext, held: Held) -> slot_pool.Taken | None:
    """空いた枠を取り、メモリが下限以上になるまで待つ。待ちの上限は枠待ちとメモリ待ちの合計で数える。

    待ちの途中で例外（シグナルの中断・置き場へ書けない）が出たら、取った枠を返してから送り直す。
    """
    start = time.monotonic()
    deadline = start + ctx.limits.wait_limit_seconds
    taken: slot_pool.Taken | None = None
    try:
        while True:
            taken = _try_once(ctx, held, taken)
            if taken is not None and _memory_ready(held, ctx.options.read_memory, ctx.limits.memory_floor_mib):
                return taken
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(ctx.options.poll, remaining))
        _give_back(taken)
        return None
    except BaseException:
        _give_back(taken, quiet=True)
        raise
    finally:
        end = time.monotonic()
        held.waited_seconds = end - start
        _note_wait(start, end)


def _try_once(ctx: SlotContext, held: Held, taken: slot_pool.Taken | None) -> slot_pool.Taken | None:
    """待ちの 1 周分。中断を確かめ、枠がまだ無ければ取る。取った枠（無ければ None）を返す。"""
    if ctx.options.interrupted():
        raise KeyboardInterrupt  # シグナルを受けた（受けたのが待ちに入る前でも、ここで抜ける）
    return taken if taken is not None else _take_free_slot(ctx, held)


def _give_back(taken: slot_pool.Taken | None, *, quiet: bool = False) -> None:
    """取った枠があれば返す。`quiet` は `Taken.release` と同じ（別の例外の後始末のとき）。"""
    if taken is not None:
        taken.release(quiet=quiet)


def _take_free_slot(ctx: SlotContext, held: Held) -> slot_pool.Taken | None:
    """テスト 1 本の重みで枠を取り、持ち主を仮に書く。空きが無い・判定の排他を待ちきれなければ None。"""
    try:
        env = os.environ if ctx.options.environ is None else ctx.options.environ
        parent = slot_pool.parent_index(ctx.directory, env)  # LLM の CLI が動かすテストは CLI の枠を含めて数える
        taken = slot_pool.take(ctx.directory, ctx.limits.capacity_mib, limits_mod.test_weight_mib(), _owner(None, ctx.cwd), absorbed=parent)
    except Timeout:  # noqa: NDF401 ファイル: ほかの起動が判定の排他を持ち続けている間は空いていないとして待つ
        return None
    if taken is not None:
        held.index = taken.index
    return taken


def _memory_ready(held: Held, read_memory: ReadMemory, floor_mib: int) -> bool:
    """メモリを測って `held` に書き、走らせてよければ真。"""
    held.available_mib = read_memory()
    if held.available_mib is None:
        held.measured = False  # 測れなければ待たず、監視もしない（I8）
        return True
    return held.available_mib >= floor_mib


def _take_slot(ctx: SlotContext, held: Held) -> slot_pool.Taken | None:
    """枠を取り、持ち主の記述に始めた時刻を書く。諦めたら None。

    置き場へ書けない（ロックや持ち主の記述を作れない）ときは、取りかけた枠を返し、`held` を枠の無い形にして
    `note` に理由を書いて None を返す。
    """
    try:
        taken = _acquire(ctx, held)
        if taken is None:
            return None
        held.started_at = time.time()
        try:
            taken.describe(_owner(held.started_at, ctx.cwd))
        except BaseException:
            taken.release(quiet=True)
            raise
        return taken
    except OSError as exc:
        log.warning("枠の置き場へ書けないので枠を取らずに走らせる", extra={"error": str(exc)})
        held._run_without_slot(f"枠の置き場へ書けないので枠を取らずに走らせた: {exc}")
        return None


def _held(ctx: SlotContext, kind: str, retired: list[dict[str, str]]) -> Iterator[Held]:
    held = Held(retired=retired)
    monitor = _MemoryMonitor(
        ctx.directory, ctx.limits.memory_floor_mib, ctx.options.poll, ctx.options.read_memory, on_stop=held._stopped_by_memory
    )
    held._monitor = monitor
    taken = _acquire_or_stop(ctx, held, kind)
    if taken is None:
        yield held  # 置き場へ書けない。枠を取らずに走らせる（I8）
        return
    held.env = {HELD_ENV: str(slot_pool.slot_path(ctx.directory, taken.index))}
    result = INTERRUPTED
    try:
        yield held
        result = FINISHED
    finally:
        result = _settle(ctx, held, kind, taken, result)
    if result == MEMORY_STOPPED:
        raise Stopped(MEMORY_STOPPED, held.waited_seconds, held.available_mib, held.retired)


def _acquire_or_stop(ctx: SlotContext, held: Held, kind: str) -> slot_pool.Taken | None:
    """枠を取る。中断されたら記録して送り直し、諦めたら記録して `Stopped` を送る。

    None を返すのは置き場へ書けず、枠を取らずに走らせる形（`held.note` に理由がある）のときだけ。
    """
    try:
        taken = _take_slot(ctx, held)
    except KeyboardInterrupt:
        # 枠・メモリの待ちの間にシグナルで中断された（取りかけた枠は返してある）
        _record(ctx.directory, ctx.cwd, kind, held, INTERRUPTED)
        raise
    if taken is None and not held.note:
        _record(ctx.directory, ctx.cwd, kind, held, GAVE_UP)
        raise Stopped(GAVE_UP, held.waited_seconds, held.available_mib, held.retired)
    return taken


def _settle(ctx: SlotContext, held: Held, kind: str, taken: slot_pool.Taken, result: str) -> str:
    """実行の後始末。監視を止め、メモリを読み直し、枠を返して記録を足す。記録した結果を返す。"""
    held._finish()
    if held.memory_stopped:
        result = MEMORY_STOPPED
    else:
        held.available_mib = ctx.options.read_memory()
    taken.release()
    _record(ctx.directory, ctx.cwd, kind, held, result)
    return result


def _close_streams(proc: subprocess.Popen[str]) -> None:
    """止めた子の出力の管を閉じる。"""
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()


def run_command(
    command: str, cwd: Path, timeout: float, *, suites: Sequence[Mapping[str, object]] | None = None
) -> subprocess.CompletedProcess[str]:
    """シェルのコマンド 1 つを、重ければ枠の下で、自分のプロセスの組で起こして終わりを待つ（出力は捕まえる）。

    上限 `timeout` を超えたとき・中断（Ctrl+C など）で抜けるときは、組ごと（子孫まで）止めて回収してから枠を返し、
    例外をそのまま送る（時間切れは `subprocess.TimeoutExpired`）。待ちの諦めと
    メモリの停止は `Stopped` を送る（落ちたコマンドとして数えない）。`suites` は重さの判定に使う宣言（省けば `cwd` から読む）。
    """
    with gate(command, cwd, options=GateOptions(suites=suites)) as held:
        proc = subprocess.Popen(
            command, shell=True, cwd=cwd, env={**os.environ, **held.env}, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True, preexec_fn=held.preexec,
        )  # fmt: skip
        held.watch(proc)
        try:
            out, err = proc.communicate(timeout=timeout)
        except BaseException:
            # 自分の組で起こした子は端末の中断を受けないため、止めずに枠だけ返すと別の検証と並んで動き続ける
            process_group.stop_group(proc, grace_key=STOP_TEST)
            _close_streams(proc)
            raise
    return subprocess.CompletedProcess(command, proc.returncode, out, err)
