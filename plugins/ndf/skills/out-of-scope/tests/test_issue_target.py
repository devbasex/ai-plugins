"""起票先の判断が 1 か所にあり、手順から引けることを固定する（#229）。

起票先を決める基準を持つのはこの Skill だけである。`retrospective` は参照だけを持つ。
判定の基準を持つ場所を 1 つにする考え方は `development-workflow` のモード判定と同じである。

読み取れないこと自体も失敗として扱う。表の書き方を変えるだけでこの検査を無効にできる形に
しない。
"""
from __future__ import annotations

import pytest

from issue_target_helpers import (
    DECISION_TABLE_HEADING,
    REFERENCE,
    RESOLUTION_TABLE_HEADING,
    SKILL,
    command_lines,
    headings,
    read,
    table,
)

# 手順の見出しの並び。**起票先を決める段は 3 択の直後に来る。** 起票先が要るのは
# 「起票する」を選んだときだけで、重複の確認もその起票先に対して行う。
EXPECTED_STEPS = [
    "1. 範囲かを照合する",
    "2. 3 択で決める",
    "3. 起票先を決める",
    "4. 重複を確かめる",
    "5. 起票する",
    "6. 由来を残す",
]


def test_the_decision_table_lists_three_kinds_and_their_target() -> None:
    """判断表は 3 つの性質と、それぞれの起票先を持つ。"""
    header, rows = table(read(REFERENCE), DECISION_TABLE_HEADING)
    assert header[1] == "起票先", f"2 列目が起票先ではない: {header}"
    assert len(rows) == 3, f"判断表の行数が 3 ではない: {rows}"
    assert all(row[0] for row in rows), f"性質が空の行がある: {rows}"
    assert all("リポジトリ" in row[1] for row in rows), f"起票先がリポジトリを指していない: {rows}"


def test_the_resolution_table_has_three_stages() -> None:
    """起票先のリポジトリの解決は 3 段で、段 3 は止まる側に倒す。"""
    _, rows = table(read(REFERENCE), RESOLUTION_TABLE_HEADING)
    assert [row[0] for row in rows] == ["1", "2", "3"], f"段の並びが違う: {rows}"
    assert "NDF_SKILL_REPO" in rows[0][1], f"段 1 が環境変数を見ていない: {rows[0]}"
    assert "remote.origin.url" in rows[1][1], f"段 2 が取得元の clone を見ていない: {rows[1]}"


def test_the_reference_passes_the_target_to_gh() -> None:
    """重複の確認と起票が、決めた起票先に対して行われる。"""
    lines = command_lines(read(REFERENCE))
    for command in ("gh issue list", "gh issue create"):
        found = [line for line in lines if command in line]
        assert found, f"呼び出しが無い: {command}"
        assert all("--repo" in line for line in found), f"起票先が渡されていない: {found}"


def test_the_steps_keep_their_order() -> None:
    """起票先を決める段が 3 択の直後にある。"""
    assert headings(read(SKILL), "### ") == EXPECTED_STEPS


def test_the_consent_shows_the_target() -> None:
    """起票の前に取る同意の提示に、起票先が含まれる。"""
    lines = [line for line in read(SKILL).splitlines() if "同意を取る" in line]
    assert lines, "同意を取る提示が見つからない"
    assert all("起票先" in line for line in lines), f"提示に起票先が無い: {lines}"


def test_the_skill_points_at_the_reference() -> None:
    """手順が判断表の置き場所を指している。"""
    assert "references/issue-target.md" in read(SKILL)


@pytest.mark.parametrize("heading", [DECISION_TABLE_HEADING, RESOLUTION_TABLE_HEADING])
def test_an_unreadable_table_fails(heading: str) -> None:
    """表を読み取れないことは、素通りではなく失敗になる。"""
    with pytest.raises(AssertionError):
        table("見出しの無い本文", heading)
