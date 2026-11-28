"""文書の基準の本文の読み出し（`ndf.contracts.writing`）。#4285 の受け入れ条件 7。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf import contracts
from ndf.contracts import writing


@pytest.fixture
def criteria_file(tmp_path: Path) -> Path:
    """一時のコントラクトの保存先の基準のファイル（まだ無い）。"""
    path = tmp_path / "contracts" / writing.CRITERIA
    path.parent.mkdir(parents=True)
    contracts.use_dir(tmp_path / "contracts")
    return path


def test_text_is_the_shipped_file() -> None:
    assert writing.criteria() == (contracts.contracts_dir() / writing.CRITERIA).read_text(encoding="utf-8")


def test_types_section_is_cut_at_the_next_section() -> None:
    section = writing.types_section()
    assert section in writing.criteria()
    assert [line for line in section.splitlines() if line.startswith("## ")] == [writing.TYPES_HEADING]


def test_types_section_keeps_subsections_and_reads_to_the_end(criteria_file: Path) -> None:
    criteria_file.write_text(f"# 基準\n\n{writing.TYPES_HEADING}\n\n型\n\n### 区分\n\n2 つ\n", encoding="utf-8")
    assert writing.types_section() == f"{writing.TYPES_HEADING}\n\n型\n\n### 区分\n\n2 つ"


def test_a_missing_file_is_a_contract_error(criteria_file: Path) -> None:
    with pytest.raises(contracts.ContractError, match="読めない"):
        writing.criteria()


def test_a_missing_heading_is_a_contract_error(criteria_file: Path) -> None:
    criteria_file.write_text("# 基準\n\n## 別の見出し\n", encoding="utf-8")
    with pytest.raises(contracts.ContractError, match="見出し"):
        writing.types_section()


BEGIN, END = writing.DISTINCTION_BEGIN, writing.DISTINCTION_END


def test_issue_4543_marked_ranges_are_removed_and_the_rest_is_kept() -> None:
    """範囲は目印の行で決まる（見出しの文を問わない）。組が複数あればすべて外し、範囲の外の行と改行は変えない。"""
    text = f"前\n{BEGIN}\n### どんな見出しでも\n中\n{END}\n間\n  {BEGIN}  \n中 2\n{END}\n後"
    assert writing.without_distinction(text) == "前\n間\n後"


@pytest.mark.parametrize("text", ["", "目印の無い本文\n", f"文の中で {BEGIN} に触れた行は数えない\n"])
def test_issue_4543_a_text_without_marker_lines_is_unchanged(text: str) -> None:
    assert writing.without_distinction(text) == text


@pytest.mark.parametrize(
    "text",
    [f"{BEGIN}\n中\n", f"中\n{END}\n", f"{BEGIN}\n{BEGIN}\n{END}\n{END}\n"],
    ids=["始まりだけ", "終わりだけ", "入れ子"],
)
def test_issue_4543_unpaired_markers_are_broken_criteria(text: str) -> None:
    with pytest.raises(writing.CriteriaBroken, match="目印"):
        writing.without_distinction(text)


def test_issue_4543_the_shipped_types_section_has_a_removable_range() -> None:
    """配っている基準の型のセクションに目印の組がある（消すと落ちる）。"""
    section = writing.types_section()
    stripped = writing.without_distinction(section)
    assert stripped != section and stripped != "" and writing.without_distinction(writing.criteria()) != writing.criteria()
