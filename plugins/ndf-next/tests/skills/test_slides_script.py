"""現状固定: 台本（script.md）の読み取り（`deck-frame.js` の `parseScript`）と尺の集計（`script-time.js`）。

期待値は今の実装の出力である。正しさを主張しない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ._slides import DECK_FRAME, needs_node, node

pytestmark = needs_node


def parse(text: str) -> object:
    """`parseScript(text, "s.md")` の戻り値か、投げた DeckError の `{message, code}`。"""
    dm = json.dumps(str(DECK_FRAME))
    code = f"""const m = require({dm});
try {{ out(m.parseScript({json.dumps(text)}, "s.md")); }} catch (e) {{ out({{ message: e.message, code: e.code, deck: e instanceof m.DeckError }}); }}"""
    return node(code)


def page(no: int, title: str, seconds: int | None = None, paragraphs: list[str] | None = None) -> dict:
    ps = paragraphs or []
    return {"no": no, "title": title, "seconds": seconds, "paragraphs": ps, "reading": "\n".join(ps)}


# ---- parseScript（I-007） ----


def test_reads_pages_paragraphs_and_seconds():
    text = "# 台本\n\n- 想定\n\n## 1. 表紙（20秒）\n\n覚え書き\n\n> 一行目\n> 続き **強調** と `コード`\n>\n> 二段落目\n\n## 2. 本編 (30 秒)\n> 本編\n"
    assert parse(text) == [
        page(1, "表紙", 20, ["一行目続き 強調 と コード", "二段落目"]),
        page(2, "本編", 30, ["本編"]),
    ]


def test_crlf_lines_are_joined_into_one_paragraph():
    assert parse("## 1. A\r\n> x\r\n> y\r\n") == [page(1, "A", None, ["xy"])]


def test_seconds_only_at_the_end_of_the_heading():
    text = "## 1. A (5秒)\n## 2. B（ 7 秒 ）\n## 3. C（秒）\n## 4. D（5秒）後\n"
    assert parse(text) == [page(1, "A", 5), page(2, "B", 7), page(3, "C（秒）"), page(4, "D（5秒）後")]


def test_unnumbered_heading_stops_reading_until_the_next_page():
    # `#` と `##` の番号の無い見出しの下は読まない。`###` は見出しとして扱わず、読む状態も戻さない
    text = "## 1. A\n> a\n# 番号なし\n> 読まない\n## メモ\n> 読まない\n### 小見出し\n> 読む\n## 2. B\n>no space\n>   spaced   \n"
    assert parse(text) == [page(1, "A", None, ["a"]), page(2, "B", None, ["no spacespaced"])]


def test_sub_heading_inside_a_page_is_an_ordinary_line():
    text = "## 1. A\n> a\n### 小見出し\n> b\n"
    assert parse(text) == [page(1, "A", None, ["a", "b"])]


def test_non_quote_line_and_blank_line_end_a_paragraph():
    assert parse("## 1. A\n> a\ntext\n> b\n\n> c\n") == [page(1, "A", None, ["a", "b", "c"])]


def test_pages_are_sorted_by_number():
    assert parse("## 2. B\n> b\n## 1. A\n> a\n") == [page(1, "A", None, ["a"]), page(2, "B", None, ["b"])]


def test_page_zero_is_allowed_and_heading_needs_space_after_hashes():
    assert parse("## 0. Z\n## 1. A\n") == [page(0, "Z"), page(1, "A")]
    assert parse("##1. A\n## 1.A\n") == [page(1, "A")]
    assert parse("## 1.\n> a\n") == [page(1, "", None, ["a"])]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "台本にページの区切り（## <番号>. <見出し>）が無い: s.md"),
        ("# x\n> y\n", "台本にページの区切り（## <番号>. <見出し>）が無い: s.md"),
        ("## 1. A\n## 1. B\n## 2. C\n## 2. D\n", "台本のページの番号が重なる: 1, 2（s.md）"),
        ("## 1. A\n## 4. D\n", "台本のページの番号が欠ける: 2, 3（s.md）"),
    ],
)
def test_malformed_script_raises_script_error(text: str, message: str):
    assert parse(text) == {"message": message, "code": 2, "deck": True}


def test_read_script_reads_the_file_and_reports_missing(tmp_path: Path):
    f = tmp_path / "script.md"
    f.write_text("## 1. A\n> a\n", encoding="utf-8")
    bad = tmp_path / "bad.md"
    bad.write_text("覚え書きだけ\n", encoding="utf-8")
    dm = json.dumps(str(DECK_FRAME))
    missing = str(tmp_path / "none.md")
    code = f"""const m = require({dm});
const err = (p) => {{ try {{ m.readScript(p); return null; }} catch (e) {{ return [e.message, e.code]; }} }};
out([m.readScript({json.dumps(str(f))}), err({json.dumps(missing)}), err({json.dumps(str(bad))})]);"""
    assert node(code) == [
        [page(1, "A", None, ["a"])],
        [f"台本が無い: {missing}", 1],
        [f"台本にページの区切り（## <番号>. <見出し>）が無い: {bad}", 2],
    ]
