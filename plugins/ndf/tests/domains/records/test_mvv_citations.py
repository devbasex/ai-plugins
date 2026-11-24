"""設計文書の決定が MVV のどの項目を根拠にしたかを書いた 1 行（`- 根拠: Value 5（MVV バージョン 11）`）の解析と検証（#4076・#4090）。"""

from __future__ import annotations

from typing import Any

import pytest

from ndf.domains.records import mvv
from ndf.domains.records import mvv_decide as md

# 承認済みのバージョン 11 の項目
CATALOG = {11: ("Mission", "Vision", "Value 3", "Value 6", "Value 7", "Value 11", "P1", "C1")}
CITED_OLD = "- 根拠: Value 1（MVV バージョン 1）"


def _outside_code(text: str) -> list[int]:
    """コードブロックの外にある、根拠の記述の行の番号。"""
    inside = mvv.fenced_lines(text)
    return [no for no, line in enumerate(text.splitlines(), 1) if md.parse_citation(line, False) and no not in inside]


def _errors(line: str, decision: bool = False, catalog: Any = None) -> list[dict[str, Any]]:
    return md.citation_errors([("docs/a.md", 7, line, decision)], CATALOG if catalog is None else catalog)


def _error(cited: str, kind: str, version: Any = None, unknown: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"path": "docs/a.md", "line": 7, "cited": cited, "kind": kind, "version": version, "unknown": list(unknown)}


# コードブロックの内外


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (f"```\n{CITED_OLD}\n```\n{CITED_OLD}\n", [4]),
        (f"````md\n```\n{CITED_OLD}\n```\n{CITED_OLD}\n````\n\n{CITED_OLD}\n", [8]),
        (f"- 例\n\n  ````md\n  ```\n  {CITED_OLD}\n  ````\n\n{CITED_OLD}\n", [8]),
        (f"~~~\n```\n{CITED_OLD}\n~~~\n{CITED_OLD}\n", [5]),
        (f"```\n{CITED_OLD}\n", []),
        (f"本文\n\n    {CITED_OLD}\n", []),
    ],
    ids=["3 字", "4 字の中の 3 字", "リストの中の 4 字", "チルダの中のバッククォート", "終わりの無いフェンス", "インデント"],
)
def test_citations_inside_code_blocks_are_told_apart(text: str, want: list[int]) -> None:
    assert _outside_code(text) == want


def test_fenced_lines_counts_the_fences_and_the_lines_between_them_from_one() -> None:
    assert mvv.fenced_lines("a\n```\nb\n```\nc\n") == {2, 3, 4}


# 決定の見出しの下


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("## 決定の記録\n\n### 決定 1: 題\n\n- 根拠: x\n\n### 決定 2\n- 根拠: y\n## 影響\nz\n", {4, 5, 6, 8}),
        ("### 決定 1: 題\na\n#### 補足\nb\n", {2}),
        ("### 決定 12: 題\na\n\nb", {2, 3, 4}),
        ("### 決定 1: 題\r\na\r\n## 次\r\nb\r\n", {2}),
        ("## 決定 1\na\n### 決定事項\nb\n### 決定 1つ\nc\n", set()),
        ("```\n### 決定 1\na\n```\nb\n", set()),
        ("", set()),
    ],
    ids=["次の見出しまで", "小見出しまで", "文書の末尾まで", "CRLF", "決定の見出しでない", "コードの中の見出し", "空"],
)
def test_decision_lines_are_those_under_a_decision_heading(text: str, want: set[int]) -> None:
    assert mvv.decision_lines(text) == want


# 1 行の解析


@pytest.mark.parametrize(
    ("line", "items"),
    [
        ("- 根拠: Value 6 / P1（MVV バージョン 11）", ("Value 6", "P1")),
        ("根拠: Value 6（MVV バージョン 11）", ("Value 6",)),
        ("  - 根拠: Value 6（MVV バージョン 11）\r", ("Value 6",)),
        ("- 根拠: Value 7「…」・Value 11「…」（MVV バージョン 11）", ("Value 7", "Value 11")),
        ("- 根拠: Value 7「a（b） / c・d」・Value 11「外「内」・e」（MVV バージョン 11）", ("Value 7", "Value 11")),
        ("- 根拠: Value 3「（MVV バージョン 9）」（MVV バージョン 11）", ("Value 3",)),
        ("- 根拠: Value 6 / スプリント Value 4（MVV バージョン 11・スプリント MVV 3f9a1c2e）", ("Value 6",)),
        ("- 根拠: Value 3（MVV バージョン 11）。STD1 / PRD2", ("Value 3",)),
        ("- 根拠: 根拠なし（MVV バージョン 11）。STD1", ()),
    ],
)
def test_parse_citation_reads_the_items_and_the_version(line: str, items: tuple[str, ...]) -> None:
    got = md.parse_citation(line, False)
    assert got is not None
    assert (got.items, got.version) == (items, 11)


@pytest.mark.parametrize(
    ("line", "decision"),
    [
        ("- 根拠: Value 3「…」（MVV 版 11）", False),
        ("- 根拠: Value 5（MVV バージョン 11x）", False),
        ("- 根拠: Value 5（MVV バージョン 11", False),
        ("- 根拠: Value 5（MVV バージョン）", False),
        ("- 根拠: Value 5（MVV バージョン 11・スプリント MVV 3f9a）", False),
        ("- 根拠: Value 5（MVV バージョン 11・補足）", False),
        ("- 根拠: Value 5（MVV なし）", False),
        ("- 根拠: Value 5「終わりの無い引用（MVV バージョン 11）", False),
        ("- 根拠: Value 3", True),
        ("- 根拠: STD1", True),
        ("- 根拠:", True),
    ],
)
def test_parse_citation_of_an_unreadable_version_has_no_version(line: str, decision: bool) -> None:
    got = md.parse_citation(line, decision)
    assert got is not None
    assert got.version is None


@pytest.mark.parametrize(
    ("line", "decision"),
    [
        ("- 根拠: 実測", False),
        ("| 形 | `- 根拠: Value 3（MVV バージョン 1）` |", True),
        ("- 例は「根拠: Value 3（MVV バージョン 1）」と書く", True),
        ("> - 根拠: Value 3（MVV バージョン 1）", True),
        ("- **根拠**: Value 3（MVV バージョン 1）", True),
        ("", True),
    ],
)
def test_parse_citation_skips_lines_that_are_not_citations(line: str, decision: bool) -> None:
    assert md.parse_citation(line, decision) is None


# 承認済みの MVV との突き合わせ


@pytest.mark.parametrize(
    ("line", "decision", "want"),
    [
        (CITED_OLD, False, _error("Value 1", "version", 1)),
        ("- 根拠: Value 3（MVV バージョン 12）", True, _error("Value 3", "version", 12)),
        ("- 根拠: Value 18 / Value 3（MVV バージョン 11）", True, _error("Value 18 / Value 3", "unknown", 11, ("Value 18",))),
        ("- 根拠: （MVV バージョン 11）", True, _error("", "unknown", 11, ("",))),
        ("- 根拠: Value 3「…」（MVV 版 11）", False, _error("Value 3", "format")),
        ("- 根拠: Value 5（MVV バージョン 11x）", False, _error("Value 5", "format")),
        ("- 根拠: Value 5（MVV バージョン 11", False, _error("Value 5", "format")),
        ("- 根拠: 根拠なし（MVV なし）", False, _error("根拠なし", "format")),
        ("- 根拠: Value 3「…」", True, _error("Value 3", "format")),
    ],
)
def test_citation_errors_report_one_error_per_line(line: str, decision: bool, want: dict[str, Any]) -> None:
    assert _errors(line, decision) == [want]


@pytest.mark.parametrize(
    "line",
    [
        "- 根拠: Value 7「…」・Value 11「…」（MVV バージョン 11）",
        "- 根拠: Mission / P1 / C1（MVV バージョン 11）",
        "- 根拠: Value 6 / スプリント Value 40（MVV バージョン 11・スプリント MVV 3f9a1c2e）",
        "- 根拠: 根拠なし（MVV バージョン 11）。STD1",
        "本文の引用「根拠: Value N（MVV バージョン 1）」",
    ],
)
def test_citation_errors_pass_correct_citations(line: str) -> None:
    assert _errors(line, True) == []


def test_citation_errors_look_up_the_items_of_the_cited_version() -> None:
    catalog = {11: ("Value 3",), 12: ("Value 3", "Value 18")}
    assert _errors("- 根拠: Value 18（MVV バージョン 12）", catalog=catalog) == []
    assert _errors("- 根拠: Value 18（MVV バージョン 11）", catalog=catalog) == [_error("Value 18", "unknown", 11, ("Value 18",))]


def test_breaking_the_version_notation_does_not_hide_a_wrong_version() -> None:
    before, after = _errors("- 根拠: Value 3（MVV バージョン 1）"), _errors("- 根拠: Value 3（MVV 版 1）")
    assert [e["kind"] for e in before + after] == ["version", "format"]


def test_citation_errors_keep_the_order_of_the_lines() -> None:
    rows = [("b.md", 2, CITED_OLD, False), ("a.md", 9, "本文", True), ("a.md", 1, "- 根拠: Value 3", True)]
    assert [(e["path"], e["line"], e["kind"]) for e in md.citation_errors(rows, CATALOG)] == [("b.md", 2, "version"), ("a.md", 1, "format")]


def test_citation_errors_refuse_rows_that_do_not_say_whether_the_line_is_under_a_decision() -> None:
    rows: Any = [("a.md", 1, "- 根拠: Value 3")]
    with pytest.raises(TypeError):
        md.citation_errors(rows, CATALOG)
