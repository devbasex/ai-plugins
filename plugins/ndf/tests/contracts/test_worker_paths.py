from __future__ import annotations

import os
from pathlib import Path

import pytest

from ndf.contracts import worker_paths


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_report_paths_returns_four_files_under_directory():
    paths = worker_paths.report_paths(Path("/w"), "n")
    assert paths == worker_paths.ReportPaths(
        prompt=Path("/w/n.prompt.md"),
        report=Path("/w/n.report.md"),
        done=Path("/w/n.report.md.done"),
        result=Path("/w/n.result.json"),
    )


@pytest.mark.parametrize("name", ["a", "a" * 64])
def test_report_paths_accepts_names_of_1_and_64_chars(name):
    assert worker_paths.report_paths(Path("/w"), name).report == Path(f"/w/{name}.report.md")


def test_report_paths_absolutizes_relative_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    paths = worker_paths.report_paths(Path("d"), "n")
    assert all(p.is_absolute() and p.parent == tmp_path.resolve() / "d" for p in paths)


def test_report_paths_does_not_resolve_link_named_result(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    d = tmp_path / "d"
    d.mkdir()
    (d / "n.result.json").symlink_to(outside)
    assert worker_paths.report_paths(d, "n").result == d.resolve() / "n.result.json"


@pytest.mark.parametrize("name", ["", "../x", "a/b", "日本語", "a" * 65])
def test_report_paths_rejects_bad_names_without_touching_files(tmp_path, name):
    before = _tree(tmp_path)
    with pytest.raises(worker_paths.NameInvalid):
        worker_paths.report_paths(tmp_path, name)
    assert _tree(tmp_path) == before


@pytest.fixture
def workdir(tmp_path):
    """作業ディレクトリ（`d`）と、その外にある利用者のファイル（`outside`）。"""
    d = tmp_path / "d"
    d.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("利用者の中身", encoding="utf-8")
    return d, outside


def test_open_workdir_gives_fd_for_names_and_closes_on_exit(workdir):
    d, _ = workdir
    (d / "f").write_text("x", encoding="utf-8")
    with worker_paths.open_workdir(d) as wd:
        assert wd.path == d.resolve()
        assert os.stat("f", dir_fd=wd.fd).st_size == 1
    with pytest.raises(OSError):
        os.fstat(wd.fd)


def test_open_workdir_resolves_link_to_directory_once(tmp_path, workdir):
    d, _ = workdir
    link = tmp_path / "link"
    link.symlink_to(d)
    with worker_paths.open_workdir(link) as wd:
        assert os.fstat(wd.fd).st_ino == d.stat().st_ino


def test_open_workdir_stays_on_opened_directory_after_swap(tmp_path, workdir):
    d, _ = workdir
    (d / "r").write_text("元", encoding="utf-8")
    (d / "gone").write_text("消す", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    for n in ("r", "w", "gone"):
        (other / n).write_text("差し替え先", encoding="utf-8")
    with worker_paths.open_workdir(d) as wd:
        d.rename(tmp_path / "moved")
        d.symlink_to(other)
        assert worker_paths.entry_state(wd, "w") is None
        assert worker_paths.read_file(wd, "r") == "元"
        worker_paths.write_file(wd, "w", "新")
        assert worker_paths.remove_file(wd, "gone") == "removed"
    assert {n: (other / n).read_text(encoding="utf-8") for n in ("r", "w", "gone")} == dict.fromkeys(("r", "w", "gone"), "差し替え先")
    assert (tmp_path / "moved" / "w").read_text(encoding="utf-8") == "新"


def test_entry_state_none_for_missing_and_stat_for_regular_file(workdir):
    d, _ = workdir
    (d / "f").write_text("abc", encoding="utf-8")
    (d / "empty").write_text("", encoding="utf-8")
    with worker_paths.open_workdir(d) as wd:
        assert worker_paths.entry_state(wd, "none") is None
        assert worker_paths.entry_state(wd, "f").st_size == 3
        assert worker_paths.entry_state(wd, "empty").st_size == 0


def _make_entries(d: Path, outside: Path) -> None:
    (d / "sym").symlink_to(outside)
    (d / "dangling").symlink_to(d / "nowhere")
    os.link(outside, d / "hard")
    (d / "dir").mkdir()
    os.mkfifo(d / "fifo")


@pytest.mark.parametrize(
    ("name", "kind"),
    [("sym", "symlink"), ("dangling", "symlink"), ("hard", "hardlink"), ("dir", "directory"), ("fifo", "other")],
)
def test_entry_state_rejects_links_and_non_regular_files(workdir, name, kind):
    d, outside = workdir
    _make_entries(d, outside)
    with worker_paths.open_workdir(d) as wd, pytest.raises(worker_paths.EntryUnsafe) as e:
        worker_paths.entry_state(wd, name)
    assert (e.value.name, e.value.kind) == (name, kind)
    assert isinstance(e.value, worker_paths.EntryError)


def test_read_file_returns_text_none_or_empty(workdir):
    d, _ = workdir
    (d / "f").write_text("中身", encoding="utf-8")
    (d / "empty").write_text("", encoding="utf-8")
    with worker_paths.open_workdir(d) as wd:
        assert worker_paths.read_file(wd, "f") == "中身"
        assert worker_paths.read_file(wd, "none") is None
        assert worker_paths.read_file(wd, "empty") == ""


@pytest.mark.parametrize(
    ("name", "kind"),
    [("sym", "symlink"), ("dangling", "symlink"), ("hard", "hardlink"), ("dir", "directory"), ("fifo", "other")],
)
def test_read_file_rejects_links_without_returning_target(workdir, name, kind):
    d, outside = workdir
    _make_entries(d, outside)
    with worker_paths.open_workdir(d) as wd, pytest.raises(worker_paths.EntryUnsafe) as e:
        worker_paths.read_file(wd, name)
    assert (e.value.name, e.value.kind) == (name, kind)
    assert "利用者の中身" not in str(e.value)


def test_read_file_wraps_bad_utf8_as_unreadable(workdir):
    d, _ = workdir
    (d / "bad").write_bytes(b"\xff\xfe")
    with worker_paths.open_workdir(d) as wd, pytest.raises(worker_paths.EntryUnreadable) as e:
        worker_paths.read_file(wd, "bad")
    assert e.value.name == "bad" and e.value.reason


@pytest.mark.skipif(os.geteuid() == 0, reason="root はアクセス権を無視する")
def test_read_file_wraps_open_error_as_unreadable(workdir):
    d, _ = workdir
    (d / "locked").write_text("x", encoding="utf-8")
    (d / "locked").chmod(0o000)
    with worker_paths.open_workdir(d) as wd, pytest.raises(worker_paths.EntryUnreadable) as e:
        worker_paths.read_file(wd, "locked")
    assert e.value.reason == os.strerror(13)


def test_read_file_reads_opened_inode_after_name_swap(workdir, monkeypatch):
    d, outside = workdir
    (d / "f").write_text("開いた中身", encoding="utf-8")
    real_fstat = os.fstat

    def swap_then_fstat(fd):
        if (d / "f").is_file() and not (d / "f").is_symlink():
            (d / "f").rename(d / "kept")
            (d / "f").symlink_to(outside)
        return real_fstat(fd)

    with worker_paths.open_workdir(d) as wd:
        monkeypatch.setattr(os, "fstat", swap_then_fstat)
        text = worker_paths.read_file(wd, "f")
    assert text == "開いた中身"
    assert (d / "f").is_symlink()


def test_write_file_creates_and_replaces_with_new_inode(workdir):
    d, _ = workdir
    (d / "old").write_text("前", encoding="utf-8")
    before = (d / "old").stat().st_ino
    with worker_paths.open_workdir(d) as wd:
        worker_paths.write_file(wd, "new", "作った")
        worker_paths.write_file(wd, "old", "後")
        worker_paths.write_file(wd, "empty", "")
    assert (d / "new").read_text(encoding="utf-8") == "作った"
    assert (d / "old").read_text(encoding="utf-8") == "後"
    assert (d / "old").stat().st_ino != before
    assert (d / "empty").stat().st_size == 0
    assert sorted(p.name for p in d.iterdir()) == ["empty", "new", "old"]


@pytest.mark.parametrize(("name", "kind"), [("sym", "symlink"), ("hard", "hardlink")])
def test_write_file_rejects_links_and_keeps_target(workdir, name, kind):
    d, outside = workdir
    _make_entries(d, outside)
    mtime = outside.stat().st_mtime_ns
    with worker_paths.open_workdir(d) as wd, pytest.raises(worker_paths.EntryUnsafe) as e:
        worker_paths.write_file(wd, name, "書き換え")
    assert e.value.kind == kind
    assert outside.read_text(encoding="utf-8") == "利用者の中身"
    assert outside.stat().st_mtime_ns == mtime


def test_write_file_replaces_name_swapped_to_link_after_check(workdir, monkeypatch):
    d, outside = workdir
    real_entry_state = worker_paths.entry_state

    def check_then_swap(wd, name):
        st = real_entry_state(wd, name)
        (d / name).symlink_to(outside)
        return st

    monkeypatch.setattr(worker_paths, "entry_state", check_then_swap)
    with worker_paths.open_workdir(d) as wd:
        worker_paths.write_file(wd, "f", "新")
    assert outside.read_text(encoding="utf-8") == "利用者の中身"
    assert not (d / "f").is_symlink()
    assert (d / "f").read_text(encoding="utf-8") == "新"


def test_write_file_publishes_written_inode_even_if_temp_name_is_swapped(workdir, monkeypatch):
    d, _ = workdir
    real_replace = os.replace

    def swap_then_replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        (tmp,) = (p for p in d.iterdir() if p.name.startswith("."))
        tmp.rename(d / "moved")
        tmp.mkdir()
        (tmp / "data").write_text("差し替え", encoding="utf-8")
        try:
            real_replace(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
        finally:
            (tmp / "data").unlink()
            tmp.rmdir()
            (d / "moved").rename(tmp)

    monkeypatch.setattr(os, "replace", swap_then_replace)
    with worker_paths.open_workdir(d) as wd:
        worker_paths.write_file(wd, "f", "書いた")
    assert (d / "f").read_text(encoding="utf-8") == "書いた"
    assert [p.name for p in d.iterdir()] == ["f"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root はアクセス権を無視する")
def test_write_file_refuses_read_only_file(workdir):
    d, _ = workdir
    (d / "ro").write_text("読み取り専用", encoding="utf-8")
    (d / "ro").chmod(0o444)
    ino = (d / "ro").stat().st_ino
    with worker_paths.open_workdir(d) as wd, pytest.raises(PermissionError):
        worker_paths.write_file(wd, "ro", "書き換え")
    assert (d / "ro").read_text(encoding="utf-8") == "読み取り専用"
    assert (d / "ro").stat().st_ino == ino
    assert [p.name for p in d.iterdir()] == ["ro"]


def test_write_file_failure_leaves_no_temp_and_keeps_original(workdir, monkeypatch):
    d, _ = workdir
    (d / "f").write_text("元", encoding="utf-8")

    def fail(*args, **kwargs):
        raise OSError(28, os.strerror(28))

    monkeypatch.setattr(os, "replace", fail)
    with worker_paths.open_workdir(d) as wd, pytest.raises(OSError):
        worker_paths.write_file(wd, "f", "新")
    assert (d / "f").read_text(encoding="utf-8") == "元"
    assert [p.name for p in d.iterdir()] == ["f"]


def test_write_file_rejects_temp_dir_that_is_not_private(workdir, monkeypatch):
    d, _ = workdir
    real_mkdir = os.mkdir

    def mkdir_then_widen(path, mode=0o777, *, dir_fd=None):
        real_mkdir(path, mode, dir_fd=dir_fd)
        os.chmod(path, 0o755, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", mkdir_then_widen)
    with worker_paths.open_workdir(d) as wd, pytest.raises(worker_paths.EntryUnsafe) as e:
        worker_paths.write_file(wd, "f", "新")
    assert e.value.kind == "other"
    assert list(d.iterdir()) == []


def test_write_file_accepts_temp_dir_inheriting_setgid(workdir, monkeypatch):
    d, _ = workdir
    real_mkdir = os.mkdir

    def mkdir_with_setgid(path, mode=0o777, *, dir_fd=None):
        real_mkdir(path, mode, dir_fd=dir_fd)
        os.chmod(path, 0o2700, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", mkdir_with_setgid)
    with worker_paths.open_workdir(d) as wd:
        worker_paths.write_file(wd, "f", "新")
    assert (d / "f").read_text(encoding="utf-8") == "新"
    assert [p.name for p in d.iterdir()] == ["f"]


def test_write_file_keeps_data_in_swapped_temp_dir_when_check_fails(workdir, monkeypatch):
    d, _ = workdir
    real_mkdir = os.mkdir

    def mkdir_then_swap(path, mode=0o777, *, dir_fd=None):
        real_mkdir(path, mode, dir_fd=dir_fd)
        (d / path).rename(d / "moved")
        real_mkdir(d / path, 0o755)
        (d / path).chmod(0o755)
        (d / path / "data").write_text("別の手の中身", encoding="utf-8")

    monkeypatch.setattr(os, "mkdir", mkdir_then_swap)
    with worker_paths.open_workdir(d) as wd, pytest.raises(worker_paths.EntryUnsafe):
        worker_paths.write_file(wd, "f", "新")
    (swapped,) = (p for p in d.iterdir() if p.name.startswith(".f."))
    assert (swapped / "data").read_text(encoding="utf-8") == "別の手の中身"
    assert not (d / "f").exists()


@pytest.mark.parametrize("name", ["", ".", "..", "sub/f", "../outside.txt"])
@pytest.mark.parametrize(
    "call",
    [
        lambda wd, name: worker_paths.entry_state(wd, name),
        lambda wd, name: worker_paths.read_file(wd, name),
        lambda wd, name: worker_paths.write_file(wd, name, "新"),
        lambda wd, name: worker_paths.remove_file(wd, name),
    ],
    ids=["entry_state", "read_file", "write_file", "remove_file"],
)
def test_file_operations_reject_names_that_are_not_one_entry(workdir, name, call):
    d, outside = workdir
    (d / "sub").mkdir()
    (d / "sub" / "f").write_text("下の中身", encoding="utf-8")
    with worker_paths.open_workdir(d) as wd, pytest.raises(worker_paths.EntryUnsafe) as e:
        call(wd, name)
    assert e.value.kind == "other"
    assert outside.read_text(encoding="utf-8") == "利用者の中身"
    assert (d / "sub" / "f").read_text(encoding="utf-8") == "下の中身"


def test_file_operations_reject_absolute_path(workdir):
    d, outside = workdir
    with worker_paths.open_workdir(d) as wd:
        for call in (worker_paths.read_file, worker_paths.remove_file):
            with pytest.raises(worker_paths.EntryUnsafe):
                call(wd, str(outside))
        with pytest.raises(worker_paths.EntryUnsafe):
            worker_paths.write_file(wd, str(outside), "新")
    assert outside.read_text(encoding="utf-8") == "利用者の中身"


def test_write_file_removes_temp_dir_it_cannot_open(workdir, monkeypatch):
    d, _ = workdir
    real_open = os.open

    def fail_on_temp(path, flags, mode=0o777, *, dir_fd=None):
        if str(path).startswith(".f."):
            raise OSError(40, os.strerror(40))
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", fail_on_temp)
    with worker_paths.open_workdir(d) as wd, pytest.raises(OSError):
        worker_paths.write_file(wd, "f", "新")
    assert list(d.iterdir()) == []


def test_remove_file_removes_then_reports_absent(workdir):
    d, _ = workdir
    (d / "f").write_text("x", encoding="utf-8")
    with worker_paths.open_workdir(d) as wd:
        assert worker_paths.remove_file(wd, "f") == "removed"
        assert worker_paths.remove_file(wd, "f") == "absent"
        assert worker_paths.remove_file(wd, "none") == "absent"
    assert list(d.iterdir()) == []


@pytest.mark.parametrize(("name", "kind"), [("sym", "symlink"), ("hard", "hardlink")])
def test_remove_file_keeps_link_and_target_when_link_at_check(workdir, name, kind):
    d, outside = workdir
    _make_entries(d, outside)
    with worker_paths.open_workdir(d) as wd, pytest.raises(worker_paths.EntryUnsafe) as e:
        worker_paths.remove_file(wd, name)
    assert e.value.kind == kind
    assert os.path.lexists(d / name)
    assert outside.read_text(encoding="utf-8") == "利用者の中身"


def test_remove_file_removes_link_name_swapped_after_check(workdir, monkeypatch):
    d, outside = workdir
    (d / "f").write_text("x", encoding="utf-8")
    real_entry_state = worker_paths.entry_state

    def check_then_swap(wd, name):
        st = real_entry_state(wd, name)
        (d / name).unlink()
        (d / name).symlink_to(outside)
        return st

    monkeypatch.setattr(worker_paths, "entry_state", check_then_swap)
    with worker_paths.open_workdir(d) as wd:
        assert worker_paths.remove_file(wd, "f") == "removed"
    assert not os.path.lexists(d / "f")
    assert outside.read_text(encoding="utf-8") == "利用者の中身"


@pytest.mark.parametrize("mode", [0o775, 0o777])
def test_write_file_writes_into_shared_directory_without_sticky_bit(workdir, mode):
    d, _ = workdir
    d.chmod(mode)
    with worker_paths.open_workdir(d) as wd:
        worker_paths.write_file(wd, "f", "新")
    assert (d / "f").read_text(encoding="utf-8") == "新"
    assert [p.name for p in d.iterdir()] == ["f"]


def test_write_file_accepts_shared_directory_with_sticky_bit(workdir):
    d, _ = workdir
    d.chmod(0o1777)
    with worker_paths.open_workdir(d) as wd:
        worker_paths.write_file(wd, "f", "新")
    assert (d / "f").read_text(encoding="utf-8") == "新"
    assert [p.name for p in d.iterdir()] == ["f"]
