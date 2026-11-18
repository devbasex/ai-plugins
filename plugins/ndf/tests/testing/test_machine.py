from __future__ import annotations

from pathlib import Path

from ndf.testing import machine

GIB = 1024**3


def _cgroup(tmp_path: Path, own: str, mount_root: str = "/") -> tuple[Path, Path, Path]:
    root = tmp_path / "cgroup"
    (root / own.strip("/")).mkdir(parents=True, exist_ok=True)
    proc_cgroup = tmp_path / "proc-cgroup"
    proc_cgroup.write_text(f"0::{own}\n")
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(f"30 1 0:26 {mount_root} {root} rw,nosuid shared:4 - cgroup2 cgroup2 rw\n")
    return root, proc_cgroup, mountinfo


def test_limits_come_from_the_own_cgroup_and_its_ancestors(tmp_path):
    root, proc_cgroup, mountinfo = _cgroup(tmp_path, "/user.slice/run.scope")
    leaf, parent = root / "user.slice" / "run.scope", root / "user.slice"
    (leaf / "memory.max").write_text("max\n")
    (leaf / "memory.current").write_text(str(1 * GIB))
    (parent / "memory.max").write_text(str(4 * GIB))
    (parent / "memory.current").write_text(str(3 * GIB))
    (leaf / "cpu.max").write_text("400000 100000\n")
    (parent / "cpu.max").write_text("200000 100000\n")
    dirs = machine.cgroup_dirs(root, proc_cgroup, mountinfo)
    assert dirs == (leaf, parent, root)
    assert machine.cgroup_memory_limit(dirs) == 4 * GIB
    assert machine.cgroup_memory_remaining(dirs) == 1 * GIB
    assert machine.cpu_count(dirs) <= 2


def test_mounted_subtree_resolves_relative_to_the_mount_root(tmp_path):
    root, proc_cgroup, mountinfo = _cgroup(tmp_path, "/docker/abc", mount_root="/docker/abc")
    (root / "memory.max").write_text(str(2 * GIB))
    dirs = machine.cgroup_dirs(root, proc_cgroup, mountinfo)
    assert dirs == (root,)
    assert machine.cgroup_memory_limit(dirs) == 2 * GIB


def test_unresolvable_membership_falls_back_to_the_mount_point(tmp_path):
    root, proc_cgroup, mountinfo = _cgroup(tmp_path, "/")
    proc_cgroup.write_text("0::/gone/elsewhere\n")
    assert machine.cgroup_dirs(root, proc_cgroup, mountinfo) == (root,)
    assert machine.cgroup_dirs(root, tmp_path / "missing", tmp_path / "missing") == (root,)
    assert machine.cgroup_memory_remaining((root,)) is None
