"""`worker_paths` の作業ディレクトリの中の名前の読み書き（`entry_state`・`read_file`・`write_file`・`staged_write`・`remove_file`）。"""

from __future__ import annotations

import errno
import os
import struct
from pathlib import Path

import pytest

from ndf.contracts import worker_paths


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


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="記述子から今の名前を引けない")
def test_write_file_removes_temp_dir_moved_to_another_name(workdir, monkeypatch):
    d, _ = workdir
    real_replace = os.replace

    def move_then_replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        (tmp,) = (p for p in d.iterdir() if p.name.startswith("."))
        tmp.rename(d / "moved")
        real_replace(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(os, "replace", move_then_replace)
    with worker_paths.open_workdir(d) as wd:
        worker_paths.write_file(wd, "f", "書いた")
    assert (d / "f").read_text(encoding="utf-8") == "書いた"
    assert [p.name for p in d.iterdir()] == ["f"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root はアクセス権を無視する")
def test_write_file_keeps_swapped_dir_when_temp_dir_cannot_be_opened(workdir, monkeypatch):
    d, _ = workdir
    real_mkdir = os.mkdir
    swapped = []

    def mkdir_then_swap(path, mode=0o777, *, dir_fd=None):
        real_mkdir(path, mode, dir_fd=dir_fd)
        (d / path).rename(d / "moved")
        real_mkdir(d / path, 0o000)
        swapped.append(d / path)

    monkeypatch.setattr(os, "mkdir", mkdir_then_swap)
    try:
        with worker_paths.open_workdir(d) as wd, pytest.raises(PermissionError):
            worker_paths.write_file(wd, "f", "新")
        assert swapped[0].is_dir()
    finally:
        for p in swapped:
            p.rmdir()


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


def test_staged_write_publishes_only_after_the_body_and_not_when_it_raises(workdir):
    d, _ = workdir
    (d / "f").write_text("元", encoding="utf-8")
    with worker_paths.open_workdir(d) as wd:
        with worker_paths.staged_write(wd, "f", "新"):
            assert (d / "f").read_text(encoding="utf-8") == "元"
        assert (d / "f").read_text(encoding="utf-8") == "新"
        with pytest.raises(OSError), worker_paths.staged_write(wd, "f", "後"):
            raise OSError(28, os.strerror(28))
    assert (d / "f").read_text(encoding="utf-8") == "新"
    assert [p.name for p in d.iterdir()] == ["f"]


def test_write_file_returns_after_publish_even_if_temp_dir_cleanup_fails(workdir, monkeypatch):
    d, _ = workdir
    (d / "f").write_text("元", encoding="utf-8")

    def fail(*args, **kwargs):
        raise OSError(16, os.strerror(16))

    monkeypatch.setattr(os, "rmdir", fail)
    with worker_paths.open_workdir(d) as wd:
        worker_paths.write_file(wd, "f", "新")
    assert (d / "f").read_text(encoding="utf-8") == "新"
    (tmp,) = (p for p in d.iterdir() if p.name != "f")
    assert list(tmp.iterdir()) == []


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
    (kept,) = (p for p in d.iterdir() if p.name.startswith(".ndf-"))
    assert kept.is_dir()
    assert not (d / "f").exists()


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
    (swapped,) = (p for p in d.iterdir() if p.name.startswith(".ndf-"))
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


def test_write_file_keeps_temp_dir_it_cannot_open(workdir, monkeypatch):
    d, _ = workdir
    real_open = os.open

    def fail_on_temp(path, flags, mode=0o777, *, dir_fd=None):
        if str(path).startswith(".ndf-"):
            raise OSError(40, os.strerror(40))
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", fail_on_temp)
    with worker_paths.open_workdir(d) as wd, pytest.raises(OSError):
        worker_paths.write_file(wd, "f", "新")
    # 開けなければ作ったディレクトリだと確かめられないので、名前を消さずに残す（詳細設計の書き込みの手順 4）
    left = list(d.iterdir())
    assert len(left) == 1 and left[0].name.startswith(".ndf-") and left[0].is_dir()


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


# --- #3877 r1: 置き換えたファイルのアクセス権 ---------------------------------------------------------


def test_write_file_keeps_the_mode_and_group_of_a_replaced_file(workdir):
    d, _ = workdir
    (d / "r").write_text("前", encoding="utf-8")
    (d / "r").chmod(0o640)
    before = (d / "r").stat()
    with worker_paths.open_workdir(d) as wd:
        worker_paths.write_file(wd, "r", "後")
        worker_paths.write_file(wd, "new", "作った")
    after = (d / "r").stat()
    assert (after.st_mode & 0o7777, after.st_gid) == (0o640, before.st_gid) and after.st_ino != before.st_ino
    assert (d / "new").stat().st_mode & 0o777 == 0o600


# --- #3877 r3: 所有者を保てない置き換え・長い名前・退けた名前の戻し ------------------------------------


@pytest.mark.parametrize("field", ["uid", "gid"])
def test_write_file_stops_before_replacing_when_the_owner_or_group_cannot_be_kept(workdir, monkeypatch, field):
    # 別の利用者のファイルをグループの書き込み権限で置き換えると、所有者の権限が別の利用者に当たる。公開する前に止める
    d, _ = workdir
    (d / "r").write_text("前", encoding="utf-8")
    (d / "r").chmod(0o620)
    real_entry_state = worker_paths.entry_state

    def other_owner(wd, name):
        st = real_entry_state(wd, name)
        if st is None or name != "r":
            return st
        values = list(st[:10])
        values[4 if field == "uid" else 5] += 1
        return os.stat_result(values)

    def refuse(*args, **kwargs):
        raise PermissionError(errno.EPERM, "refused")

    monkeypatch.setattr(worker_paths, "entry_state", other_owner)
    monkeypatch.setattr(worker_paths.os, "fchown", refuse)
    with pytest.raises(worker_paths.EntryUnsafe) as e, worker_paths.open_workdir(d) as wd:
        worker_paths.write_file(wd, "r", "後")
    assert e.value.kind == "owner"
    assert (d / "r").read_text(encoding="utf-8") == "前" and [p.name for p in d.iterdir()] == ["r"]


def test_write_file_accepts_a_name_at_the_length_limit(workdir):
    # 一時ディレクトリの名前は出力名の長さに依らないので、上限ちょうどの名前も書ける
    d, _ = workdir
    name = "a" * os.pathconf(d, "PC_NAME_MAX")
    with worker_paths.open_workdir(d) as wd:
        worker_paths.write_file(wd, name, "中身")
    assert (d / name).read_text(encoding="utf-8") == "中身" and [p.name for p in d.iterdir()] == [name]


def test_staged_write_restores_retired_names_when_publishing_fails(workdir, monkeypatch):
    d, _ = workdir
    for name, text in (("f", "前"), ("done", ""), ("result", "{}")):
        (d / name).write_text(text, encoding="utf-8")

    def fail(*args, **kwargs):
        raise PermissionError(errno.EPERM, "sticky")

    with worker_paths.open_workdir(d) as wd:
        with worker_paths.staged_write(wd, "f", "", retire=("done", "result", "none")) as actions:
            assert actions == {"done": "removed", "result": "removed", "none": "absent"}
            assert not (d / "done").exists() and not (d / "result").exists()
        assert sorted(p.name for p in d.iterdir()) == ["f"]
        (d / "done").write_text("", encoding="utf-8")
        (d / "result").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(os, "replace", fail)
        with pytest.raises(PermissionError), worker_paths.staged_write(wd, "f", "後", retire=("done", "result")):
            pass
    assert (d / "f").read_text(encoding="utf-8") == "" and (d / "result").read_text(encoding="utf-8") == "{}"
    assert sorted(p.name for p in d.iterdir()) == ["done", "f", "result"]


def test_staged_write_keeps_a_retired_name_whose_place_was_taken(workdir):
    # #3877 r4: 退けた後に別の手が同じ名前へ書いたら、古いもので上書きせずに回収先を知らせる
    d, _ = workdir
    (d / "f").write_text("前", encoding="utf-8")
    (d / "done").write_text("古い", encoding="utf-8")
    (d / "result").write_text("{}", encoding="utf-8")
    with worker_paths.open_workdir(d) as wd:
        with pytest.raises(worker_paths.RetiredKept) as e, worker_paths.staged_write(wd, "f", "", retire=("done", "result")):
            (d / "done").write_text("新しい", encoding="utf-8")
            raise OSError(28, os.strerror(28))
    assert isinstance(e.value.__cause__, OSError)
    ((name, kept),) = e.value.left
    assert name == "done" and kept.read_text(encoding="utf-8") == "古い" and str(kept) in str(e.value)
    assert (d / "done").read_text(encoding="utf-8") == "新しい" and (d / "result").read_text(encoding="utf-8") == "{}"
    assert (d / "f").read_text(encoding="utf-8") == "前"


def _retire_then_take_the_place(d: Path, *, rename_tmp: bool):
    """`done` を退けた後に同じ名前へ新しいファイルを書き、本体を失敗させる（`rename_tmp` なら一時ディレクトリも改名する）。"""
    (d / "f").write_text("前", encoding="utf-8")
    (d / "done").write_text("古い", encoding="utf-8")
    with worker_paths.open_workdir(d) as wd:
        with pytest.raises(worker_paths.RetiredKept) as e, worker_paths.staged_write(wd, "f", "", retire=("done",)):
            if rename_tmp:
                (tmp,) = [p for p in d.iterdir() if p.name.startswith(".ndf-")]
                tmp.rename(d / "moved")
            (d / "done").write_text("新しい", encoding="utf-8")
            raise OSError(28, os.strerror(28))
    return e.value


def test_retired_kept_names_where_the_temp_dir_is_now(workdir):
    # #3877 r5: 回収先は記述子から引いた今の場所で、作った時点のパスではない
    d, _ = workdir
    err = _retire_then_take_the_place(d, rename_tmp=True)
    ((name, kept),) = err.left
    assert name == "done" and kept is not None and kept.parent.name == "moved" and kept.read_text(encoding="utf-8") == "古い"
    assert (d / "done").read_text(encoding="utf-8") == "新しい"


def test_retired_kept_is_not_hidden_by_a_cleanup_failure(workdir, monkeypatch):
    # #3877 r5: 一時ファイルを消せなくても、回収先を持つ例外を呼び出し元へ返す
    d, _ = workdir
    real_unlink = os.unlink

    def refuse_data(path, *, dir_fd=None):
        if path == "data":
            raise OSError(errno.EIO, "refused")
        return real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", refuse_data)
    err = _retire_then_take_the_place(d, rename_tmp=False)
    ((name, kept),) = err.left
    assert name == "done" and kept is not None and kept.read_text(encoding="utf-8") == "古い"


# --- #3877 r6: 拡張 ACL のある既存のファイルは ACL を写さずに置き換えない -------------------------------


def _acl(*entries: tuple[int, int, int]) -> bytes:
    """`system.posix_acl_access` の値（バージョン 2 と、タグ・権限・識別子の並び）。"""
    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *e) for e in entries)


def _set_acl(path: Path) -> None:
    """所有者のほかに別の利用者が読める ACL を付ける。対応しないファイルシステムならテストを飛ばす。"""
    undefined = 0xFFFFFFFF
    acl = _acl((0x01, 6, undefined), (0x02, 4, os.geteuid() + 1), (0x04, 0, undefined), (0x10, 4, undefined), (0x20, 0, undefined))
    try:
        os.setxattr(path, "system.posix_acl_access", acl)
    except OSError:
        pytest.skip("このファイルシステムは POSIX ACL に対応しない")


@pytest.mark.skipif(not hasattr(os, "setxattr"), reason="POSIX ACL を設定できない")
def test_write_file_refuses_to_replace_a_file_that_has_an_extended_acl(workdir):
    d, _ = workdir
    (d / "r").write_text("前", encoding="utf-8")
    (d / "r").chmod(0o640)
    _set_acl(d / "r")
    before = (d / "r").stat()
    with pytest.raises(worker_paths.EntryUnsafe) as e, worker_paths.open_workdir(d) as wd:
        worker_paths.write_file(wd, "r", "後")
    assert e.value.kind == "acl"
    after = (d / "r").stat()
    assert (d / "r").read_text(encoding="utf-8") == "前" and after.st_ino == before.st_ino
    assert [p.name for p in d.iterdir()] == ["r"]


@pytest.mark.skipif(not hasattr(os, "setxattr"), reason="POSIX ACL を設定できない")
def test_write_file_replaces_a_file_without_an_acl_and_drops_the_inherited_one(workdir):
    d, _ = workdir
    undefined = 0xFFFFFFFF
    # 所有者は rwx（一時ディレクトリが x を失うとその中にファイルを作れない）、別の利用者は読めるという既定の ACL
    default = _acl((0x01, 7, undefined), (0x02, 4, os.geteuid() + 1), (0x04, 0, undefined), (0x10, 4, undefined), (0x20, 0, undefined))
    try:
        os.setxattr(d, "system.posix_acl_default", default)  # 下に作るファイルが継ぐ既定の ACL
    except OSError:
        pytest.skip("このファイルシステムは POSIX ACL に対応しない")
    (d / "r").write_text("前", encoding="utf-8")
    os.removexattr(d / "r", "system.posix_acl_access")
    (d / "r").chmod(0o640)
    with worker_paths.open_workdir(d) as wd:
        worker_paths.write_file(wd, "r", "後")
    with pytest.raises(OSError) as e:
        os.getxattr(d / "r", "system.posix_acl_access")
    assert e.value.errno == errno.ENODATA
    assert (d / "r").stat().st_mode & 0o777 == 0o640 and (d / "r").read_text(encoding="utf-8") == "後"
