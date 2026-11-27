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
    """記録と同じ中身でも、記録と別のファイル（利用者が作り直した）は変えずに `Unowned`（r1-2）。"""
    out = tmp_path / "h"
    outputs.write_recorded(out, "x\n")
    out.unlink()
    out.write_text("x\n", encoding="utf-8")
    with pytest.raises(outputs.Unowned):
        outputs.check(out)
    assert out.read_text(encoding="utf-8") == "x\n"


def test_partly_written_output_is_emptied(tmp_path: Path) -> None:
    """書く途中で止まった出力（記録と同じファイルで、中身が記録の先頭の部分）は空に戻す。"""
    out = tmp_path / "h"
    outputs.write_recorded(out, "abcdef\n")
    out.write_text("abc", encoding="utf-8")
    _settle(out)
    assert out.read_text(encoding="utf-8") == ""


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
