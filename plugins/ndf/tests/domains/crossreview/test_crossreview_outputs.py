"""`ndf.domains.crossreview.outputs`: 出力の片付けと書き込み（PR 4638 の C4、利用者が選んだ判断 A）。"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from ndf.domains.crossreview import outputs


def _settle(path: Path) -> None:
    outputs.check(path)
    outputs.clear_previous(path)


def test_output_written_by_the_previous_run_is_emptied_in_place(tmp_path: Path) -> None:
    """前の起動が書いた出力は、同じファイル（inode と権限）のまま空に戻し、記録を消す（r1-3）。"""
    out = tmp_path / "h"
    out.touch(mode=0o600)
    out.chmod(0o600)
    outputs.write_recorded(out, "a" * 40 + "\n")
    before = out.stat()
    _settle(out)
    after = out.stat()
    assert out.read_text(encoding="utf-8") == ""
    assert (after.st_ino, stat.S_IMODE(after.st_mode)) == (before.st_ino, 0o600)
    assert not outputs.owner_record(out).exists()


def test_new_output_is_accessible_only_by_the_owner(tmp_path: Path) -> None:
    out = tmp_path / "reviewed-head"
    old = os.umask(0o002)
    try:
        outputs.write_recorded(out, "a" * 40 + "\n")
    finally:
        os.umask(old)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_group_write_is_removed_from_an_existing_output(tmp_path: Path) -> None:
    """呼び手が umask 002 で用意した空の出力は、同じファイルのまま書き込み権限だけを外す（承認の記録の読み手が拒まない）。"""
    out = tmp_path / "reviewed-head"
    out.touch()
    out.chmod(0o664)
    ino = out.stat().st_ino
    _settle(out)
    outputs.write_recorded(out, "x\n")
    assert (out.stat().st_ino, stat.S_IMODE(out.stat().st_mode)) == (ino, 0o644)


def test_on_macos_the_inherited_acl_is_dropped_before_the_files_appear(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """出力と記録は、親から継いだ拡張 ACL を外してから最終の名前へ公開する（PR 4686 の r3-1・r3-2）。"""
    out = tmp_path / "reviewed-head"
    record = outputs.owner_record(out)
    appeared: dict[str, bool] = {}
    monkeypatch.setattr(outputs.worker_paths.sys, "platform", "darwin")
    monkeypatch.setattr(
        outputs.worker_paths, "_drop_inherited_acl", lambda _fd, name: appeared.setdefault(name, (tmp_path / name).exists())
    )
    outputs.write_recorded(out, "x\n")
    assert appeared[out.name] is False and appeared[record.name] is False
    assert [stat.S_IMODE(p.stat().st_mode) for p in (out, record)] == [0o600, 0o600]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([out.name, record.name])


@pytest.mark.parametrize("published", [False, True], ids=["before-link", "after-link"])
def test_on_macos_the_residue_of_a_killed_publish_is_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, published: bool) -> None:
    """公開の途中で強制終了して残った一時ディレクトリは、次の起動が片付けて同じ出力先で続ける（PR 4686 の r4-2・r4-3）。"""
    out = tmp_path / "reviewed-head"
    monkeypatch.setattr(outputs.worker_paths.sys, "platform", "darwin")
    monkeypatch.setattr(outputs.worker_paths, "_has_acl_darwin", lambda _fd: False)
    tmp = tmp_path / outputs.worker_paths._private_tmp_name(out.name)
    tmp.mkdir(mode=0o700)
    (tmp / "data").touch(mode=0o600)
    if published:
        os.link(tmp / "data", out)
    _settle(out)
    assert not tmp.exists()
    outputs.write_recorded(out, "x\n")
    assert out.stat().st_nlink == 1


def test_on_macos_an_existing_file_gets_no_temporary_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """既にある出力には、公開用の一時ディレクトリを作らない（PR 4686 の r4-4）。"""
    out = tmp_path / "reviewed-head"
    out.touch(mode=0o600)
    made: list[str] = []
    monkeypatch.setattr(outputs.worker_paths.sys, "platform", "darwin")
    monkeypatch.setattr(outputs.worker_paths, "_make_tmp_dir", lambda *_args: made.append("tmp"))
    outputs.worker_paths.create_private(out)
    assert made == []


def test_hardlinked_output_is_refused_before_the_review(tmp_path: Path) -> None:
    """承認の記録の読み手が拒むハードリンクのある出力は、何も変えずに `OSError`。"""
    out = tmp_path / "reviewed-head"
    out.touch()
    os.link(out, tmp_path / "other")
    with pytest.raises(OSError, match="ハードリンク"):
        outputs.check(out)
    assert out.stat().st_nlink == 2


def test_record_is_readable_only_by_the_owner(tmp_path: Path) -> None:
    """記録は umask によらず所有者だけが読める（出力の値を他の利用者に見せない。r1-3）。"""
    out = tmp_path / "h"
    old = os.umask(0o022)
    try:
        outputs.write_recorded(out, "x\n")
    finally:
        os.umask(old)
    assert stat.S_IMODE(outputs.owner_record(out).stat().st_mode) == 0o600


@pytest.mark.parametrize("recorded", [None, "b" * 40 + "\n"], ids=["no-record", "other-text"])
def test_file_not_matching_the_record_is_kept(tmp_path: Path, recorded: str | None) -> None:
    """記録が無いか記録と一致しない中身のあるファイルは、変えずに `Unowned`（記録も残す）。"""
    out = tmp_path / "h"
    if recorded is not None:
        outputs.write_recorded(out, recorded)
    out.write_text("利用者のデータ\n", encoding="utf-8")
    with pytest.raises(outputs.Unowned):
        outputs.check(out)
    assert out.read_text(encoding="utf-8") == "利用者のデータ\n"
    assert outputs.owner_record(out).exists() == (recorded is not None)


def test_same_text_in_another_file_is_kept(tmp_path: Path) -> None:
    """記録と同じ中身でも、記録と別のファイル（利用者が作り直した）は変えずに `Unowned`（r1-2）。

    置き換え先は元のファイルが残っている間に作るので、inode は必ず元と違う（r2-5）。"""
    out = tmp_path / "h"
    outputs.write_recorded(out, "x\n")
    other = tmp_path / "other"
    other.write_text("x\n", encoding="utf-8")
    assert other.stat().st_ino != out.stat().st_ino
    os.replace(other, out)
    with pytest.raises(outputs.Unowned):
        outputs.check(out)
    assert out.read_text(encoding="utf-8") == "x\n"


@pytest.mark.parametrize(("written", "held"), [("abcdef\n", b"abc"), ("あ\n", b"\xe3\x81")], ids=["shortened", "cut-in-a-character"])
def test_output_differing_from_the_record_in_the_same_file_is_kept(tmp_path: Path, written: str, held: bytes) -> None:
    """記録と同じファイルでも、中身全体が記録と一致しなければ（利用者が短く編集した・UTF-8 の文字の途中まで）変えずに
    `Unowned`（r2-5・r2-8）。"""
    out = tmp_path / "h"
    outputs.write_recorded(out, written)
    with out.open("r+b") as f:
        f.truncate(0)
        f.write(held)
    with pytest.raises(outputs.Unowned):
        outputs.check(out)
    assert out.read_bytes() == held


def test_write_leaves_only_the_output_and_the_record(tmp_path: Path) -> None:
    """記録は一時のファイルから置き換えるので、書いた後に一時のファイルは残らない（r2-6）。"""
    out = tmp_path / "h"
    outputs.write_recorded(out, "x\n")
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([out.name, outputs.owner_record(out).name])


def test_record_not_written_by_ndf_is_kept(tmp_path: Path) -> None:
    """隣の記録が NDF の記録と読めなければ、出力も記録も変えずに `Unowned`。"""
    out = tmp_path / "h"
    outputs.owner_record(out).write_text("利用者のメモ", encoding="utf-8")
    with pytest.raises(outputs.Unowned):
        outputs.check(out)
    assert outputs.owner_record(out).read_text(encoding="utf-8") == "利用者のメモ"


def test_symlink_output_is_kept(tmp_path: Path) -> None:
    """出力先がシンボリックリンクなら、リンクもリンク先も変えずに `Unowned`。"""
    target = tmp_path / "target"
    target.write_text("", encoding="utf-8")
    out = tmp_path / "h"
    out.symlink_to(target)
    with pytest.raises(outputs.Unowned):
        outputs.check(out)
    assert out.is_symlink() and target.exists()


def test_empty_output_is_kept_and_written(tmp_path: Path) -> None:
    """空のファイルは残し、同じファイルへ書ける。"""
    out = tmp_path / "h"
    out.write_text("", encoding="utf-8")
    ino = out.stat().st_ino
    _settle(out)
    outputs.write_recorded(out, "x\n")
    assert out.read_text(encoding="utf-8") == "x\n" and out.stat().st_ino == ino


def test_missing_parent_is_an_os_error(tmp_path: Path) -> None:
    """親のディレクトリが無ければ `OSError`（相手役を起動する前に止める）。"""
    with pytest.raises(OSError):
        outputs.check(tmp_path / "no-such-dir" / "h")


@pytest.mark.skipif(os.geteuid() == 0, reason="root はディレクトリの権限に関わらず作れる")
def test_record_that_cannot_be_created_is_an_os_error(tmp_path: Path) -> None:
    """空の出力に書けても、隣に記録を作れなければ相手役を起動する前に `OSError`（r1-6）。"""
    out = tmp_path / "h"
    out.write_text("", encoding="utf-8")
    tmp_path.chmod(0o500)
    try:
        with pytest.raises(OSError):
            outputs.check(out)
    finally:
        tmp_path.chmod(0o700)


def test_failed_write_leaves_an_empty_output_and_no_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """出力を書き切れなかった起動は、その場で出力を空に戻し記録を消すので、同じ引数で打ち直せる（r3-3・r3-4）。"""
    out = tmp_path / "h"
    out.touch()
    out.chmod(0o600)
    calls: list[Path] = []
    real = outputs._write_once

    def short(fd: int, data: bytes, path: Path) -> None:
        calls.append(path)
        if path == out:
            os.write(fd, data[:3])
            raise OSError("書き切れない")
        real(fd, data, path)

    monkeypatch.setattr(outputs, "_write_once", short)
    with pytest.raises(OSError):
        outputs.write_recorded(out, "a" * 40 + "\n")
    assert calls == [outputs.owner_record(out), out]
    assert out.read_bytes() == b"" and stat.S_IMODE(out.stat().st_mode) == 0o600
    assert not outputs.owner_record(out).exists()
    monkeypatch.setattr(outputs, "_write_once", real)
    _settle(out)


def test_empty_record_counts_as_no_record(tmp_path: Path) -> None:
    """記録を作った直後に終了して残った空の記録は、記録が無いのと同じに扱い、片付けで消す（r2-6）。"""
    out = tmp_path / "h"
    out.touch()
    outputs.owner_record(out).touch()
    _settle(out)
    assert out.exists() and not outputs.owner_record(out).exists()
