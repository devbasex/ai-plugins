"""プロセスの生死・親子・木の停止・メモリの包み（lib/procs.py・#1142 の決定 19）。外部パッケージは全体テストの環境（根の pyproject.toml）が入れる。"""

from __future__ import annotations

import errno
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import psutil  # noqa: E402,F401  包みの外部パッケージ。無ければ集めるところで落とす
import procs  # noqa: E402


def spawn_tree() -> subprocess.Popen:
    """子を 1 つ持つシェルを、別のプロセスグループの先頭として起こす。"""
    p = subprocess.Popen(["sh", "-c", "sleep 30 & sleep 30; wait"], start_new_session=True)
    deadline = time.monotonic() + 5
    while len(procs.child_pids(p.pid)) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    return p


def test_alive_children_cmdline_and_stop_tree():
    p = spawn_tree()
    kids = procs.child_pids(p.pid)
    assert procs.pid_alive(p.pid) and len(kids) == 2
    assert procs.leads_own_group(p.pid)
    assert procs.cmdline_contains(p.pid, "SLEEP 30") is True
    assert procs.parent_and_name(p.pid)[0] == os.getpid()
    assert os.getpid() in procs.ancestor_pids(p.pid) or procs.ancestor_pids(p.pid)[0] == os.getpid()
    assert procs.stop_tree(p.pid, grace=2)
    p.wait(timeout=5)
    assert not any(procs.pid_alive(k) for k in kids)


def test_zombie_counts_as_dead_and_missing_pids_are_quiet():
    p = subprocess.Popen(["true"])
    deadline = time.monotonic() + 5
    while not procs.pid_is_zombie(p.pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert procs.pid_is_zombie(p.pid) and not procs.pid_alive(p.pid)
    p.wait()
    assert not procs.pid_alive(p.pid) and procs.cmdline_contains(p.pid, "x") is None
    assert procs.parent_and_name(0) is None and procs.child_pids(-1) == [] and procs.stop_tree(0)
    assert procs.wait_gone(p.pid, timeout=0.1)


def test_memory_and_cgroup(tmp_path: Path):
    avail, total = procs.memory_available()
    assert 0 < avail <= total
    (tmp_path / "memory.max").write_text("1048576\n")
    (tmp_path / "memory.current").write_text("524288\n")
    (tmp_path / "memory.events").write_text("low 0\nmax 3\noom_kill 2\n")
    assert procs.cgroup_memory(tmp_path) == procs.CgroupMemory(1048576, 524288, 2, False)
    (tmp_path / "memory.max").write_text("max\n")
    assert procs.cgroup_memory(tmp_path).unlimited and procs.cgroup_memory(tmp_path).limit is None
    assert procs.cgroup_memory(tmp_path / "none") == procs.CgroupMemory(None, None, None, False)


def test_cgroup_anon(tmp_path: Path):
    """`memory.stat` の anon をバイトで返す。読めない・行が無い・数値でなければ None で、ほかの 4 つは変わらない。"""
    (tmp_path / "memory.max").write_text("1048576\n")
    (tmp_path / "memory.current").write_text("524288\n")
    (tmp_path / "memory.events").write_text("oom_kill 2\n")
    assert procs.cgroup_memory(tmp_path).anon is None
    (tmp_path / "memory.stat").write_text("file 4096\nanon 2299527168\nanon_thp 0\n")
    assert procs.cgroup_memory(tmp_path) == procs.CgroupMemory(1048576, 524288, 2, False, 2299527168)
    (tmp_path / "memory.stat").write_text("file 4096\n")
    assert procs.cgroup_memory(tmp_path).anon is None
    (tmp_path / "memory.stat").write_text("anon x\n")
    assert procs.cgroup_memory(tmp_path).anon is None


def test_memory_pressure_some_avg10(tmp_path: Path):
    path = tmp_path / "psi"
    path.write_text("some avg10=0.05 avg60=0.83 avg300=1.18 total=1\nfull avg10=0.04 avg60=0.80 avg300=1.10 total=1\n")
    assert procs.memory_pressure_some_avg10(path) == "0.05"
    path.write_text("full avg10=0.04 avg60=0.80 avg300=1.10 total=1\n")
    assert procs.memory_pressure_some_avg10(path) is None
    path.write_text("some avg10=x avg60=0.83 avg300=1.18 total=1\n")
    assert procs.memory_pressure_some_avg10(path) is None
    assert procs.memory_pressure_some_avg10(tmp_path / "none") is None


def test_swap_io_pages(tmp_path: Path):
    path = tmp_path / "vmstat"
    path.write_text("nr_free_pages 1\npswpin 1147782\npswpout 2990899\n")
    assert procs.swap_io_pages(path) == 1147782 + 2990899
    path.write_text("pswpin 1\n")
    assert procs.swap_io_pages(path) is None
    path.write_text("pswpin 1\npswpout x\n")
    assert procs.swap_io_pages(path) is None
    assert procs.swap_io_pages(tmp_path / "none") is None


def test_cgroup_dir_inside_a_container_and_on_a_host(tmp_path: Path):
    root, own = tmp_path / "cg", tmp_path / "self"
    root.mkdir()
    own.write_text("0::/user.slice/x.scope\n")
    assert procs.cgroup_dir(root, own) == root / "user.slice/x.scope"
    (root / "memory.events").write_text("")
    assert procs.cgroup_dir(root, own) == root


def _raising_wait(exc: BaseException):
    def wait_procs(procs_, timeout=None):
        raise exc

    return wait_procs


@pytest.mark.parametrize("code", [errno.EINVAL, errno.ESRCH])
def test_stop_tree_treats_einval_and_esrch_while_waiting_as_stopped(monkeypatch, code):
    """待ちの途中の `os.pidfd_open` の EINVAL / ESRCH は外へ出さず、止まったものとみなす（#1817）。"""
    p = spawn_tree()
    try:
        monkeypatch.setattr(procs.psutil, "wait_procs", _raising_wait(OSError(code, os.strerror(code))))
        assert procs.stop_tree(p.pid, grace=0)
    finally:
        monkeypatch.undo()
        procs.stop_tree(p.pid, grace=0)
        p.wait(timeout=5)


def test_stop_tree_still_kills_the_rest_when_one_pid_raises_while_waiting(monkeypatch):
    """1 つの pid の EINVAL で残りを空にせず、TERM を無視して生きている子へ SIGKILL を送る（#1817）。"""
    p = subprocess.Popen(["sh", "-c", "trap '' TERM; sleep 30 & sleep 30; wait"], start_new_session=True)
    deadline = time.monotonic() + 5
    while len(procs.child_pids(p.pid)) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    kids = procs.child_pids(p.pid)
    real = procs.psutil.wait_procs

    def wait_procs(procs_, timeout=None):
        if len(procs_) > 1 or procs_[0].pid == p.pid:
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))
        return real(procs_, timeout=timeout)

    try:
        monkeypatch.setattr(procs.psutil, "wait_procs", wait_procs)
        assert procs.stop_tree(p.pid, grace=0.2)
        assert len(kids) == 2
        assert all(not procs.pid_alive(k) or procs.pid_is_zombie(k) for k in kids)
    finally:
        monkeypatch.undo()
        procs.stop_tree(p.pid, grace=0)
        p.wait(timeout=5)


@pytest.mark.parametrize("exc", [OSError(errno.EPERM, "x"), RuntimeError("x")])
def test_stop_tree_raises_other_errors_while_waiting(monkeypatch, exc):
    """EINVAL / ESRCH 以外の errno と、OSError 以外の例外は握りつぶさない（#1817）。"""
    p = spawn_tree()
    try:
        monkeypatch.setattr(procs.psutil, "wait_procs", _raising_wait(exc))
        with pytest.raises(type(exc)):
            procs.stop_tree(p.pid, grace=0)
    finally:
        monkeypatch.undo()
        procs.stop_tree(p.pid, grace=0)
        p.wait(timeout=5)
