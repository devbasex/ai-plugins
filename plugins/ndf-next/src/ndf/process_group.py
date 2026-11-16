"""`start_new_session` で起こしたプロセスの組を止める（#3394 の 3394-9）。

テストの枠（`testing/`）と外部との接続（`adapters/`）の両方が読む下の層で、どちらも import しない。
猶予の秒と終わりを見る間隔は `contracts/limits.json` の方針（既定 `procs.stop-tree`）から読む。
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from collections.abc import Callable

from ndf.contracts import limits

log = logging.getLogger(__name__)

STOP_TREE = "procs.stop-tree"


def alive(proc: subprocess.Popen, pgid: int) -> bool:
    """組 `pgid` に生きた成員が残るか。直接の子は先に回収し、終わった子（ゾンビ）を成員に数えない。"""
    proc.poll()
    return group_alive(pgid)


def group_alive(pgid: int) -> bool:
    """組 `pgid` に成員が残るか（自分の子でない組にも使う。回収されていない終わった成員も数える）。"""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:  # noqa: NDF401 外部コマンド: 組の成員が残っていない
        return False
    except PermissionError:  # noqa: NDF401 外部コマンド: 送る権限の無い成員が残っている（生きている）
        return True
    return True


def signal_group(pgid: int, sig: int) -> None:
    """組 `pgid` へ `sig` を送る。組が既に終わっていれば何もしない。"""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:  # noqa: S110 外部コマンド: 組が既に終わっていれば送る先が無い
        pass


def stop_group(proc: subprocess.Popen, *, grace_key: str = STOP_TREE, send_first: bool = True) -> None:
    """組を SIGTERM から猶予の後の SIGKILL まで 1 つの手順で止める。

    組が自分と同じなら、組でなく `proc` へだけ送る。`send_first` が偽なら SIGTERM を送らずに猶予から始める
    （シグナルを組へ転送した後に呼ぶ形。先頭が回収された後でも、組の番号＝先頭の pid で残りを見る）。
    猶予の間に終わったかは組全体で見る（SIGTERM を無視する子孫が残れば SIGKILL を送る）。
    """
    grace, interval = _grace(grace_key)
    pgid = _target_group(proc, send_first)
    if pgid is None:
        return
    own_group = pgid == os.getpgrp()

    def send(sig: int) -> None:
        if not own_group:
            signal_group(pgid, sig)
            return
        try:
            proc.send_signal(sig)
        except ProcessLookupError:  # noqa: S110 外部コマンド: 子が既に終わっていれば送る先が無い
            pass

    def remaining_members() -> bool:
        return proc.poll() is None if own_group else alive(proc, pgid)

    if send_first:
        send(signal.SIGTERM)
    if _wait_or_kill(pgid, remaining_members, send, grace, interval):
        _reap(proc, grace)


def stop_detached_group(pgid: int, *, grace_key: str = STOP_TREE) -> None:
    """自分の子でない組（前の回の親が落ちて残した組）を、`stop_group` と同じ SIGTERM から SIGKILL までの手順で止める。

    先頭は自分の子でないので回収しない（回収は引き取った親が行う）。組が既に終わっていれば何もしない。
    """
    if not group_alive(pgid):
        return
    signal_group(pgid, signal.SIGTERM)
    grace, interval = _grace(grace_key)
    _wait_or_kill(
        pgid,
        lambda: group_alive(pgid),
        lambda sig: signal_group(pgid, sig),
        grace,
        interval,
    )


def _grace(grace_key: str) -> tuple[float, float]:
    """上限の表の `grace_key` の猶予の秒と、終わったかを見る間隔。"""
    return limits.seconds(grace_key), limits.value(grace_key, "interval")


def _wait_or_kill(pgid: int, remaining: Callable[[], bool], send: Callable[[int], None], grace: float, interval: float) -> bool:
    """猶予の間 `remaining` が偽になるのを待ち、残れば SIGKILL を送る。SIGKILL を送ったかを返す。"""
    deadline = time.monotonic() + grace
    while remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            log.warning("猶予の間に終わらないプロセスの組へ SIGKILL を送る", extra={"pgid": pgid, "grace_seconds": grace})
            send(signal.SIGKILL)
            return True
        time.sleep(min(interval, left))
    return False


def _target_group(proc: subprocess.Popen, send_first: bool) -> int | None:
    """止める組の番号。`send_first` なら `proc` の今の組、偽なら組の番号＝先頭の pid。既に終わっていれば回収して None。"""
    if not send_first:
        return proc.pid
    try:
        return os.getpgid(proc.pid)
    except ProcessLookupError:  # 外部コマンド: 既に終わっていれば止める先が無い（直接の子だけ回収する）
        proc.poll()
        return None


def _reap(proc: subprocess.Popen, grace: float) -> None:
    """SIGKILL の後に直接の子を回収する。回収できなければ待たずに戻る（呼んだ側の次の `poll` が回収する）。"""
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:  # noqa: S110 外部コマンド: カーネルの待ちで回収できなければ待たずに戻る
        pass
