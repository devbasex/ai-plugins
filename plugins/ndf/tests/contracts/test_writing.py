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


def test_readers_section_keeps_subsections_and_is_cut_at_the_next_section(criteria_file: Path) -> None:
    criteria_file.write_text(f"# 基準\n\n{writing.READERS_HEADING}\n\n表\n\n### 複数\n\n条件\n\n## 次の章\n", encoding="utf-8")
    assert writing.readers_section() == f"{writing.READERS_HEADING}\n\n表\n\n### 複数\n\n条件"


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
