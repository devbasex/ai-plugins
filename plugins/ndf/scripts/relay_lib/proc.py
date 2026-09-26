"""hook を呼んだ claude とラッパーとの位置を、親のプロセスをたどって決める（#895・#1016・#1142 の C6）。"""
from __future__ import annotations

import os
import subprocess

from .common import CHILD_FILE, relay_running


def proc_info(pid: int) -> tuple[int, str] | None:
    """(親の pid, 名前)。Linux は /proc、それ以外は ps で読む。"""
    try:
        with open(f"/proc/{pid}/stat") as f:
            s = f.read()
        name = s[s.index("(") + 1:s.rindex(")")]
        ppid = int(s[s.rindex(")") + 2:].split()[1])
        return ppid, name
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=2).stdout.strip()
        ppid, name = out.split(None, 1)
        return int(ppid), os.path.basename(name)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def is_direct_child(d: str, start: int | None = None) -> bool:
    """hook の親をたどって最初に当たる claude が、ラッパーの起動した子（`child.pid`）かを見る。

    claude は名前（`claude`）か、子と同じ名前か、pid そのもので見分ける。
    conductor が Bash から起こした `claude -p` は子の claude の孫に当たり、先にそちらに当たる。
    """
    try:
        with open(os.path.join(d, CHILD_FILE)) as f:
            child = int(f.read().strip())
    except (OSError, ValueError):
        return False
    info = proc_info(child)
    child_name = info[1] if info else None
    pid = os.getppid() if start is None else start
    for _ in range(64):
        if pid <= 1:
            return False
        if pid == child:
            return True
        info = proc_info(pid)
        if info is None:
            return False
        ppid, name = info
        if name == "claude" or (child_name is not None and name == child_name):
            return False
        pid = ppid
    return False


def relay_position() -> str:
    """ラッパーとの位置を返す。`relay`（直接の子）か、外である理由（`no-dir` / `not-running` / `not-child`）。

    `not-child` はラッパーが動いているのに hook を呼んだ claude が `child.pid` でないとき
    （fork したセッション・bg-pty-host の下・別の入口。#1016）。
    """
    d = os.environ.get("NDF_RELAY_DIR")
    if not d or not os.path.isdir(d):
        return "no-dir"
    if not relay_running(d):
        return "not-running"
    if not is_direct_child(d):
        return "not-child"
    return "relay"


def under_relay() -> str | None:
    """`NDF_RELAY_DIR` があり、ラッパーが動いていて、hook を呼んだ claude がラッパーの直接の子ならその場所を返す。"""
    if relay_position() == "relay":
        return os.environ.get("NDF_RELAY_DIR")
    return None


def relay_child_pid() -> int | None:
    """ラッパーが起動した子の pid（`child.pid`）。読めなければ None。"""
    d = os.environ.get("NDF_RELAY_DIR")
    if not d:
        return None
    try:
        with open(os.path.join(d, CHILD_FILE)) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None
