"""スライドの汎用テンプレート（`release/assets/slides/`）の振る舞い（#2065）。

生成スクリプトを node で実行し、出た pptx を zip と XML で読んで確かめる。
"""

from __future__ import annotations

import json
import shutil
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from slide_deck_support import TEMPLATE, copy_template, read_pptx, run_node, script_readings  # noqa: E402

# 手本の枠組みの座標（インチ）。トーン＆マナーに依らず固定される（I1）
TITLE = (0.26, 0.15, 8.29, 0.63)
RULE = (0.24, 0.8, 9.52)
COPYRIGHT = (0.26, 5.18, 3.2, 0.3)
PAGE_NO = (9.27, 5.18, 0.5, 0.3)
NOTE = (0.26, 4.88, 9.5, 0.28)
LOGO = (8.78, 0.24, 0.96, 0.23)

DEFAULT_COLORS = {"2B6CB0", "E3EDF7", "000000", "303030", "7F7F7F", "D9D9D9", "F3F3F3", "EEEEEE", "F4CCCC", "FFF2CC", "FFFFFF"}
OVERRIDE_COLORS = {
    "accent": "112233",
    "accentSoft": "112234",
    "text": "112235",
    "ink": "112236",
    "mute": "112237",
    "rule": "112238",
    "panel1": "112239",
    "panel2": "11223A",
    "warn": "11223B",
    "caution": "11223C",
    "header": "11223D",
    "onHeader": "11223E",
    "background": "11223F",
}


def _png(path: Path) -> None:
    """1 × 1 の PNG を書く。"""
    raw = b"\x00\xff\x00\x00"
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def deck(tmp_path: Path) -> Path:
    return copy_template(tmp_path / "slides")


def _build(d: Path, out: Path, *theme: Path):
    return run_node(d / "build-deck.js", out, *theme, cwd=d)


def _find(slide, text: str):
    hits = [s for s in slide.shapes if s.text == text]
    assert hits, f"{text!r} の図形が無い: {[s.text for s in slide.shapes]}"
    return hits[0]


def _box(shape) -> tuple:
    return (shape.x, shape.y, shape.w, shape.h)


def test_sample_builds_three_pages_on_16_9(deck: Path, tmp_path: Path) -> None:
    out = tmp_path / "t.pptx"
    cp = _build(deck, out)
    assert cp.returncode == 0, cp.stderr
    info = read_pptx(out)
    assert info["size"] == (10.0, 5.62)  # 5.625 インチ（EMU を 2 桁へ丸めた値）
    slides = info["slides"]
    assert len(slides) == 3
    cover, section, body = slides
    assert any(s.text == "<資料の題>" for s in cover.shapes)
    assert any(s.text == "現状" for s in section.shapes)
    assert any(s.kind == "graphicFrame" and "<a:tbl>" in body.xml for s in body.shapes)
    assert _box(_find(body, "出典: <数値の出所>")) == NOTE


def _frame_positions(info) -> dict:
    body = info["slides"][2]
    rule = [s for s in body.shapes if s.prst == "line" and s.y == RULE[1]]
    return {
        "title": _box(_find(body, "現状の数値")),
        "rule": (rule[0].x, rule[0].y, rule[0].w) if rule else None,
        "copyright": _box(_find(body, "© Example")),
        "page": _box(_find(body, "3")),
        "note": _box(_find(body, "出典: <数値の出所>")),
    }


def test_frame_positions_match_reference_and_ignore_theme(deck: Path, tmp_path: Path) -> None:
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps({"copyright": "© Example"}), encoding="utf-8")
    big = tmp_path / "big.json"
    big.write_text(
        json.dumps({"copyright": "© Example", "font": "Other Font", "sizes": {"title": 28, "footer": 12, "note": 11}, "colors": {"rule": "123456"}}),
        encoding="utf-8",
    )
    got = []
    for theme in (plain, big):
        out = tmp_path / f"{theme.stem}.pptx"
        cp = _build(deck, out, theme)
        assert cp.returncode == 0, cp.stderr
        got.append(_frame_positions(read_pptx(out)))
    assert got[0] == {"title": TITLE, "rule": RULE, "copyright": COPYRIGHT, "page": PAGE_NO, "note": NOTE}
    assert got[0] == got[1]


def _override_theme(dir_: Path) -> Path:
    _png(dir_ / "logo.png")
    theme = dir_ / "theme.json"
    theme.write_text(
        json.dumps({"font": "Replaced Sans", "colors": OVERRIDE_COLORS, "copyright": "© Replaced", "logo": "logo.png"}),
        encoding="utf-8",
    )
    return theme


def _assert_replaced(info) -> None:
    assert info["fonts"] == {"Replaced Sans"}
    assert info["colors"] <= set(OVERRIDE_COLORS.values())
    assert not info["colors"] & DEFAULT_COLORS
    assert info["media"], "ロゴの画像が入っていない"
    body = info["slides"][2]
    pics = [s for s in body.shapes if s.kind == "pic"]
    assert [_box(p) for p in pics] == [LOGO]
    assert any(s.text == "© Replaced" for s in body.shapes)


def test_theme_json_replaces_fonts_colors_copyright_and_logo(deck: Path, tmp_path: Path) -> None:
    _override_theme(deck)  # 既定の置き場（slides/theme.json）
    out = tmp_path / "r.pptx"
    cp = _build(deck, out)
    assert cp.returncode == 0, cp.stderr
    _assert_replaced(read_pptx(out))

    # 枠組みを写し直しても（更新の取り込み）、theme.json の値が残る（I2）
    shutil.rmtree(deck / "lib")
    shutil.copytree(TEMPLATE / "lib", deck / "lib")
    out2 = tmp_path / "r2.pptx"
    cp = _build(deck, out2)
    assert cp.returncode == 0, cp.stderr
    _assert_replaced(read_pptx(out2))


def test_partial_theme_keeps_other_defaults(deck: Path, tmp_path: Path) -> None:
    theme = tmp_path / "accent.json"
    theme.write_text(json.dumps({"colors": {"accent": "AA0000"}}), encoding="utf-8")
    out = tmp_path / "a.pptx"
    cp = _build(deck, out, theme)
    assert cp.returncode == 0, cp.stderr
    info = read_pptx(out)
    assert "AA0000" in info["colors"]
    assert "2B6CB0" not in info["colors"]
    assert {"303030", "D9D9D9"} <= info["colors"]  # 書いていない項目は既定値のまま
    assert info["fonts"] == {"Noto Sans JP"}


@pytest.mark.parametrize(
    ("theme", "word"),
    [({"fonts": "X"}, "fonts"), ({"colors": {"brand": "000000"}}, "brand"), ({"colors": {"accent": "#2B6CB0"}}, "accent")],
)
def test_bad_theme_stops_with_1(deck: Path, tmp_path: Path, theme: dict, word: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(theme), encoding="utf-8")
    cp = _build(deck, tmp_path / "x.pptx", path)
    assert cp.returncode == 1
    assert word in cp.stderr
    assert not (tmp_path / "x.pptx").exists()


def test_raw_color_value_in_build_script_stops(deck: Path, tmp_path: Path) -> None:
    src = (deck / "build-deck.js").read_text(encoding="utf-8")
    (deck / "build-deck.js").write_text(src.replace('"panel1"', '"F3F3F3"'), encoding="utf-8")
    cp = _build(deck, tmp_path / "x.pptx")
    assert cp.returncode == 1
    assert "F3F3F3" in cp.stderr


def test_notes_come_from_script(deck: Path, tmp_path: Path) -> None:
    out = tmp_path / "n.pptx"
    assert _build(deck, out).returncode == 0
    readings = script_readings(deck / "script.md")
    notes = [s.notes for s in read_pptx(out)["slides"]]
    assert notes == [readings[i] for i in range(1, 4)]
    assert "\n" in notes[2]  # `>` だけの行が段落を分ける


def test_page_count_must_match_script(deck: Path, tmp_path: Path) -> None:
    with open(deck / "script.md", "a", encoding="utf-8") as fh:
        fh.write("\n## 4. 増やしたページ\n\n> 4 ページ目の読み上げ。\n")
    cp = _build(deck, tmp_path / "x.pptx")
    assert cp.returncode == 1
    assert "4" in cp.stderr and "3" in cp.stderr
    assert not (tmp_path / "x.pptx").exists()


@pytest.mark.parametrize(("swap", "to", "message"), [("## 2.", "## 4.", "欠ける: 2"), ("## 3.", "## 2.", "重なる: 2")])
def test_broken_script_numbers_stop_with_2(deck: Path, tmp_path: Path, swap: str, to: str, message: str) -> None:
    script = deck / "script.md"
    script.write_text(script.read_text(encoding="utf-8").replace(swap, to), encoding="utf-8")
    for cp in (_build(deck, tmp_path / "x.pptx"), run_node(deck / "lib" / "script-time.js", script)):
        assert cp.returncode == 2, cp.stderr
        assert message in cp.stderr


SCRIPT = """# 台本

> 最初の区切りより前は読まない。

## 1. 表紙（30秒）

覚え書きの行は数えない。

> あいう **えお** かき。
>
> `くけ` こ

## 2. 本編

> さしすせそ たちつてと
"""


def test_script_time_counts_per_page_and_total(deck: Path, tmp_path: Path) -> None:
    script = tmp_path / "s.md"
    script.write_text(SCRIPT, encoding="utf-8")
    cp = run_node(deck / "lib" / "script-time.js", script, "--json")
    assert cp.returncode == 0, cp.stderr
    m = json.loads(cp.stdout)
    pages = m["pages"]
    assert [(p["no"], p["title"], p["chars"], p["planned"]) for p in pages] == [(1, "表紙", 11, 30), (2, "本編", 10, None)]
    assert pages[0]["seconds"] == round(11 / 350 * 60, 1)
    assert m["total"]["chars"] == 21
    assert m["total"]["seconds"] == round(pages[0]["seconds"] + pages[1]["seconds"], 1)

    table = run_node(deck / "lib" / "script-time.js", script)
    assert table.returncode == 0
    assert "| 合計 |" in table.stdout

    over = run_node(deck / "lib" / "script-time.js", script, "--limit", "0.01")
    assert over.returncode == 1
    fast = run_node(deck / "lib" / "script-time.js", script, "--wpm", "700", "--json")
    assert json.loads(fast.stdout)["total"]["seconds"] == pytest.approx(m["total"]["seconds"] / 2, abs=0.11)
