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


# ---- 手順書の分割（#330） ----
#
# `docs/01-state-and-review.md` は 508 行で、分割の基準（501 行以上）を超えていた。
# 契約（状態ファイルの形式と入出力の取り決め）を `docs/04-contracts.md` へ移し、
# 手順だけを残した。
#
# **行数の上限は `cross-review` の中だけで固定する。** 検査を
# `scripts/check-skill-frontmatter.py` へ入れると、このバッチのどの担当の範囲にも
# 入っていない文書で落ちる（#354）。

DOCS = HERE / "docs"
PROCEDURE = DOCS / "01-state-and-review.md"
CONTRACTS = DOCS / "04-contracts.md"

# 分割の基準は 501 行以上。`SKILL.md` の上限と同じ値を文書にも当てる。
DOC_MAX_LINES = 500
# 手順書は 420 行以下に保つ。次に節を足す担当のために 80 行の余白を残す。
PROCEDURE_MAX_LINES = 420

MOVED_CONTRACT_HEADINGS = (
    "## 状態ファイル",
    "### 重要なフィールド",
    "## AI への入出力契約（両 launcher 共通）",
    "## AI が書き出すファイル契約",
)


def _line_count(path: pathlib.Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


@pytest.mark.parametrize(
    "doc", sorted(DOCS.glob("*.md")), ids=lambda p: p.name
)
def test_the_review_docs_stay_under_the_split_threshold(doc: pathlib.Path) -> None:
    lines = _line_count(doc)
    assert lines <= DOC_MAX_LINES, f"{doc.name} が {lines} 行"


def test_the_procedure_has_room_for_the_next_change() -> None:
    lines = _line_count(PROCEDURE)
    assert lines <= PROCEDURE_MAX_LINES, f"docs/01 が {lines} 行"


def test_the_contract_document_exists() -> None:
    assert CONTRACTS.is_file()


@pytest.mark.parametrize("heading", MOVED_CONTRACT_HEADINGS)
def test_the_contract_sections_are_kept(heading: str) -> None:
    """移した内容が失われていないことを、見出しの有無で確かめる。"""
    body = CONTRACTS.read_text(encoding="utf-8")
    assert any(line.strip() == heading for line in body.splitlines()), heading


@pytest.mark.parametrize("heading", MOVED_CONTRACT_HEADINGS)
def test_the_contract_sections_are_not_left_behind(heading: str) -> None:
    """同じ内容が手順書にも残っていると、片方だけが古くなる。"""
    body = PROCEDURE.read_text(encoding="utf-8")
    assert not any(line.strip() == heading for line in body.splitlines()), heading


def test_the_procedure_points_at_the_contract_document() -> None:
    assert "04-contracts.md" in PROCEDURE.read_text(encoding="utf-8")


def test_the_skill_points_at_the_contract_document() -> None:
    assert "docs/04-contracts.md" in SKILL.read_text(encoding="utf-8")
