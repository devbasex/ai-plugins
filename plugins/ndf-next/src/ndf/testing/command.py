"""副命令 `test-slot`（#2711 の契約「`test-slot.py run`」「`test-slot.py report`」）。

- `run [--wait-limit <秒>] -- <シェルのコマンド 1 つ>`: 重い処理なら枠の下で走らせる。出力と終了コードはそのまま返す（I7）。
  待ちを諦めた・メモリで止めた・引数か設定が誤っているときは、子を走らせずに（止めて）`stopped` の結果 JSON を
  標準出力へ 1 行出し、止まった理由の表の種類の終了コードで終える
- `report [--since <7d のような期間>]`: 枠の記録を読み、結果 JSON を標準出力へ 1 行出す

結果 JSON の形と終了コードは `ndf.contracts.result` が持つ。枠の記録の結果の語（`gave_up`・`memory_stopped`）は
`_CAUSE_OF` で止まった理由の表の種類へ写す。
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import re
import shlex
import signal
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType

from ndf import cli, identity
from ndf.contracts import causes, result
from ndf.testing import limits as limits_mod
from ndf.testing import machine, records, slots, weight

# 結果 JSON の tool の名前は副命令の名前（正本は `cli.COMMANDS`）
TOOL = next(name for name, module in cli.COMMANDS.items() if module == __name__)
_PERIOD = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_FORWARDED = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


# 枠の記録の結果の語 → 止まった理由の表の種類。待ちの上限で諦めた（枠を持つ実行は動いている）は wait_timeout、
# メモリの見張りで止めたは作業マシンの資源の待ちとして infra_wait（終了コードが落ちたテストの 1 と重ならない。I9）
_CAUSE_OF = {records.GAVE_UP: "wait_timeout", records.MEMORY_STOPPED: "infra_wait"}


def _stopped(kind: str, detail: str, **extra: object) -> int:
    """`stopped` の結果 JSON を標準出力へ 1 行出し、種類の終了コードを返す。"""
    return result.emit(result.stopped(TOOL, kind, detail, **extra))


def parse_period(text: str) -> float:
    match = _PERIOD.match(text)
    if not match or int(match.group(1)) < 1:
        raise ValueError(f"期間は 7d・12h・30m のように書く: {text!r}")
    return int(match.group(1)) * _UNIT_SECONDS[match.group(2)]


def _load_limits(cwd: Path, wait_limit: int | None) -> limits_mod.Limits:
    """設定の上限を読み、`--wait-limit` があれば待ちの上限を上書きする。誤った設定は `SlotConfigError` を送る。"""
    limits = limits_mod.load_limits(cwd)
    if wait_limit is not None:
        limits = dataclasses.replace(limits, wait_limit_seconds=wait_limit)
    return limits


@contextmanager
def _forwarding_signals(get_proc: Callable[[], subprocess.Popen | None]) -> Iterator[list[int]]:
    """受けたシグナルを子のグループへ送り、受けたシグナルの一覧を渡す。出るときに元のハンドラーへ戻す。

    子をまだ起こしていない（枠・メモリを待っている）ときは一覧へ足すだけにする。ここで例外を送ると、ハンドラーを
    入れてから `slots.gate` が待ちに入るまでの間に受けたときに記録が残らないため、待ちを抜けるのは
    `slots.gate` の `interrupted`（一覧を見る）に任せる。取りかけた枠は `slots.gate` が返す。
    """
    interrupted: list[int] = []

    def forward(signum: int, _frame: FrameType | None) -> None:
        interrupted.append(signum)
        proc = get_proc()
        if proc is not None and proc.poll() is None:
            _signal_group(proc, signum)

    previous = {sig: signal.signal(sig, forward) for sig in _FORWARDED}
    try:
        yield interrupted
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _again(command: str, wait_limit: int | None) -> str:
    """同じ待ちを打ち直すコマンド（`ndf test-slot run [--wait-limit N] -- '<コマンド>'`）。"""
    limit = ["--wait-limit", str(wait_limit)] if wait_limit is not None else []
    return shlex.join(["ndf", TOOL, "run", *limit, "--", command])


def _stopped_from(exc: slots.Stopped, again: str | None = None) -> int:
    """枠の待ちを止めた結果を出す。wait_timeout は契約どおり同じ待ち（`again`）を next に載せる。"""
    kind = _CAUSE_OF[exc.reason]
    return _stopped(
        kind,
        exc.summary(),
        instance=exc.reason,
        metrics={"waited_seconds": round(exc.waited_seconds, 1), "available_mib": exc.available_mib},
        next=again if kind == "wait_timeout" else None,
    )


def _run(command: str, wait_limit: int | None) -> int:
    cwd = Path.cwd()
    try:
        limits = _load_limits(cwd, wait_limit)
    except limits_mod.SlotConfigError as exc:
        return _stopped("invalid", str(exc), items=[{"detail": item} for item in exc.items])
    # 枠の記録の種類は AI の直接の実行（direct）。重くなければ枠を取らない（宣言の suites を空で渡す）
    kind = slots.DIRECT if weight.is_heavy(command, limits_mod.load_suites(cwd)) else None
    started: list[subprocess.Popen] = []
    with _forwarding_signals(lambda: started[-1] if started else None) as interrupted:
        options = slots.GateOptions(interrupted=lambda: bool(interrupted))
        try:
            with slots.gate(command, cwd, kind=kind, suites=[], limits=limits, options=options) as held:
                code = _run_child(command, held, interrupted, started)
        except slots.Stopped as exc:
            return _stopped_from(exc, _again(command, wait_limit))
        except KeyboardInterrupt:
            return 128 + (interrupted[0] if interrupted else signal.SIGINT)
    if held.note:
        print(held.note, file=sys.stderr)
    return code


def _run_child(command: str, held: slots.Held, interrupted: list[int], started: list[subprocess.Popen]) -> int:
    """枠の下で子を起こして終わりを待つ。起こした子は `started` へ足す（シグナルの転送先）。"""
    if interrupted:
        raise KeyboardInterrupt
    proc = subprocess.Popen(["bash", "-c", command], env={**os.environ, **held.env}, start_new_session=True, preexec_fn=held.preexec)
    started.append(proc)
    if interrupted:
        _signal_group(proc, interrupted[0])  # 起こしている間に受けたシグナルは転送されていない
    held.watch(proc)
    code = _wait(proc)
    if interrupted:
        # SIGTERM を無視する子孫が残っていれば、終わるまで（猶予の後は SIGKILL で）枠を持ち続ける
        slots.end_group(proc)
        raise KeyboardInterrupt  # 記録に interrupted を残す
    return code


def _signal_group(proc: subprocess.Popen, signum: int) -> None:
    try:
        os.killpg(proc.pid, signum)
    except ProcessLookupError:  # noqa: S110 外部コマンド: 子のグループが既に終わっていれば送る先が無い
        pass


def _wait(proc: subprocess.Popen) -> int:
    while True:
        try:
            return proc.wait()
        except InterruptedError:  # noqa: S112 外部コマンド: 子の終わりを待つ間にシグナルで割り込まれたら待ち直す
            continue


def _report(since: str) -> int:
    try:
        seconds = parse_period(since)
    except ValueError as exc:
        return _stopped("invalid", str(exc))
    try:
        directory = slots.slots_dir()
    except identity.IdentityError as exc:
        return _stopped("config_broken", str(exc))
    # 記録が無ければ 0 件として読む
    metrics = records.report(directory, seconds, current_oom=machine.oom_kill_count())
    summary = f"直近 {since}: 待った実行 {metrics['waited_runs']} 本・メモリで止めた {metrics['memory_stopped']} 本・諦めた {metrics['gave_up']} 本"
    return result.emit(result.result(TOOL, result.OK, summary, metrics=metrics))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=TOOL, description="重いテストを作業マシンのテストの枠の下で走らせる")
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="1 本のコマンドを枠の下で走らせる")
    run.add_argument("--wait-limit", type=int, help="枠とメモリを待つ上限の秒（既定は設定の値）")
    run.add_argument("command", nargs=argparse.REMAINDER, help="-- の後ろにシェルのコマンド 1 つ")
    rep = sub.add_parser("report", help="待った・止めた・諦めた回数と OOM Killer の増分を読む")
    rep.add_argument("--since", default="7d", help="期間（既定 7d）")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # 使い方の誤りは argparse が標準エラーへ書いた。--help は 0
        return causes.exit_code("invalid") if exc.code else 0
    if args.action == "report":
        return _report(args.since)
    words = args.command[1:] if args.command[:1] == ["--"] else args.command
    if len(words) != 1 or not words[0].strip():
        return _stopped("invalid", "run には -- の後ろにシェルのコマンドを 1 つ（引用して）渡す")
    if args.wait_limit is not None and args.wait_limit < 1:
        return _stopped("invalid", "--wait-limit は 1 以上の秒にする")
    return _run(words[0], args.wait_limit)
