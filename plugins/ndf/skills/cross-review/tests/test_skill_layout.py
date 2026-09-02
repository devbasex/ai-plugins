"""手順書の構成を固定する（#245）。

`SKILL.md` は 500 行の上限にちょうど張り付いていた。判定に関わらない説明を削らないと
1 行も足せない状態だったため、実行の途中で読まなくてよい 4 節を参照へ移した。

移した内容が失われていないこと、移した先への案内が手順書に残っていること、そして
上限に対する余白があることを固定する。
"""
from __future__ import annotations

import pathlib

import pytest

HERE = pathlib.Path(__file__).resolve().parent.parent
SKILL = HERE / "SKILL.md"
REVIEW_OUTPUT = HERE / "docs/03-review-output.md"

# 上限は `scripts/check-skill-frontmatter.py` の 500 行。余白を 80 行以上残す。
SKILL_MD_MAX_LINES = 420

MOVED_HEADINGS = (
    "## レビュー出力の制約",
    "## CI failure の分類（誤中断防止）",
    "## アンチパターン",
    "## monitor.py が誤って kill する場合の手順",
)


def test_the_skill_has_room_under_the_line_limit() -> None:
    lines = len(SKILL.read_text(encoding="utf-8").splitlines())
    assert lines <= SKILL_MD_MAX_LINES, f"SKILL.md が {lines} 行"


def test_the_moved_document_exists() -> None:
    assert REVIEW_OUTPUT.is_file()


@pytest.mark.parametrize("heading", MOVED_HEADINGS)
def test_the_moved_sections_are_kept(heading: str) -> None:
    """移した内容が失われていないことを、見出しの有無で確かめる。"""
    body = REVIEW_OUTPUT.read_text(encoding="utf-8")
    assert any(line.strip() == heading for line in body.splitlines()), heading


@pytest.mark.parametrize("heading", MOVED_HEADINGS)
def test_the_moved_sections_are_not_left_behind(heading: str) -> None:
    """同じ内容が手順書にも残っていると、片方だけが古くなる。"""
    body = SKILL.read_text(encoding="utf-8")
    assert not any(line.strip() == heading for line in body.splitlines()), heading


def test_the_skill_points_at_the_moved_document() -> None:
    body = SKILL.read_text(encoding="utf-8")
    assert "docs/03-review-output.md" in body
