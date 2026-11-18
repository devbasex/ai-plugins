"""現状固定: `deck-frame.js` の `createDeck`（ページの部品と保存）。

期待値は今の実装の出力である。正しさを主張しない。pptxgenjs は呼び出しを記録するだけの偽物へ差し替え
（`_slides.FAKE_PPTX`）、部品ごとに pptxgenjs へ渡した引数を固定する。

代表の資料（表紙・章扉・本編とすべての部品）の呼び出しは `data/slides_deck_calls.json` に保存してある。
更新するときは中身を読んで差分を確かめる（読まずに更新すると退行を承認してしまう）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ._slides import DECK_FRAME, fake_pptx, needs_node, node

pytestmark = needs_node

GOLDEN = Path(__file__).with_name("data") / "slides_deck_calls.json"
SCRIPT = "## 1. 表紙（20秒）\n> 表紙の読み上げ\n## 2. 本編\n> 本編の読み上げ\n"

# 代表の資料。表紙・章扉・本編の 3 ページに、本編へすべての部品を載せる
SAMPLE = """
d.cover("題", "副", "メタ");
d.section("章", "章の副");
const s = d.page("頁", "リード");
d.text(s, "本文");
d.text(s, [{ text: "a", options: { color: "accent", fontSize: "lead" } }, "b"], { x: 1, color: "warn", fontSize: 14 });
d.box(s, 1, 2, 3, 4, "panel1");
d.box(s, 1, 2, 3, 4, "panel2", false);
d.box(s, 1, 2, 3, 4, "panel2", "accent");
d.chip(s, 1, 2, 3, 4, "チップ", "accentSoft");
d.chip(s, 1, 2, 3, 4, "チップ2", "warn", { color: "ink", bold: false });
d.arrow(s, 1, 2, 3, 0);
d.arrow(s, 1, 2, 3, 0, "accent");
d.note(s, "注");
d.tbl(s, [[d.H("h"), d.H("h2", { fill: { color: "accent" } })], [d.B("b"), d.C("c", { border: [{ color: "rule" }, null, { pt: 1 }] }), "raw"]], { x: 0.3, line: { color: "mute" } });
d.callout(s, 3, 1, "caution", "見出し", "本文");
d.callout(s, 3, 1, "caution", "見出しだけ");
"""


def _round(v: object) -> object:
    """浮動小数の誤差（9.040000000000001 など）を比較から外す。"""
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, list):
        return [_round(x) for x in v]
    if isinstance(v, dict):
        return {k: _round(x) for k, x in v.items()}
    return v


def build(base: Path, body: str, opts: str = "{}", save: str | None = "out.pptx") -> dict:
    """偽の pptxgenjs で資料を作り、ページごとの呼び出しと保存の結果を返す。"""
    fake_pptx(base)
    dm = json.dumps(str(DECK_FRAME))
    out_path = json.dumps(str(base / save) if save else None)
    code = f"""const m = require({dm});
const d = m.createDeck(Object.assign({{ baseDir: {json.dumps(str(base))} }}, {opts}));
{body}
let saved = null;
if ({out_path} !== null) saved = await d.save({out_path});
out({{ saved, pageNo: d.pageNo, scriptNo: d.scriptNo,
  meta: {{ layout: d.pptx.layout, title: d.pptx.title ?? null, author: d.pptx.author ?? null, subject: d.pptx.subject ?? null, written: d.pptx.written }},
  slides: d.pptx.slides.map((x) => ({{ background: x.background, notes: x.notes, calls: x.calls }})) }});"""
    return _round(node(code))


def build_error(base: Path, body: str, opts: str = "{}") -> dict:
    """資料を作る途中か保存で投げた DeckError の文と終了コード。"""
    fake_pptx(base)
    dm = json.dumps(str(DECK_FRAME))
    code = f"""const m = require({dm});
try {{
  const d = m.createDeck(Object.assign({{ baseDir: {json.dumps(str(base))} }}, {opts}));
  {body}
  out(null);
}} catch (e) {{ out({{ message: e.message, code: e.code, deck: e instanceof m.DeckError }}); }}"""
    return node(code)


@pytest.fixture
def base(tmp_path: Path) -> Path:
    (tmp_path / "script.md").write_text(SCRIPT, encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"png")
    return tmp_path


def sample_opts(base: Path) -> str:
    return json.dumps(
        {"script": str(base / "script.md"), "title": "T", "author": "A", "theme": {"copyright": "(c) C", "logo": str(base / "logo.png")}}
    )


def test_sample_deck_calls_match_the_golden(base: Path):
    got = build(base, SAMPLE, sample_opts(base))
    want = _round(json.loads(GOLDEN.read_text(encoding="utf-8")))
    out = str(base / "out.pptx")
    assert got["saved"] == {"out": out, "pages": 3, "sections": 1}
    assert got["pageNo"] == 3
    assert got["scriptNo"] == 2
    assert got["meta"] == {"layout": "LAYOUT_16x9", "title": "T", "author": "A", "subject": "T", "written": {"fileName": out}}
    assert got["slides"] == want


def test_without_title_author_script_copyright_and_logo(tmp_path: Path):
    got = build(tmp_path, 'd.cover("題"); d.section("章"); d.page("頁");')
    assert got["meta"] == {
        "layout": "LAYOUT_16x9",
        "title": None,
        "author": None,
        "subject": None,
        "written": {"fileName": str(tmp_path / "out.pptx")},
    }
    cover, section, page = got["slides"]
    # 台本が無ければノートを付けない。著作権表示とロゴが無ければその呼び出しも無い
    assert [s["notes"] for s in got["slides"]] == [None, None, None]
    assert [c[0] for c in cover["calls"]] == ["shape", "text", "shape"]
    assert [c[0] for c in section["calls"]] == ["shape", "text", "shape", "text"]
    assert section["calls"][-1][1] == "2"  # 章扉もページ番号を数える
    assert [c[0] for c in page["calls"]] == ["text", "shape", "text"]
    assert page["calls"][-1][1] == "3"
    assert got["saved"] == {"out": str(tmp_path / "out.pptx"), "pages": 3, "sections": 1}
    assert got["scriptNo"] == 2


def test_script_page_without_reading_has_no_notes(tmp_path: Path):
    (tmp_path / "script.md").write_text("## 1. 表紙\n覚え書き\n## 2. 本編\n> 読む\n", encoding="utf-8")
    got = build(tmp_path, 'd.cover("題"); d.page("頁");', json.dumps({"script": str(tmp_path / "script.md")}))
    assert [s["notes"] for s in got["slides"]] == [None, "読む"]


def test_frame_and_theme_are_exposed(tmp_path: Path):
    fake_pptx(tmp_path)
    dm = json.dumps(str(DECK_FRAME))
    code = f"""const m = require({dm});
const d = m.createDeck({{ baseDir: {json.dumps(str(tmp_path))}, theme: {{ colors: {{ accent: "aaaaaa" }} }} }});
out({{ frame: d.frame === m.FRAME, script: d.script, accent: d.theme.colors.accent, color: d.color("accent"),
  cells: [d.H === m.H, d.C === m.C, d.B === m.B], keys: Object.keys(d).sort() }});"""
    got = node(code)
    assert got == {
        "frame": True,
        "script": None,
        "accent": "AAAAAA",
        "color": "AAAAAA",
        "cells": [True, True, True],
        "keys": sorted(
            "pptx theme frame script pageNo scriptNo color text cover page section box chip arrow note tbl callout H C B save".split()
        ),
    }


def test_cell_helpers_merge_extra_options():
    dm = json.dumps(str(DECK_FRAME))
    got = node(
        f'const m = require({dm});\nout([m.H("h"), m.H("h", {{ align: "left" }}), m.C("c"), m.C("c", {{ bold: true }}), m.B("b"), m.B("b", {{ color: "warn" }})]);'
    )
    assert got == [
        {"text": "h", "options": {"fill": {"color": "header"}, "color": "onHeader", "bold": True, "align": "center"}},
        {"text": "h", "options": {"fill": {"color": "header"}, "color": "onHeader", "bold": True, "align": "left"}},
        {"text": "c", "options": {"align": "center"}},
        {"text": "c", "options": {"align": "center", "bold": True}},
        {"text": "b", "options": {"bold": True}},
        {"text": "b", "options": {"bold": True, "color": "warn"}},
    ]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('d.color("nope");', "色は theme.colors の項目名で指す: nope"),
        ('d.text(d.page("p"), "t", { color: "#000000" });', "色は theme.colors の項目名で指す: #000000"),
        ('d.text(d.page("p"), "t", { fontSize: "huge" });', "文字の大きさは pt か theme.sizes の項目名で指す: huge"),
        ('d.box(d.page("p"), 0, 0, 1, 1, "nope");', "色は theme.colors の項目名で指す: nope"),
        ('d.arrow(d.page("p"), 0, 0, 1, 1, "nope");', "色は theme.colors の項目名で指す: nope"),
    ],
)
def test_unknown_color_or_size_name_raises_input_error(tmp_path: Path, body: str, message: str):
    assert build_error(tmp_path, body) == {"message": message, "code": 1, "deck": True}


@pytest.mark.parametrize("out", ["", "deck.pdf", "deck"])
def test_save_requires_pptx_path(tmp_path: Path, out: str):
    body = f'd.page("p"); await d.save({json.dumps(out)});'
    assert build_error(tmp_path, body) == {"message": "出力のパスは .pptx で終える", "code": 1, "deck": True}


def test_save_accepts_upper_case_extension(tmp_path: Path):
    got = build(tmp_path, 'd.page("p");', save="OUT.PPTX")
    assert got["saved"] == {"out": str(tmp_path / "OUT.PPTX"), "pages": 1, "sections": 0}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('d.cover("c");', "ページ数が台本と合わない: 台本 2 ページ / 作ったページ 1（章扉 0 を除く）"),
        ('d.cover("c"); d.section("s"); d.section("s2");', "ページ数が台本と合わない: 台本 2 ページ / 作ったページ 1（章扉 2 を除く）"),
        ('d.cover("c"); d.page("p"); d.page("p2");', "ページ数が台本と合わない: 台本 2 ページ / 作ったページ 3（章扉 0 を除く）"),
    ],
)
def test_save_checks_page_count_against_script(base: Path, body: str, message: str):
    opts = json.dumps({"script": str(base / "script.md")})
    body = body + f" await d.save({json.dumps(str(base / 'out.pptx'))});"
    assert build_error(base, body, opts) == {"message": message, "code": 1, "deck": True}


def test_missing_script_raises_input_error(tmp_path: Path):
    missing = str(tmp_path / "none.md")
    assert build_error(tmp_path, "", json.dumps({"script": missing})) == {"message": f"台本が無い: {missing}", "code": 1, "deck": True}


# ---- 同じ形の処理が複数の部品にある箇所（I-013） ----

FONT = "Noto Sans JP"
BODY = {"fontFace": FONT, "fontSize": 10, "color": "303030", "margin": 0, "valign": "top"}


def page_calls(base: Path, body: str, opts: str = "{}") -> list:
    """本編 1 ページ（`const s = d.page("p")`）に `body` を載せ、ページのスロットの後の呼び出しだけを返す。"""
    got = build(base, 'const s = d.page("p");\n' + body, opts, save=None)
    calls = got["slides"][-1]["calls"]
    return calls[4:] if json.loads(opts).get("theme") else calls[3:]


def test_color_names_resolve_in_fill_line_and_border_objects_and_arrays(tmp_path: Path):
    body = """
d.text(s, "t", { line: { color: "accent", width: 2 }, border: { color: "rule", pt: 1 }, fill: [{ color: "warn" }, null, { pt: 1 }], x: 1 });
d.text(s, "t", { fill: { color: "accent" }, line: [{ color: "mute" }], border: [{ color: "ink" }, "raw"] });
d.text(s, "t", { fill: { type: "none" }, line: { width: 1 }, color: undefined, fontSize: undefined });
"""
    assert page_calls(tmp_path, body) == [
        [
            "text",
            "t",
            {
                **BODY,
                "line": {"color": "2B6CB0", "width": 2},
                "border": {"color": "D9D9D9", "pt": 1},
                "fill": [{"color": "F4CCCC"}, None, {"pt": 1}],
                "x": 1,
            },
        ],
        ["text", "t", {**BODY, "fill": {"color": "2B6CB0"}, "line": [{"color": "7F7F7F"}], "border": [{"color": "303030"}, "raw"]}],
        # undefined を渡すと既定の色と大きさが消える（今の振る舞い）
        ["text", "t", {"fontFace": FONT, "margin": 0, "valign": "top", "fill": {"type": "none"}, "line": {"width": 1}}],
    ]


def test_runs_resolve_options_and_keep_other_entries(tmp_path: Path):
    body = """
d.text(s, [null, "x", { text: "y" }, { text: "z", options: { fill: { color: "warn" } } }]);
d.note(s, [{ text: "n", options: { color: "accent" } }]);
d.callout(s, 1, 1, "warn", "h", [{ text: "b", options: { color: "warn" } }]);
d.tbl(s, [[null, "x", { text: "y" }]]);
"""
    calls = page_calls(tmp_path, body)
    assert calls[0] == ["text", [None, "x", {"text": "y"}, {"text": "z", "options": {"fill": {"color": "F4CCCC"}}}], BODY]
    assert calls[1] == [
        "text",
        [{"text": "n", "options": {"color": "2B6CB0"}}],
        {"x": 0.26, "y": 4.88, "w": 9.5, "h": 0.28, "fontFace": FONT, "fontSize": 8, "color": "7F7F7F", "valign": "middle", "margin": 0},
    ]
    assert calls[2] == ["shape", "rect", {"x": 0.26, "y": 1, "w": 9.48, "h": 1, "fill": {"color": "F4CCCC"}, "line": {"type": "none"}}]
    assert calls[3] == [
        "text",
        "h",
        {
            "x": 0.48,
            "y": 1.05,
            "w": 9.04,
            "h": 0.28,
            "fontFace": FONT,
            "fontSize": 11.5,
            "bold": True,
            "color": "000000",
            "valign": "middle",
            "margin": 0,
        },
    ]
    assert calls[4] == [
        "text",
        [{"text": "b", "options": {"color": "F4CCCC"}}],
        {"x": 0.48, "y": 1.33, "w": 9.04, "h": 0.62, "fontFace": FONT, "fontSize": 9.5, "color": "303030", "valign": "top", "margin": 0},
    ]
    assert calls[5] == [
        "table",
        [[None, "x", {"text": "y"}]],
        {
            "fontFace": FONT,
            "fontSize": 10,
            "color": "303030",
            "valign": "middle",
            "border": {"type": "solid", "color": "D9D9D9", "pt": 1},
            "autoPage": False,
            "margin": [2, 4, 2, 4],
        },
    ]
    assert len(calls) == 6


@pytest.mark.parametrize(
    "body",
    [
        'd.text(d.page("p"), [{ text: "a", options: { color: "nope" } }]);',
        'd.tbl(d.page("p"), [[{ text: "a", options: { fill: { color: "nope" } } }]]);',
        'd.text(d.page("p"), "t", { border: [{ color: "nope" }] });',
        'd.text(d.page("p"), "t", { line: { color: "nope" } });',
        'd.chip(d.page("p"), 0, 0, 1, 1, "c", "nope");',
        'd.callout(d.page("p"), 0, 1, "nope", "h");',
        'd.note(d.page("p"), [{ text: "a", options: { color: "nope" } }]);',
    ],
)
def test_unknown_color_name_in_nested_options_raises(tmp_path: Path, body: str):
    assert build_error(tmp_path, body) == {"message": "色は theme.colors の項目名で指す: nope", "code": 1, "deck": True}


def test_cover_and_section_share_band_accent_and_footer_shapes(tmp_path: Path):
    got = build(tmp_path, 'd.page("p"); d.cover("c"); d.section("s");', json.dumps({"theme": {"copyright": "(c)"}}), save=None)
    _, cover, section = got["slides"]
    foot = {"fontFace": FONT, "fontSize": 9, "color": "7F7F7F", "valign": "middle", "margin": 0}
    assert cover["calls"] == [
        ["shape", "rect", {"x": 0, "y": 0, "w": 10, "h": 0.36, "fill": {"color": "2B6CB0"}, "line": {"type": "none"}}],
        [
            "text",
            "c",
            {
                "x": 1.02,
                "y": 1.3,
                "w": 8.2,
                "h": 0.72,
                "fontFace": FONT,
                "fontSize": 28,
                "bold": True,
                "color": "000000",
                "valign": "bottom",
                "margin": 0,
                "fit": "shrink",
            },
        ],
        ["shape", "line", {"x": 1.04, "y": 2.1, "w": 1.2, "h": 0, "line": {"color": "2B6CB0", "width": 4}}],
        ["text", "(c)", {"x": 0.26, "y": 5.18, "w": 3.2, "h": 0.3, **foot}],
    ]
    assert section["calls"] == [
        ["shape", "rect", {"x": 0, "y": 0, "w": 0.36, "h": 5.625, "fill": {"color": "2B6CB0"}, "line": {"type": "none"}}],
        [
            "text",
            "s",
            {"x": 0.9, "y": 1.9, "w": 8.4, "h": 0.8, "fontFace": FONT, "fontSize": 32, "bold": True, "color": "000000", "margin": 0},
        ],
        ["shape", "line", {"x": 0.92, "y": 2.82, "w": 1.2, "h": 0, "line": {"color": "2B6CB0", "width": 4}}],
        ["text", "(c)", {"x": 0.56, "y": 5.18, "w": 3.2, "h": 0.3, **foot}],
        ["text", "3", {"x": 9.27, "y": 5.18, "w": 0.5, "h": 0.3, **foot, "align": "right"}],
    ]
    # 表紙は台本の番号を進め、章扉は進めない
    assert (got["pageNo"], got["scriptNo"]) == (3, 2)
