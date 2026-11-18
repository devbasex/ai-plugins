"""現状固定: `deck-frame.js` の `mergeTheme`（theme.json を既定のトーン＆マナーへ重ねる）。

期待値は今の実装の出力である。正しさを主張しない。項目ごとの検査の分岐（font・colors・sizes・copyright・logo）と、
知らない項目・型の誤りで投げる DeckError の文と終了コードを固定する。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ._slides import DECK_FRAME, fake_pptx, needs_node, node, raises

pytestmark = needs_node

DEFAULT = {
    "font": "Noto Sans JP",
    "colors": {
        "accent": "2B6CB0",
        "accentSoft": "E3EDF7",
        "text": "000000",
        "ink": "303030",
        "mute": "7F7F7F",
        "rule": "D9D9D9",
        "panel1": "F3F3F3",
        "panel2": "EEEEEE",
        "warn": "F4CCCC",
        "caution": "FFF2CC",
        "header": "303030",
        "onHeader": "FFFFFF",
        "background": "FFFFFF",
    },
    "sizes": {
        "title": 20,
        "lead": 12,
        "body": 10,
        "table": 10,
        "note": 8,
        "footer": 9,
        "calloutHead": 11.5,
        "calloutBody": 9.5,
        "sectionTitle": 32,
        "sectionSub": 14,
        "coverTitle": 28,
        "coverSub": 16,
        "coverMeta": 12,
    },
    "copyright": "",
    "logo": None,
}


def merge(override: object, base_dir: str | None = None) -> object:
    arg = json.dumps(override)
    base = json.dumps(base_dir)
    return node(f"const m = require({json.dumps(str(DECK_FRAME))});\nout(m.mergeTheme({arg}, {base}));")


def merge_error(override: object, base_dir: str | None = None) -> dict:
    arg = json.dumps(override)
    base = json.dumps(base_dir)
    return node(f"const m = require({json.dumps(str(DECK_FRAME))});\nout({raises(f'm.mergeTheme({arg}, {base})')});")


@pytest.mark.parametrize("override", [None, {}])
def test_no_override_returns_the_default(override):
    assert merge(override) == DEFAULT


def test_result_is_a_copy_of_the_default():
    code = f"""const m = require({json.dumps(str(DECK_FRAME))});
const t = m.mergeTheme(null);
t.colors.accent = "000000"; t.sizes.body = 99;
out([m.DEFAULT_THEME.colors.accent, m.DEFAULT_THEME.sizes.body, m.mergeTheme(null).colors.accent]);"""
    assert node(code) == ["2B6CB0", 10, "2B6CB0"]


def test_font_copyright_colors_and_sizes_are_overlaid_item_by_item():
    got = merge({"font": "BIZ UDPGothic", "copyright": "(c) X", "colors": {"accent": "abcdef", "ink": "123456"}, "sizes": {"body": 11.5}})
    want = json.loads(json.dumps(DEFAULT))
    want["font"] = "BIZ UDPGothic"
    want["copyright"] = "(c) X"
    want["colors"]["accent"] = "ABCDEF"  # 大文字へそろえる
    want["colors"]["ink"] = "123456"
    want["sizes"]["body"] = 11.5
    assert got == want


def test_empty_copyright_and_null_logo_are_accepted():
    got = merge({"copyright": "", "logo": None})
    assert got == DEFAULT


def test_logo_resolves_against_base_dir(tmp_path: Path):
    (tmp_path / "logo.png").write_bytes(b"png")
    got = merge({"logo": "logo.png"}, str(tmp_path))
    assert got["logo"] == str(tmp_path / "logo.png")


def test_absolute_logo_ignores_base_dir(tmp_path: Path):
    logo = tmp_path / "abs.png"
    logo.write_bytes(b"png")
    got = merge({"logo": str(logo)}, "/nonexistent")
    assert got["logo"] == str(logo)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ([], "theme.json はオブジェクトでなければならない"),
        ("x", "theme.json はオブジェクトでなければならない"),
        (3, "theme.json はオブジェクトでなければならない"),
        ({"fonts": "a", "x": 1}, "theme.json に知らない項目がある: fonts, x"),
        ({"font": ""}, "theme.json の font は空でない文字列"),
        ({"font": 3}, "theme.json の font は空でない文字列"),
        ({"colors": None}, "theme.json の colors はオブジェクト"),
        ({"colors": []}, "theme.json の colors はオブジェクト"),
        ({"sizes": "x"}, "theme.json の sizes はオブジェクト"),
        ({"colors": {"accent": "000000", "nope": "000000"}}, "theme.json の colors に知らない項目がある: nope"),
        ({"sizes": {"huge": 1}}, "theme.json の sizes に知らない項目がある: huge"),
        ({"colors": {"accent": "#2B6CB0"}}, "theme.json の colors.accent は 6 桁の 16 進（# なし）: #2B6CB0"),
        ({"colors": {"accent": "12345"}}, "theme.json の colors.accent は 6 桁の 16 進（# なし）: 12345"),
        ({"colors": {"accent": 123456}}, "theme.json の colors.accent は 6 桁の 16 進（# なし）: 123456"),
        ({"sizes": {"body": 0}}, "theme.json の sizes.body は正の数（pt）: 0"),
        ({"sizes": {"body": "10"}}, "theme.json の sizes.body は正の数（pt）: 10"),
        ({"sizes": {"body": -1}}, "theme.json の sizes.body は正の数（pt）: -1"),
        ({"copyright": None}, "theme.json の copyright は文字列"),
        ({"copyright": 1}, "theme.json の copyright は文字列"),
        ({"logo": ""}, "theme.json の logo は PNG の相対パスか null"),
        ({"logo": 1}, "theme.json の logo は PNG の相対パスか null"),
    ],
)
def test_invalid_override_raises_input_error(override, message):
    assert merge_error(override) == {"message": message, "code": 1, "deck": True}


def test_missing_logo_file_raises_input_error(tmp_path: Path):
    got = merge_error({"logo": "missing.png"}, str(tmp_path))
    assert got == {"message": f"theme.json の logo のファイルが無い: {tmp_path / 'missing.png'}", "code": 1, "deck": True}


def test_first_bad_item_in_order_wins():
    # 検査は font → colors → sizes → copyright → logo の順で、最初の誤りで止まる
    got = merge_error({"logo": 1, "copyright": 1, "sizes": {"body": 0}, "colors": {"accent": "x"}, "font": ""})
    assert got["message"] == "theme.json の font は空でない文字列"
    got = merge_error({"logo": 1, "copyright": 1, "sizes": {"body": 0}, "colors": {"accent": "x"}})
    assert got["message"] == "theme.json の colors.accent は 6 桁の 16 進（# なし）: x"
    got = merge_error({"logo": 1, "copyright": 1, "sizes": {"body": 0}})
    assert got["message"] == "theme.json の sizes.body は正の数（pt）: 0"
    got = merge_error({"logo": 1, "copyright": 1})
    assert got["message"] == "theme.json の copyright は文字列"


def test_load_theme_via_create_deck_reads_theme_file(tmp_path: Path):
    """`loadTheme` の分岐（未指定・オブジェクト・無いファイル・JSON でない）も theme のエントリポイントとして固定する。"""
    fake_pptx(tmp_path)
    (tmp_path / "theme.json").write_text('{"font": "F1"}', encoding="utf-8")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    dm = json.dumps(str(DECK_FRAME))
    base = json.dumps(str(tmp_path))
    code = f"""const m = require({dm});
const f = (theme) => m.createDeck({{ baseDir: {base}, theme }}).theme.font;
let broken;
try {{ f({json.dumps(str(tmp_path / "broken.json"))}); }} catch (e) {{ broken = [e.message.split(":")[0], e.code]; }}
out([f(undefined), f({{ font: "F2" }}), f({json.dumps(str(tmp_path / "theme.json"))}), f({json.dumps(str(tmp_path / "none.json"))}), broken]);"""
    assert node(code) == ["Noto Sans JP", "F2", "F1", "Noto Sans JP", ["theme.json を JSON として読めない", 1]]
