"""Markdown の行の種類とコードのコメントのアダプター（`ndf.adapters.markdown`）。"""

from __future__ import annotations

import pytest

from ndf.adapters import markdown

MIXED = "\n".join(
    [
        "# 題",  # 0
        "",  # 1
        "段落",  # 2
        "",  # 3
        "| a | b |",  # 4
        "| --- | --- |",  # 5
        "| 1 | 2 |",  # 6
        "",  # 7
        "> 引用",  # 8
        "",  # 9
        "```py",  # 10
        "## 中",  # 11
        "```",  # 12
        "",  # 13
        "    インデント",  # 14
    ]
)


def test_block_kinds_mixed_document() -> None:
    assert markdown.block_kinds(MIXED) == [
        "heading",
        "other",
        "other",
        "other",
        "table",
        "table",
        "table",
        "other",
        "quote",
        "other",
        "code",
        "code",
        "code",
        "other",
        "code",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", []),
        ("```\na\n\nb", ["code", "code", "code", "code"]),
        ("~~~\n```\n~~~\nx", ["code", "code", "code", "other"]),
        ("> | a |\n> | --- |\n> ```\n> c\n> ```", ["quote"] * 5),
        ("題\n==\n", ["heading", "heading"]),
        ("a\r\n\r\n```\r\nb\r\n```\r\n", ["other", "other", "code", "code", "code"]),
    ],
    ids=["empty", "unclosed-fence", "backticks-in-tilde", "outer-quote-wins", "setext", "crlf"],
)
def test_block_kinds_boundaries(text: str, expected: list[str]) -> None:
    kinds = markdown.block_kinds(text)
    assert kinds == expected
    assert set(kinds) <= set(markdown.BLOCK_KINDS)


def test_code_lines_include_nested_and_indented_code() -> None:
    text = "- 項目\n\n  ```\n  c\n  ```\n\n      ind\n\nend"
    assert markdown.code_lines(text) == [False, False, True, True, True, False, True, False, False]


def test_atx_headings_skip_code_quote_and_setext() -> None:
    text = "# A\n> ## 引用\n```\n## コード\n```\nS\n==\n### B ###\n"
    assert markdown.atx_headings(text) == [
        markdown.Heading(0, 1, "A"),
        markdown.Heading(7, 3, "B"),
    ]


@pytest.mark.parametrize(
    ("lines", "suffix", "expected"),
    [
        (['s = "#x"  # c'], ".py", ['s = "#x"     ']),
        (['"""', "# 文字列", '"""  # c'], ".py", ['"""', "# 文字列", '"""     ']),
        (['echo "${x#y}" # c', "x=1 #c2", "a#b"], ".sh", ['echo "${x#y}"    ', "x=1    ", "a#b"]),
        (["a = 1 // c", "/* b", " c */ x"], ".js", ["a = 1     ", "    ", "      x"]),
        (["let a: number = 1 // c"], ".ts", ["let a: number = 1     "]),
        (["# not a comment"], ".md", ["# not a comment"]),
    ],
    ids=["py", "py-triple-quote", "sh", "js", "ts", "unknown-suffix"],
)
def test_mask_comments(lines: list[str], suffix: str, expected: list[str]) -> None:
    masked = markdown.mask_comments(lines, suffix)
    assert masked == expected
    assert [len(x) for x in masked] == [len(x) for x in lines]


def test_tables_reads_the_header_and_cells_with_line_numbers() -> None:
    """#2912: 最上位の表の見出しと行の欄（インラインのままの文）と、0 始まりの行番号。"""
    (table,) = markdown.gfm_tables(MIXED)
    assert table == markdown.Table(4, ("a", "b"), (markdown.TableRow(6, ("1", "2")),))


def test_tables_keeps_inline_code_and_pads_short_rows() -> None:
    text = "| 判断 | 番号 |\n| --- | --- |\n| `a \\| b` を足す | `C2` |\n| 空 |\n|  | |\n"
    (table,) = markdown.gfm_tables(text)
    assert [r.cells for r in table.rows] == [("`a | b` を足す", "`C2`"), ("空", ""), ("", "")]
    assert [r.line for r in table.rows] == [2, 3, 4]


def test_tables_skips_tables_in_code_and_quotes() -> None:
    text = "```\n| a | b |\n| - | - |\n| 1 | 2 |\n```\n\n> | q | r |\n> | - | - |\n> | 1 | 2 |\n"
    assert markdown.gfm_tables(text) == []
