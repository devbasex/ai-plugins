"""プロセスの生死・親子・木の停止・メモリの包み（#1142 の決定 19・種類 6）。psutil を呼ぶのはこのモジュールだけである。

`/proc/` を読むのもこのモジュールだけである（構造チェックの I14）。psutil に無い cgroup のメモリ
（`memory.max`・`memory.current`・`memory.events`・`memory.stat`）と、メモリの圧（PSI の
`/proc/pressure/memory`・`/proc/vmstat` のスワップ I/O）と、進捗ログに載せる負荷とメモリの値（`/proc/loadavg`・
`/proc/meminfo`。#2998 の決定 3）の読み取りはここに残す。

- 生死はゾンビを死んだとみなす（`os.kill(pid, 0)` はゾンビにも成功するため）
- 停止は SIGTERM の後、猶予を過ぎても残れば SIGKILL を送る。対象がプロセスグループの先頭で、呼んだ側と
  別のグループなら、グループへ送る（子の CLI を残さない。#584 / #729 の決定 10）

使う側は `deps.require("procs")` を先に呼ぶ。
"""

from __future__ import annotations

import errno
import os
import signal
import time
from pathlib import Path
from typing import NamedTuple

import limits
import psutil

CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_SELF_CGROUP = Path("/proc/self/cgroup")
PROC_PRESSURE_MEMORY = Path("/proc/pressure/memory")
PROC_VMSTAT = Path("/proc/vmstat")
PROC_LOADAVG = Path("/proc/loadavg")
PROC_MEMINFO = Path("/proc/meminfo")
PYTEST_NAMES = ("pytest", "py.test")


class CgroupMemory(NamedTuple):
    limit: int | None  # バイト。上限が無い（`max`）・読めないときは None
    current: int | None  # バイト。読めなければ None
    oom_kill: int | None  # `memory.events` の oom_kill。読めなければ None
    unlimited: bool  # `memory.max` が `max`
    anon: int | None = None  # `memory.stat` の anon（バイト）。読めなければ None


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


def start_time(pid: int) -> float | None:
    """pid の開始の時刻（epoch 秒・小数 2 桁）。無い・ゾンビなら None。pid の使い回しを見分けるのに使う。"""
    p = _process(pid)
    if p is None or not pid_alive(pid):
        return None
    try:
        return round(p.create_time(), 2)
    except psutil.Error:
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


def cmdline_contains(pid: int, expected: str) -> bool | None:
    """pid の起動の引数（空白でつないだもの）が `expected` を含むか（大文字と小文字を区別しない）。読めなければ None。

    空の引数も読めないとみなす。終了の途中（ゾンビになる前）の pid は、psutil が例外でなく `[]` を返す。
    False（別のプロセス）と取ると、すぐ終わる CLI を監視が PIDFILE_BAD で止める（CI の不定の失敗）。
    """
    p = _process(pid)
    if p is None:
        return None
    try:
        args = p.cmdline()
    except psutil.Error:
        return None
    return expected.lower() in " ".join(args).lower() if args else None


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


# ランタイム（Claude Code・Codex など）のプロセスの名前の頭。hook の持ち主の探し方（#2229）と同じ並び
RUNTIME_NAMES = ("claude", "codex", "kiro-cli", "kiro", "agy")


def ancestors(pid: int | None = None) -> list[tuple[int, float]]:
    """pid（既定は自分）の親から順に、祖先の (pid, 開始時刻)。開始時刻を読めない祖先は飛ばす。"""
    out = []
    for a in ancestor_pids(os.getpid() if pid is None else pid):
        started = start_time(a)
        if started is not None:
            out.append((a, started))
    return out


def runtime_ancestor() -> tuple[int, float] | None:
    """自分の親をたどって最初に当たるランタイムのプロセスの (pid, 開始時刻)。見つからなければ None。"""
    for pid in ancestor_pids(os.getpid()):
        got = parent_and_name(pid)
        if got and got[1].lower().startswith(RUNTIME_NAMES):
            started = start_time(pid)
            return (pid, started) if started is not None else None
    return None


def child_pids(pid: int, recursive: bool = True) -> list[int]:
    p = _process(pid)
    if p is None:
        return []
    try:
        return [c.pid for c in p.children(recursive=recursive)]
    except psutil.Error:
        return []


def count_cmdline(*words: str) -> int:
    """コマンドラインに `words` をすべて含むプロセスの数。"""
    found = 0
    for p in psutil.process_iter(["cmdline"]):
        line = p.info["cmdline"] or []
        found += all(any(word in arg for arg in line) for word in words)
    return found


def leads_own_group(pid: int) -> bool:
    """pid がプロセスグループの先頭で、呼んだ側と別のグループか。"""
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return False
    return pgid == pid and pgid != os.getpgrp()


def stop_tree(
    pid: int, grace: float = limits.seconds("procs.stop-tree"), poll: float = limits.value("procs.stop-tree", "interval")
) -> bool:
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


def self_cgroup(path: Path = PROC_SELF_CGROUP) -> str:
    """自分の cgroup の行（`/proc/self/cgroup`）。読めなければ OSError。"""
    return path.read_text(encoding="utf-8")


def _cpu_max(path: Path) -> float | None:
    """1 つの `cpu.max` が許す CPU の数。上限が無い（`max`）・読めないときは None。"""
    try:
        quota, period = path.read_text(encoding="utf-8").split()
        return None if quota == "max" else int(quota) / int(period)
    except (OSError, ValueError):
        return None


def _cgroup_levels(directory: Path | None, root: Path) -> list[Path]:
    """自分から `root` までの cgroup のディレクトリ。親の上限も子に効くため上限はこの全部で読む。`root` の外はそこだけ。"""
    d = cgroup_dir() if directory is None else Path(directory)
    return [d, *(p for p in d.parents if p.is_relative_to(root))] if d.is_relative_to(root) else [d]


def cgroup_cpu(directory: Path | None = None, root: Path = CGROUP_ROOT) -> float | None:
    """自分から `root` までの cgroup の `cpu.max` が許す CPU の数の最小。どの階層にも上限が無い（`max`）・読めないときは None。"""
    limits = [v for v in (_cpu_max(p / "cpu.max") for p in _cgroup_levels(directory, root)) if v is not None]
    return min(limits, default=None)


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def cgroup_memory_limit(directory: Path | None = None, root: Path = CGROUP_ROOT) -> int | None:
    """自分から `root` までの cgroup の `memory.max` の最小（バイト）。どの階層にも上限が無い（`max`）・読めないときは None。"""
    limits = [v for v in (_read_int(p / "memory.max") for p in _cgroup_levels(directory, root)) if v is not None]
    return min(limits, default=None)


def cgroup_memory(directory: Path | None = None) -> CgroupMemory:
    """cgroup の上限・使用量・oom_kill の回数・anon。"""
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
    return CgroupMemory(limit, _read_int(d / "memory.current"), oom, unlimited, _stat_values(d / "memory.stat", "anon")[0])


def _stat_values(path: Path, *keys: str) -> list[int | None]:
    """`<キー> <整数>` の行が並ぶファイルを 1 回読み、`keys` の値を順に返す。読めない・行が無い・数値でなければ None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [None] * len(keys)
    found: dict[str, int | None] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] in keys and fields[0] not in found:
            found[fields[0]] = int(fields[1]) if fields[1].isdigit() else None
    return [found.get(key) for key in keys]


def memory_pressure_some_avg10(path: Path = PROC_PRESSURE_MEMORY) -> str | None:
    """PSI の `some` の行の `avg10=` の値（原文の文字列）。読めない・行が無い・小数でなければ None。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0] != "some":
            continue
        for field in fields[1:]:
            name, _, value = field.partition("=")
            if name == "avg10":
                try:
                    float(value)
                except ValueError:
                    return None
                return value
        return None
    return None


def swap_io_pages(path: Path = PROC_VMSTAT) -> int | None:
    """vmstat の `pswpin + pswpout`（ページ）。読めない・どちらかの行が無い・数値でなければ None。"""
    pages_in, pages_out = _stat_values(Path(path), "pswpin", "pswpout")
    if pages_in is None or pages_out is None:
        return None
    return pages_in + pages_out


def load_average(path: Path) -> list[float] | None:
    """`/proc/loadavg` の先頭の 3 つ（1・5・15 分）。読めない・数に直せなければ None。"""
    try:
        fields = Path(path).read_text(encoding="utf-8").split()[:3]
        return [float(f) for f in fields] if len(fields) == 3 else None
    except (OSError, ValueError):
        return None


def meminfo_mib(path: Path) -> dict[str, int]:
    """`/proc/meminfo` の `<名前>: <数> kB` の行を MiB（切り捨て）にした辞書。読めなければ空。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    found: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        fields = rest.split()
        if fields and fields[0].isdigit() and fields[1:] == ["kB"]:
            found[name.strip()] = int(fields[0]) // 1024
    return found


def _is_python(arg: str) -> bool:
    name = os.path.basename(arg)
    return name == "python" or (name.startswith("python") and name[6:].replace(".", "").isdigit())


def is_pytest_main(cmdline: list[str]) -> bool:
    """pytest の本体か（実行ファイルが pytest、または Python で最初の引数が pytest か `-m pytest`。#2998 の決定 4）。

    `uv run ... pytest` の親と、命令を文字列で持つシェルは本体に数えない。
    """
    if not cmdline:
        return False
    if os.path.basename(cmdline[0]) in PYTEST_NAMES:
        return True
    if not _is_python(cmdline[0]):
        return False
    args = iter(cmdline[1:])
    for arg in args:
        if arg == "-m":
            return next(args, None) in PYTEST_NAMES
        if arg == "-c":
            return False
        if arg in ("-X", "-W"):
            next(args, None)  # 値をとる旗
        elif not arg.startswith("-"):
            return os.path.basename(arg) in PYTEST_NAMES
    return False


def pytest_counts() -> tuple[int, int] | None:
    """(pytest の本体の数, 本体の直下の子のうち Python の作業プロセスの数)。一覧を読めなければ None。

    同じ PID の名前空間から見えるプロセスだけを数える。起動の経路（supervise・AI の Bash・人のシェル）は区別しない。
    """
    try:
        listed = [(p.info["pid"], p.info["ppid"], p.info["cmdline"] or []) for p in psutil.process_iter(["pid", "ppid", "cmdline"])]
    except psutil.Error:
        return None
    mains = {pid for pid, _, line in listed if is_pytest_main(line)}
    workers = sum(1 for pid, ppid, line in listed if ppid in mains and pid not in mains and line and _is_python(line[0]))
    return len(mains), workers


def host_sample(loadavg: Path | None = None, meminfo: Path | None = None) -> dict:
    """進捗ログの `host` に載せる負荷とメモリの値とマシンの上の pytest の数（#2998）。

    読めた値だけを持つ（読めない値は 0 で埋めず鍵を省く）。例外は上げない。
    """
    host: dict = {}
    load = load_average(loadavg or PROC_LOADAVG)
    if load is not None:
        host["load"] = load
    mem = meminfo_mib(meminfo or PROC_MEMINFO)
    for key, name in (("mem_available_mib", "MemAvailable"), ("swap_total_mib", "SwapTotal"), ("swap_free_mib", "SwapFree")):
        if name in mem:
            host[key] = mem[name]
    try:
        counts = pytest_counts()
    except Exception:  # noqa: BLE001 — 記録の行の書き込みを止めない（#2998 の I1）
        counts = None
    if counts is not None:
        host["pytest"], host["pytest_workers"] = counts
    return host
