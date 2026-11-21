"""プロセスの親子・木の停止の包み（docs/specifications/ndf-rebuild-libraries.md の決定 19・「採ったライブラリと置き場」）。psutil を呼ぶのはこのモジュールだけである。

11.0.0 の入れ替え（#3304）で 10 系のプラグインから根へ移した。テストの子孫の後始末（`scripts/pytest_child_cleanup.py`）
が使う、子の一覧・ゾンビの判定・木の停止だけを持つ。

- 停止は SIGTERM の後、猶予を過ぎても残れば SIGKILL を送る。対象がプロセスグループの先頭で、呼んだ側と
  別のグループなら、グループへ送る（子の CLI を残さない。#584 / #729 の決定 10）
- 猶予と見る間隔は 10 系の上限の表（`procs.stop-tree`）と同じ値を根で持つ

使う側は `ndf_wrappers.require("procs")` を先に呼ぶか、psutil の無い環境を自分で扱う。
"""

from __future__ import annotations

import errno
import os
import signal
import time

import psutil

STOP_GRACE = 3.0  # SIGTERM から SIGKILL までの猶予（秒）
STOP_POLL = 0.1  # 終わりを見る間隔（秒）


def _process(pid: int) -> psutil.Process | None:
    if pid <= 0:
        return None
    try:
        return psutil.Process(pid)
    except (psutil.Error, ValueError):
        return None


def pid_is_zombie(pid: int) -> bool:
    p = _process(pid)
    if p is None:
        return False
    try:
        return p.status() == psutil.STATUS_ZOMBIE
    except psutil.ZombieProcess:
        return True
    except psutil.Error:
        return False


def child_pids(pid: int, recursive: bool = True) -> list[int]:
    p = _process(pid)
    if p is None:
        return []
    try:
        return [c.pid for c in p.children(recursive=recursive)]
    except psutil.Error:
        return []


def leads_own_group(pid: int) -> bool:
    """pid がプロセスグループの先頭で、呼んだ側と別のグループか。"""
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return False
    return pgid == pid and pgid != os.getpgrp()


def stop_tree(pid: int, grace: float = STOP_GRACE, poll: float = STOP_POLL) -> bool:
    """pid とその子を止める（SIGTERM → 猶予の後に SIGKILL）。止まった（元から無い）なら真。"""
    root = _process(pid)
    if root is None or pid_is_zombie(pid):
        return True
    group = leads_own_group(pid)
    procs = [root, *(_process(c) for c in child_pids(pid))]
    procs = [p for p in procs if p is not None]
    _signal_all(procs, pid, group, signal.SIGTERM)
    alive = _wait_procs(procs, grace)
    if alive:
        _signal_all(alive, pid, group, signal.SIGKILL)
        alive = _wait_procs(alive, max(poll, 0.5))
    return not alive


def _wait_procs(procs: list[psutil.Process], timeout: float) -> list[psutil.Process]:
    """psutil.wait_procs の残りを返す。待ちの途中の EINVAL / ESRCH は、投げたプロセスだけを止まったものとみなす（#1817）。

    待つ間に子が終わり pid が別のプロセスのスレッドへ再利用されると、`os.pidfd_open` が EINVAL を投げる。
    まとめて待つ呼び出しが投げたら、同じ期限の中で 1 つずつ待ち直し、ほかのまだ生きているプロセスを残りに返す
    （残りを空にすると SIGKILL が省かれる）。ほかの errno と OSError 以外の例外はそのまま投げる。"""
    deadline = time.monotonic() + timeout
    try:
        _gone, alive = psutil.wait_procs(procs, timeout=timeout)
    except OSError as e:
        if e.errno not in (errno.EINVAL, errno.ESRCH):
            raise
    else:
        return alive
    alive = []
    for p in procs:
        try:
            _gone, rest = psutil.wait_procs([p], timeout=max(0.0, deadline - time.monotonic()))
        except OSError as e:
            if e.errno not in (errno.EINVAL, errno.ESRCH):
                raise
            continue
        alive.extend(rest)
    return alive


def _signal_all(procs: list[psutil.Process], pid: int, group: bool, sig: int) -> None:
    if group:
        try:
            os.killpg(pid, sig)
        except OSError:
            pass
    for p in procs:
        try:
            p.send_signal(sig)
        except psutil.Error:
            pass
