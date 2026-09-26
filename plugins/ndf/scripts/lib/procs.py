"""プロセスの生死・親子・木の停止・メモリの包み（#1142 の決定 19・種類 6）。psutil を呼ぶのはこのモジュールだけである。

`/proc/` を読むのもこのモジュールだけである（構造チェックの I14）。psutil に無い cgroup のメモリ
（`memory.max`・`memory.current`・`memory.events`）の読み取りはここに残す。

- 生死はゾンビを死んだとみなす（`os.kill(pid, 0)` はゾンビにも成功するため）
- 停止は SIGTERM の後、猶予を過ぎても残れば SIGKILL を送る。対象がプロセスグループの先頭で、呼んだ側と
  別のグループなら、グループへ送る（子の CLI を残さない。#584 / #729 の決定 10）

使う側は `deps.require("procs")` を先に呼ぶ。
"""
from __future__ import annotations

import os
import signal
import time
from pathlib import Path
from typing import NamedTuple

import psutil

CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_SELF_CGROUP = Path("/proc/self/cgroup")


class CgroupMemory(NamedTuple):
    limit: int | None     # バイト。上限が無い（`max`）・読めないときは None
    current: int | None   # バイト。読めなければ None
    oom_kill: int | None  # `memory.events` の oom_kill。読めなければ None
    unlimited: bool       # `memory.max` が `max`


def _process(pid: int) -> psutil.Process | None:
    if pid <= 0:
        return None
    try:
        return psutil.Process(pid)
    except (psutil.Error, ValueError):
        return None


def pid_alive(pid: int) -> bool:
    """pid が生きているか（ゾンビは死んだとみなす）。"""
    p = _process(pid)
    if p is None:
        return False
    try:
        return p.status() != psutil.STATUS_ZOMBIE
    except psutil.ZombieProcess:
        return False
    except psutil.Error:
        return False


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


def cmdline_contains(pid: int, expected: str) -> bool | None:
    """pid の起動の引数（空白でつないだもの）が `expected` を含むか（大文字と小文字を区別しない）。読めなければ None。"""
    p = _process(pid)
    if p is None:
        return None
    try:
        return expected.lower() in " ".join(p.cmdline()).lower()
    except psutil.Error:
        return None


def parent_and_name(pid: int) -> tuple[int, str] | None:
    """(親の pid, プロセスの名前)。読めなければ None。"""
    p = _process(pid)
    if p is None:
        return None
    try:
        return p.ppid(), p.name()
    except psutil.Error:
        return None


def ancestor_pids(pid: int, limit: int = 64) -> list[int]:
    """pid の親から順に、1 の手前までの祖先の pid（`limit` 個まで）。"""
    p = _process(pid)
    if p is None:
        return []
    try:
        return [a.pid for a in p.parents() if a.pid > 1][:limit]
    except psutil.Error:
        return []


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


def stop_tree(pid: int, grace: float = 3.0, poll: float = 0.1) -> bool:
    """pid とその子を止める（SIGTERM → 猶予の後に SIGKILL）。止まった（元から無い）なら真。"""
    root = _process(pid)
    if root is None or pid_is_zombie(pid):
        return True
    group = leads_own_group(pid)
    procs = [root, *(_process(c) for c in child_pids(pid))]
    procs = [p for p in procs if p is not None]
    _signal_all(procs, pid, group, signal.SIGTERM)
    _gone, alive = psutil.wait_procs(procs, timeout=grace)
    if alive:
        _signal_all(alive, pid, group, signal.SIGKILL)
        _gone, alive = psutil.wait_procs(alive, timeout=max(poll, 0.5))
    return not alive


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


def memory_available() -> tuple[int, int]:
    """(使える量, 全体) をバイトで返す（`/proc/meminfo` の MemAvailable と MemTotal に当たる）。"""
    vm = psutil.virtual_memory()
    return vm.available, vm.total


def cgroup_dir(root: Path = CGROUP_ROOT, proc_cgroup: Path = PROC_SELF_CGROUP) -> Path:
    """自分の cgroup（v2）の位置。根に `memory.events` があればそこ（コンテナの中）、無ければ `0::<path>` の下。"""
    if (root / "memory.events").exists():
        return root
    try:
        for line in proc_cgroup.read_text(encoding="utf-8").splitlines():
            if line.startswith("0::") and line[3:].strip().lstrip("/"):
                return root / line[3:].strip().lstrip("/")
    except OSError:
        pass
    return root


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def cgroup_memory(directory: Path | None = None) -> CgroupMemory:
    """cgroup の上限・使用量・oom_kill の回数。"""
    d = cgroup_dir() if directory is None else Path(directory)
    try:
        raw_limit = (d / "memory.max").read_text(encoding="utf-8").strip()
    except OSError:
        raw_limit = ""
    unlimited = raw_limit == "max"
    limit = None if unlimited else _read_int(d / "memory.max")
    oom = None
    try:
        for line in (d / "memory.events").read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[0] == "oom_kill" and fields[1].isdigit():
                oom = int(fields[1])
    except OSError:
        pass
    return CgroupMemory(limit, _read_int(d / "memory.current"), oom, unlimited)


def wait_gone(pid: int, timeout: float, poll: float = 0.1) -> bool:
    """pid が終わる（ゾンビを含む）まで最大 `timeout` 秒待つ。終われば真。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(poll)
    return not pid_alive(pid)
