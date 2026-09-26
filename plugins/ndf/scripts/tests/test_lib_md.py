"""Markdown の構造の読み取りの包み（lib/md.py・#1142 の決定 19）。外部パッケージは全体テストの環境（根の pyproject.toml）が入れる。

今の実装（囲みを `^\\s*(```|~~~)` で開け閉めする 9 本）と読み方が変わる入力を、ここで固定する:
閉じは開きと同じ記号で同じ長さ以上・4 桁の字下げは囲みにならない・閉じていない囲みは文書の終わりまで続く。
"""
from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import markdown_it  # noqa: E402,F401  包みの外部パッケージ。無ければ集めるところで落とす
import md  # noqa: E402

DOC = """# 題

前書き

## 手順

```bash
# 見出しではない
echo '## これも'
```

### 細目

| 列 | 値 \\| 説明 |
| --- | ---: |
| a | [先](other.md#x) |

## 手順

<div>
## HTML の中
</div>
"""


def test_headings_skip_fences_and_number_duplicate_anchors():
    hs = md.headings(DOC)
    assert [(h.level, h.title, h.line) for h in hs] == [(1, "題", 0), (2, "手順", 4), (3, "細目", 11), (2, "手順", 17)]
    assert [h.anchor for h in hs] == ["題", "手順", "細目", "手順-1"]


def test_sections_run_to_the_next_heading_of_the_same_or_shallower_level():
    s = md.section_named(DOC, "手順")
    assert (s.start, s.end) == (5, 17)
    assert md.section_named(DOC, "細目", level=3).end == 17
    assert md.section_named(DOC, "無い") is None
    assert [x.heading.title for x in md.md_sections(DOC)] == ["題", "手順", "細目", "手順"]


def test_tables_unescape_pipes_and_links_keep_the_line():
    (t,) = md.tables(DOC)
    assert t.header == ["列", "値 | 説明"] and t.rows == [["a", "[先](other.md#x)"]]
    assert (t.start, t.end) == (13, 16)
    assert md.links(DOC) == [md.Link("other.md#x", "先", 15)]
    assert md.links("a\n[日本](#%E6%97%A5)\n")[0].href == "#日"
    assert md.links("x\ny [a](#b)\n")[0].line == 1


def test_fences_follow_commonmark():
    text = "````md\n```\ninner\n```\n````\n    ```\n    indented\n~~~\nopen to the end\n"
    assert md.fences(text) == [("md", 0, 5), ("", 7, 9)]
    mask = md.fenced_lines(text)
    assert mask[:5] == [True] * 5 and mask[7:] == [True, True]
    assert mask[5] and mask[6]  # 4 桁の字下げはコードブロック（囲みではないが地の文でもない）


def test_prose_lines_drop_code_and_html_blocks():
    prose = [ln for _, ln in md.prose_lines(DOC)]
    assert "echo '## これも'" not in prose and "## HTML の中" not in prose
    assert "前書き" in prose and "### 細目" in prose


def test_heading_anchor_follows_github():
    assert md.heading_anchor("Sub `code` (x)!") == "sub-code-x"
    assert md.heading_anchor("決定 19: 包み（ラッパー）") == "決定-19-包みラッパー"
    assert md.headings("# A `b` [c](d)\n")[0].title == "A b c"
