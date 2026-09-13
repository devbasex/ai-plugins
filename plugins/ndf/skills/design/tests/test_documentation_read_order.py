"""`design` SKILL の `documentation` の読む順を固定する（#526 / R3-005）。

**読む順（共通参照 → 出力の形の参照 1 つ）は、触る領域の表のセルが唯一の定義である。**
直後の段落が読む順を言い直していたが、同じ規則を 2 箇所に持つと、形態の追加や参照の変更の
たびに両方を直す必要がある。段落には根拠（形態固有の参照を決めるのは出力の形）だけを残す。

この現状固定テストは、書き換えの前後で次の 2 つが保たれることを示す。

1. `documentation` 行のセルが「共通参照を先に、その後に出力の形の参照を 1 つ」という読む順を持つ
2. 直後の段落が根拠だけを述べ、読む順を言い直さない
"""
from __future__ import annotations

import pathlib

SKILL = pathlib.Path(__file__).resolve().parents[1] / "SKILL.md"


def _documentation_row() -> str:
    body = SKILL.read_text(encoding="utf-8")
    return next(
        line for line in body.split("\n")
        if line.startswith("| **読み手へ渡す文書を作る**")
    )


def _paragraph_after_the_table() -> str:
    """触る領域の表の直後、「触らない領域の参照は読まない。」の次の段落を返す。"""
    body = SKILL.read_text(encoding="utf-8")
    marker = "触らない領域の参照は読まない。"
    rest = body[body.index(marker) + len(marker):].lstrip("\n")
    return rest.split("\n\n", 1)[0]


def test_the_table_cell_keeps_the_read_order() -> None:
    """読む順は `documentation` 行のセルが持つ。共通が先で、形の参照は 1 つ。"""
    row = _documentation_row()
    assert "共通の" in row
    assert "出力の形に当たる参照 1 つ" in row


def test_the_paragraph_states_only_the_rationale() -> None:
    """段落は根拠（形態固有の参照を決めるのは出力の形）だけを残す。"""
    paragraph = _paragraph_after_the_table()
    assert "形態固有の参照を決めるのは、触る領域ではなく出力の形である" in paragraph


def test_the_paragraph_does_not_restate_the_read_order() -> None:
    """段落は読む順を言い直さない。読む順の定義は表のセルに一元化する。"""
    paragraph = _paragraph_after_the_table()
    assert "共通参照を先に読み" not in paragraph
    assert "その後に対象の形のファイル" not in paragraph
