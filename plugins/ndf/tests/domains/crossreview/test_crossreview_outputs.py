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
    put = outputs.Claim._put

    def broken(self, value):
        if value.keys() & {outputs.STAGING_KEY, outputs.WRITING_KEY}:
            raise OSError("書けない")
        put(self, value)

    out = tmp_path / "h"
    if made_empty:
        out.touch()
    monkeypatch.setattr(outputs.Claim, "_put", broken)
    with pytest.raises(OSError):
        outputs.write_output(out, HEAD)
    monkeypatch.undo()
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


def test_pr_4638_r1_4_record_is_written_before_the_output_is_published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PR 4638 の r1-4・r2-1・r2-7・r2-9・r3-5: 無い出力は、記録を残してから出力の名前へ置き換える。置き換えの前に強制終了
    されて記録と一時のファイルが残っても、次の起動は記録から一時のファイルを見つけて消し、記録も消して続く。記録のための一時の
    ファイルは作らないので、ディレクトリには何も残らない。"""
    out = tmp_path / "h"
    record = outputs.owner_record(out)
    replace = os.replace
    left: dict[str, str] = {}

    def watching(src, dst):
        if Path(dst) != out:  # 出力の名前への置き換えだけを止める
            return replace(src, dst)
        # 強制終了された時点の記録と一時のファイルを残しておく（hard link は inode を保つ）
        left.update(record=record.read_text(encoding="utf-8"), tmp=str(src))
        os.link(src, tmp_path / "kept")
        raise KeyboardInterrupt

    monkeypatch.setattr(outputs.os, "replace", watching)
    with pytest.raises(KeyboardInterrupt):
        outputs.write_output(out, HEAD)
    monkeypatch.setattr(outputs.os, "replace", replace)
    # 例外処理も走らずに残った状態へ戻す
    record.write_text(left["record"], encoding="utf-8")
    os.rename(tmp_path / "kept", left["tmp"])
    outputs.clear_stale(out)
    assert sorted(p.name for p in tmp_path.iterdir()) == []


@pytest.mark.parametrize("written", [0, 10, len(HEAD)], ids=["none", "part", "all"])
def test_pr_4638_r2_8_write_into_an_empty_output_is_resumable(tmp_path: Path, written: int) -> None:
    """PR 4638 の r1-4・r2-8: 空の出力先へ書いている途中か書いた後に、書いた後の記録を残す前に落ちても、次の起動は書きかけの
    記録で今回の書き込みと確かめ、inode を保ったまま空へ戻す。同じ書き込みをやり直せる。"""
    out = tmp_path / "h"
    out.touch()
    before = out.stat().st_ino
    store.write_object(outputs.owner_record(out), outputs._writing_record(out, HEAD))
    out.write_text(HEAD[:written], encoding="utf-8")
    outputs.clear_stale(out)
    assert (out.stat().st_ino, out.read_text(encoding="utf-8"), outputs.owner_record(out).exists()) == (before, "", False)
    outputs.write_output(out, HEAD)
    assert (out.stat().st_ino, out.read_text(encoding="utf-8")) == (before, HEAD)


@pytest.mark.parametrize("output", ["absent", "empty", "filled"])
def test_pr_4638_r2_5_record_not_written_by_ndf_is_kept(tmp_path: Path, output: str) -> None:
    """PR 4638 の r2-5: 記録の名前のファイルでも、NDF が書いた記録と確かめられなければ消さずに止まる。"""
    out = tmp_path / "h"
    if output != "absent":
        out.write_text("" if output == "empty" else HEAD, encoding="utf-8")
    record = outputs.owner_record(out)
    record.write_text('{"note": "利用者のファイル"}', encoding="utf-8")
    with pytest.raises((OSError, outputs.Unowned)):
        outputs.clear_stale(out)
    assert record.read_text(encoding="utf-8") == '{"note": "利用者のファイル"}'


def test_pr_4638_r2_7_staging_file_that_does_not_match_the_record_is_kept(tmp_path: Path) -> None:
    """PR 4638 の r2-7: 記録の指す一時のファイルでも、inode が記録と違えば利用者のファイルとみなして消さない。"""
    out = tmp_path / "h"
    tmp = tmp_path / ".h.0.tmp"
    tmp.write_text("利用者のファイル", encoding="utf-8")
    staging = {"tmp": tmp.name, "dev": tmp.stat().st_dev, "ino": tmp.stat().st_ino + 1}
    store.write_object(outputs.owner_record(out), outputs._record(**{outputs.STAGING_KEY: staging}))
    with pytest.raises(OSError):
        outputs.clear_stale(out)
    assert tmp.read_text(encoding="utf-8") == "利用者のファイル"


@pytest.mark.skipif(os.geteuid() == 0, reason="root はアクセス権を無視する")
def test_pr_4638_r1_5_write_only_empty_output_is_refused(tmp_path: Path) -> None:
    """PR 4638 の r1-5: 書いた後に記録を作るために読み直すので、読めない空の出力先は始めの確かめで `OSError`。"""
    out = tmp_path / "h"
    out.touch()
    out.chmod(0o200)
    with pytest.raises(OSError):
        outputs.clear_stale(out)


def test_pr_4638_r3_4_output_in_use_by_a_running_review_is_not_cleared(tmp_path: Path) -> None:
    """PR 4638 の r3-4: 同じ出力先を実行中の起動が使っていれば、後の起動は片付けずに `Busy` で止まり、先の起動は書き終える。"""
    out = tmp_path / "h"
    with outputs.claim(out) as running:
        with pytest.raises(outputs.Busy):
            outputs.clear_stale(out)
        running.write(HEAD)
    assert out.read_text(encoding="utf-8") == HEAD
    outputs.clear_stale(out)
    assert not out.exists()
