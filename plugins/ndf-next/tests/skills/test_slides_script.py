"""現状固定: 台本（script.md）の読み取り（`deck-frame.js` の `parseScript`）と尺の集計（`script-time.js`）。

期待値は今の実装の出力である。正しさを主張しない。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ._slides import DECK_FRAME, NODE, SCRIPT_TIME, needs_node, node

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


# ---- script-time.js の CLI（I-008・I-012・I-014） ----

# 字数は 表紙 5・本編 12。見出しの `|` は表で `\|` に置き換わる
SAMPLE = "## 1. 表紙（20秒）\n> あいう えお\n## 2. 本編|縦\n> **強調**と`コード`\n>\n> 二段落目です\n"
USAGE = "usage: node lib/script-time.js <台本> [--wpm 350] [--limit <分>] [--json]"


def cli(cwd: Path, *args: str) -> tuple[int, str, str]:
    r = subprocess.run([NODE, str(SCRIPT_TIME), *args], capture_output=True, text=True, cwd=cwd, timeout=60, check=False)
    return r.returncode, r.stdout, r.stderr


@pytest.fixture
def work(tmp_path: Path) -> Path:
    (tmp_path / "s.md").write_text(SAMPLE, encoding="utf-8")
    return tmp_path


def sample_json(wpm: float, s1: float, s2: float, total: float) -> dict:
    return {
        "wpm": wpm,
        "pages": [
            {"no": 1, "title": "表紙", "chars": 5, "seconds": s1, "planned": 20},
            {"no": 2, "title": "本編|縦", "chars": 12, "seconds": s2, "planned": None},
        ],
        "total": {"pages": 2, "chars": 17, "seconds": total, "planned": 20},
    }


def test_default_wpm_is_350(work: Path):
    code, out, err = cli(work, "s.md", "--json")
    assert (code, err) == (0, "")
    assert json.loads(out) == sample_json(350, 0.9, 2.1, 3)


def test_default_wpm_appears_in_the_table(work: Path):
    code, out, _ = cli(work, "s.md")
    assert code == 0
    assert out.splitlines()[-1] == "| 合計 | 2 ページ（350 字/分） | 17 | 3.0（0分03秒） | 20 |"


@pytest.mark.parametrize(
    ("args", "wpm", "s1", "s2", "total"),
    [
        (["--wpm", "100"], 100, 3, 7.2, 10.2),
        (["--wpm", "350.5"], 350.5, 0.9, 2.1, 3),
        (["--wpm", "100", "--wpm", "200"], 200, 1.5, 3.6, 5.1),  # 後に書いた値が勝つ
    ],
)
def test_wpm_overrides_the_default(work: Path, args: list[str], wpm: float, s1: float, s2: float, total: float):
    code, out, _ = cli(work, "s.md", "--json", *args)
    assert code == 0
    assert json.loads(out) == sample_json(wpm, s1, s2, total)


@pytest.mark.parametrize("args", [[], ["--json"], ["--wpm", "100"]])
def test_usage_without_a_script(work: Path, args: list[str]):
    assert cli(work, *args) == (1, "", USAGE + "\n")


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["s.md", "--wpm", "0"], "--wpm は正の数"),
        (["s.md", "--wpm", "abc"], "--wpm は正の数"),
        (["s.md", "--wpm"], "--wpm は正の数"),
        (["s.md", "--limit", "-1"], "--limit は正の数"),
        (["s.md", "--foo"], "知らない引数: --foo"),
        (["s.md", "--wpm=100"], "知らない引数: --wpm=100"),
        (["s.md", "other.md"], "台本は 1 つだけ渡す: other.md"),
        (["s.md", "-x"], "台本は 1 つだけ渡す: -x"),
    ],
)
def test_bad_arguments_exit_1(work: Path, args: list[str], message: str):
    assert cli(work, *args) == (1, "", message + "\n")


@pytest.mark.parametrize("args", [["--json", "s.md"], ["s.md", "--json", "--json"], ["--wpm", "350", "s.md", "--json"]])
def test_flags_may_come_before_or_after_the_script(work: Path, args: list[str]):
    code, out, _ = cli(work, *args)
    assert code == 0
    assert json.loads(out) == sample_json(350, 0.9, 2.1, 3)


def test_limit_is_reported_in_json(work: Path):
    code, out, _ = cli(work, "s.md", "--limit", "1", "--json")
    assert code == 0
    assert json.loads(out) == {**sample_json(350, 0.9, 2.1, 3), "limit": {"minutes": 1, "over": False}}
