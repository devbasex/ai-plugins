"""作業マシンの CPU とメモリの読み取り（#2711 の構成要素「プロセスの読み取り」）。

cgroup の上限があればその値で絞る。読めない値は None を返し、呼ぶ側は測れなかったとして扱う（I8）。
cgroup・OOM Killer の回数・OOM の優先度は Linux だけで働く。
"""

from __future__ import annotations

import os
from pathlib import Path

import psutil

MIB = 1024 * 1024
CGROUP_ROOT = Path("/sys/fs/cgroup")
# テストの OOM の優先度（決定 7）。OOM Killer が本体でなくテストを選ぶ
TEST_OOM_SCORE_ADJ = 500


def _read_int(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return int(text) if text.isdigit() else None


def cgroup_memory_remaining(root: Path = CGROUP_ROOT) -> int | None:
    """cgroup v2 の上限までの残り（バイト）。上限が無いか読めなければ None。"""
    limit = _read_int(root / "memory.max")
    current = _read_int(root / "memory.current")
    if limit is None or current is None:
        return None
    return max(0, limit - current)


def _virtual_memory(field: str) -> int | None:
    """psutil のメモリの値（バイト）。読めなければ None。"""
    try:
        return int(getattr(psutil.virtual_memory(), field))
    except (OSError, RuntimeError, AttributeError):
        return None


def available_mib(cgroup_root: Path = CGROUP_ROOT) -> int | None:
    """使えるメモリ（MiB）。ホストの使えるメモリと cgroup の残りの小さい方。読めなければ None。"""
    available = _virtual_memory("available")
    if available is None:
        return None
    remaining = cgroup_memory_remaining(cgroup_root)
    if remaining is not None:
        available = min(available, remaining)
    return available // MIB


def total_mib(cgroup_root: Path = CGROUP_ROOT) -> int | None:
    """全体のメモリ（MiB）。cgroup の上限があれば物理メモリとの小さい方。"""
    total = _virtual_memory("total")
    if total is None:
        return None
    limit = _read_int(cgroup_root / "memory.max")
    if limit is not None:
        total = min(total, limit)
    return total // MIB


def cpu_count(cgroup_root: Path = CGROUP_ROOT) -> int:
    """使える CPU の数。cgroup の `cpu.max` の割り当てがあればその値（切り上げ）。最小 1。"""
    try:
        count = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        count = os.cpu_count() or 1
    try:
        quota, period = (cgroup_root / "cpu.max").read_text(encoding="ascii").split()[:2]
        if quota.isdigit() and period.isdigit() and int(period) > 0:
            count = min(count, -(-int(quota) // int(period)))
    except (OSError, ValueError):
        pass
    return max(1, count)


def oom_kill_count(vmstat: Path = Path("/proc/vmstat")) -> int | None:
    """カーネルの OOM Killer がこれまでに止めた回数。読めなければ None。"""
    try:
        for line in vmstat.read_text(encoding="ascii").splitlines():
            key, _, value = line.partition(" ")
            if key == "oom_kill" and value.strip().isdigit():
                return int(value)
    except (OSError, UnicodeDecodeError):
        return None
    return None


def raise_oom_score(pid: int, value: int = TEST_OOM_SCORE_ADJ) -> bool:
    """プロセスの OOM の優先度を上げる。上げられなければ False（権限・Linux 以外）。"""
    try:
        Path(f"/proc/{pid}/oom_score_adj").write_text(str(value), encoding="ascii")
    except OSError:
        return False
    return True
