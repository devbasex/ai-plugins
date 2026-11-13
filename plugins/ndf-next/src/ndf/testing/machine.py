"""作業マシンの CPU とメモリの読み取り（#2711 の構成要素「プロセスの読み取り」）。

cgroup の上限があればその値で絞る。上限は、このプロセスが属する cgroup とその祖先の全部の段から読む。読めない値は None を返し、呼ぶ側は測れなかったとして扱う（I8）。
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


def _cgroup_mount(mountinfo: Path) -> tuple[str, Path] | None:
    """cgroup v2 の（マウントした cgroup の根の名前, マウント先）。無いか読めなければ None。"""
    try:
        lines = mountinfo.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines:
        head, sep, tail = line.partition(" - ")
        fields = head.split()
        if sep and tail.split()[:1] == ["cgroup2"] and len(fields) >= 5:
            return fields[3], Path(fields[4])
    return None


def cgroup_dirs(
    root: Path = CGROUP_ROOT,
    proc_cgroup: Path = Path("/proc/self/cgroup"),
    mountinfo: Path = Path("/proc/self/mountinfo"),
) -> tuple[Path, ...]:
    """このプロセスが属する cgroup v2 のディレクトリと、マウントの根までの祖先（葉から根の順）。

    祖先の上限も子へ効くので、上限は全部の段の小さい方を採る。所属先を解決できなければ `root` だけを返す。
    """
    mount = _cgroup_mount(mountinfo)
    mount_root, mount_point = mount if mount is not None else ("/", root)
    try:
        lines = proc_cgroup.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    own = next((line[3:] for line in lines if line.startswith("0::")), None)
    relative = None if own is None else os.path.relpath(own, mount_root)
    if relative is None or relative == ".." or relative.startswith("../"):
        return (mount_point,)
    leaf = mount_point / relative if relative != "." else mount_point
    if not leaf.is_dir():
        return (mount_point,)
    chain = [leaf]
    while chain[-1] != mount_point and mount_point in chain[-1].parents:
        chain.append(chain[-1].parent)
    return tuple(chain)


def _smallest(values: list[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return min(known) if known else None


def cgroup_memory_limit(dirs: tuple[Path, ...] | None = None) -> int | None:
    """cgroup v2 の `memory.max` の、祖先を含めた一番小さい値（バイト）。上限が無いか読めなければ None。"""
    return _smallest([_read_int(d / "memory.max") for d in (cgroup_dirs() if dirs is None else dirs)])


def cgroup_memory_remaining(dirs: tuple[Path, ...] | None = None) -> int | None:
    """cgroup v2 の上限までの残り（バイト）。祖先を含め一番少ない段の値。上限が無いか読めなければ None。"""
    remaining: list[int | None] = []
    for d in cgroup_dirs() if dirs is None else dirs:
        limit, current = _read_int(d / "memory.max"), _read_int(d / "memory.current")
        remaining.append(None if limit is None or current is None else max(0, limit - current))
    return _smallest(remaining)


def _cpu_quota(directory: Path) -> int | None:
    """`cpu.max` の割り当ての CPU の数（切り上げ）。上限が無いか読めなければ None。"""
    try:
        quota, period = (directory / "cpu.max").read_text(encoding="ascii").split()[:2]
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if quota.isdigit() and period.isdigit() and int(period) > 0:
        return -(-int(quota) // int(period))
    return None


def _virtual_memory(field: str) -> int | None:
    """psutil のメモリの値（バイト）。読めなければ None。"""
    try:
        return int(getattr(psutil.virtual_memory(), field))
    except (OSError, RuntimeError, AttributeError):
        return None


def available_mib(cgroups: tuple[Path, ...] | None = None) -> int | None:
    """使えるメモリ（MiB）。ホストの使えるメモリと cgroup の残りの小さい方。読めなければ None。"""
    available = _virtual_memory("available")
    if available is None:
        return None
    remaining = cgroup_memory_remaining(cgroups)
    if remaining is not None:
        available = min(available, remaining)
    return available // MIB


def total_mib(cgroups: tuple[Path, ...] | None = None) -> int | None:
    """全体のメモリ（MiB）。cgroup の上限（祖先を含む）があれば物理メモリとの小さい方。"""
    total = _virtual_memory("total")
    if total is None:
        return None
    limit = cgroup_memory_limit(cgroups)
    if limit is not None:
        total = min(total, limit)
    return total // MIB


def cpu_count(cgroups: tuple[Path, ...] | None = None) -> int:
    """使える CPU の数。cgroup の `cpu.max` の割り当て（祖先を含む一番小さい値）があればその値。最小 1。"""
    try:
        count = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        count = os.cpu_count() or 1
    quota = _smallest([_cpu_quota(d) for d in (cgroup_dirs() if cgroups is None else cgroups)])
    if quota is not None:
        count = min(count, quota)
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
