"""副命令 `test-slot`（#2711 の契約「`test-slot.py run`」「`test-slot.py report`」）。

- `run [--wait-limit <秒>] -- <シェルのコマンド 1 つ>`: 重い処理なら枠の下で走らせる。出力と終了コードはそのまま返す（I7）。
  待ちを諦めた・メモリで止めた・設定が誤っているときは、標準エラーへ結果の JSON を出して終了コード 2
- `report [--since <7d のような期間>]`: 枠の記録を読み、結果の JSON を標準出力へ 1 行出す
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import signal
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType

from ndf import identity
from ndf.testing import limits as limits_mod
from ndf.testing import machine, records, slots, weight

EXIT_STOPPED = 2
_PERIOD = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_FORWARDED = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


def _stopped(summary: str, **extra: object) -> int:
    print(json.dumps({"status": "stopped", "summary": summary, **extra}, ensure_ascii=False), file=sys.stderr)
    return EXIT_STOPPED


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

    子をまだ起こしていない（枠・メモリを待っている）ときは `KeyboardInterrupt` を送って待ちを抜ける。
    取りかけた枠は `slots.gate` が返す。
    """
    interrupted: list[int] = []

    def forward(signum: int, _frame: FrameType | None) -> None:
        interrupted.append(signum)
        proc = get_proc()
        if proc is None:
            raise KeyboardInterrupt
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signum)
            except ProcessLookupError:
                pass

    previous = {sig: signal.signal(sig, forward) for sig in _FORWARDED}
    try:
        yield interrupted
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _stopped_from(exc: slots.Stopped) -> int:
    return _stopped(
        exc.summary(),
        metrics={"waited_seconds": round(exc.waited_seconds, 1), "available_mib": exc.available_mib},
        cause={"kind": exc.reason},
    )


def _run(command: str, wait_limit: int | None) -> int:
    cwd = Path.cwd()
    try:
        limits = _load_limits(cwd, wait_limit)
    except limits_mod.SlotConfigError as exc:
        return _stopped(str(exc), items=exc.items)
    # 枠の記録の種類は AI の直接の実行（direct）。重くなければ枠を取らない（宣言の suites を空で渡す）
    kind = slots.DIRECT if weight.is_heavy(command, limits_mod.load_suites(cwd)) else None
    proc: subprocess.Popen | None = None
    with _forwarding_signals(lambda: proc) as interrupted:
        try:
            with slots.gate(command, cwd, kind=kind, suites=[], limits=limits) as held:
                if interrupted:
                    raise KeyboardInterrupt
                proc = subprocess.Popen(
                    ["bash", "-c", command], env={**os.environ, **held.env}, start_new_session=True, preexec_fn=held.preexec
                )
                held.watch(proc)
                code = _wait(proc)
                if interrupted:
                    raise KeyboardInterrupt  # 記録に interrupted を残す
        except slots.Stopped as exc:
            return _stopped_from(exc)
        except KeyboardInterrupt:
            return 128 + (interrupted[0] if interrupted else signal.SIGINT)
    if held.note:
        print(held.note, file=sys.stderr)
    return code


def _wait(proc: subprocess.Popen) -> int:
    while True:
        try:
            return proc.wait()
        except InterruptedError:
            continue


def _report(since: str) -> int:
    try:
        seconds = parse_period(since)
    except ValueError as exc:
        return _stopped(str(exc))
    try:
        directory = slots.slots_dir()
    except identity.IdentityError as exc:
        return _stopped(str(exc), cause={"kind": "config_broken"})
    # 記録が無ければ 0 件として読む
    metrics = records.report(directory, seconds, current_oom=machine.oom_kill_count())
    summary = f"直近 {since}: 待った実行 {metrics['waited_runs']} 本・メモリで止めた {metrics['memory_stopped']} 本・諦めた {metrics['gave_up']} 本"
    result = {"status": "ok", "summary": summary, "metrics": metrics}
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="test-slot", description="重いテストを作業マシンのテストの枠の下で走らせる")
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="1 本のコマンドを枠の下で走らせる")
    run.add_argument("--wait-limit", type=int, help="枠とメモリを待つ上限の秒（既定は設定の値）")
    run.add_argument("command", nargs=argparse.REMAINDER, help="-- の後ろにシェルのコマンド 1 つ")
    rep = sub.add_parser("report", help="待った・止めた・諦めた回数と OOM Killer の増分を読む")
    rep.add_argument("--since", default="7d", help="期間（既定 7d）")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_STOPPED if exc.code else 0
    if args.action == "report":
        return _report(args.since)
    words = args.command[1:] if args.command[:1] == ["--"] else args.command
    if len(words) != 1 or not words[0].strip():
        return _stopped("run には -- の後ろにシェルのコマンドを 1 つ（引用して）渡す")
    if args.wait_limit is not None and args.wait_limit < 1:
        return _stopped("--wait-limit は 1 以上の秒にする")
    return _run(words[0], args.wait_limit)
