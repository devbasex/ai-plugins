"""`cross-review run` の出力ファイルの片付けと書き出し（`outputs`。#3702）。リポジトリも相手役も使わない。"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from ndf.domains.crossreview import outputs, store

HEAD = "a" * 40 + "\n"


def test_pr_4638_r1_4_recreated_output_is_not_taken_for_the_previous_one(tmp_path: Path) -> None:
    """PR 4638 の r1-4: 消すと書いた出力の記録も消え、同じパスへ同じ中身で作り直したファイルは NDF の出力とみなさない。"""
    out = tmp_path / "head"
    outputs.write_output(out, HEAD)
    outputs.clear_stale(out)
    assert not out.exists() and not outputs.owner_record(out).exists()
    outputs.write_output(out, HEAD)
    out.unlink()
    out.write_text(HEAD, encoding="utf-8")
    # 時刻の刻みの粗いファイルシステムでも、後から作り直したファイルとして更新時刻を変える
    os.utime(out, ns=(1, 1))
    with pytest.raises(outputs.Unowned):
        outputs.clear_stale(out)
    assert out.read_text(encoding="utf-8") == HEAD


def test_pr_4638_r3_2_output_without_a_record_is_kept(tmp_path: Path) -> None:
    """PR 4638 の r3-2: 記録の無い中身のあるファイルは、中身が何であっても来歴を確かめられないので消さない。"""
    out = tmp_path / "head"
    out.write_text(HEAD, encoding="utf-8")
    with pytest.raises(outputs.Unowned):
        outputs.clear_stale(out)
    assert out.read_text(encoding="utf-8") == HEAD


def test_pr_4638_r2_4_record_location_that_is_not_a_file_is_refused(tmp_path: Path) -> None:
    """PR 4638 の r2-4・r3-7: 記録の保存先に同名のディレクトリがあれば、始めの確かめで `OSError`。"""
    out = tmp_path / "h"
    outputs.owner_record(out).mkdir()
    with pytest.raises(OSError):
        outputs.clear_stale(out)
    assert not out.exists()


@pytest.mark.parametrize("made_empty", [False, True], ids=["absent", "empty"])
def test_pr_4638_r2_4_output_is_restored_when_the_record_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, made_empty: bool
) -> None:
    """PR 4638 の r2-4: 記録を残せなければ、出力を書く前の状態（無い・空）へ戻す（次の起動が記録の無い出力で止まらない）。"""

    def broken(path, value):
        raise OSError("書けない")

    out = tmp_path / "h"
    if made_empty:
        out.touch()
    monkeypatch.setattr(store, "write_object", broken)
    with pytest.raises(OSError):
        outputs.write_output(out, HEAD)
    assert (out.exists(), out.read_text(encoding="utf-8") if made_empty else "") == (made_empty, "")
    outputs.clear_stale(out)


def test_pr_4638_r3_3_empty_output_keeps_its_inode_and_mode(tmp_path: Path) -> None:
    """PR 4638 の r3-3: 呼び手が 0600 で先に作った空の出力先は、その inode と権限のまま書く。"""
    out = tmp_path / "h"
    out.touch(mode=0o600)
    out.chmod(0o600)
    before = out.stat().st_ino
    outputs.clear_stale(out)
    outputs.write_output(out, HEAD)
    assert (out.stat().st_ino, stat.S_IMODE(out.stat().st_mode), out.read_text(encoding="utf-8")) == (before, 0o600, HEAD)


@pytest.mark.skipif(os.geteuid() == 0, reason="root はアクセス権を無視する")
def test_pr_4638_r3_5_parent_without_write_permission_is_refused(tmp_path: Path) -> None:
    """PR 4638 の r3-5: 空の出力先に書けても、親のディレクトリに記録を作れなければ、始めの確かめで `OSError`。"""
    out = tmp_path / "d" / "h"
    out.parent.mkdir()
    out.touch()
    out.parent.chmod(0o500)
    try:
        with pytest.raises(OSError):
            outputs.clear_stale(out)
    finally:
        out.parent.chmod(0o700)
