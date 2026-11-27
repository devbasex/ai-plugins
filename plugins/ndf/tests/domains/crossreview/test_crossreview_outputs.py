"""`ndf.domains.crossreview.outputs`: 出力の片付けと書き込み（PR 4638 の C4、利用者が選んだ判断 A）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.domains.crossreview import outputs


def test_output_written_by_the_previous_run_is_cleared(tmp_path: Path) -> None:
    """前の起動が書いた出力（記録と一致する）は、出力と記録の両方を消す。"""
    out = tmp_path / "h"
    outputs.write_recorded(out, "a" * 40 + "\n")
    outputs.clear_previous(out)
    assert not out.exists() and not outputs.owner_record(out).exists()


@pytest.mark.parametrize("recorded", [None, "b" * 40 + "\n"], ids=["no-record", "other-text"])
def test_file_not_matching_the_record_is_kept(tmp_path: Path, recorded: str | None) -> None:
    """記録が無いか記録と一致しない中身のあるファイルは、消さずに `Unowned`（記録も残す）。"""
    out = tmp_path / "h"
    if recorded is not None:
        outputs.write_recorded(out, recorded)
    out.write_text("利用者のデータ\n", encoding="utf-8")
    with pytest.raises(outputs.Unowned):
        outputs.clear_previous(out)
    assert out.read_text(encoding="utf-8") == "利用者のデータ\n"
    assert outputs.owner_record(out).exists() == (recorded is not None)


def test_record_not_written_by_ndf_is_kept(tmp_path: Path) -> None:
    """隣の記録が NDF の記録と読めなければ、出力も記録も消さずに `Unowned`。"""
    out = tmp_path / "h"
    outputs.owner_record(out).write_text("利用者のメモ", encoding="utf-8")
    with pytest.raises(outputs.Unowned):
        outputs.clear_previous(out)
    assert outputs.owner_record(out).read_text(encoding="utf-8") == "利用者のメモ"


def test_symlink_output_is_kept(tmp_path: Path) -> None:
    """出力先がシンボリックリンクなら、リンクもリンク先も消さずに `Unowned`。"""
    target = tmp_path / "target"
    target.write_text("", encoding="utf-8")
    out = tmp_path / "h"
    out.symlink_to(target)
    with pytest.raises(outputs.Unowned):
        outputs.clear_previous(out)
    assert out.is_symlink() and target.exists()


def test_empty_output_is_kept_and_written(tmp_path: Path) -> None:
    """空のファイルは消さずに残し、そこへ書ける。"""
    out = tmp_path / "h"
    out.write_text("", encoding="utf-8")
    outputs.clear_previous(out)
    assert out.exists()
    outputs.write_recorded(out, "x\n")
    assert out.read_text(encoding="utf-8") == "x\n"


def test_missing_parent_is_an_os_error(tmp_path: Path) -> None:
    """親のディレクトリが無ければ `OSError`（相手役を起動する前に止める）。"""
    with pytest.raises(OSError):
        outputs.clear_previous(tmp_path / "no-such-dir" / "h")
