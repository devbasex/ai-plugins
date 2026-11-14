"""用語集の文書の分割（#1616）と、意味の説明の廃止した語（fix:3190）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.domains.records import glossary
from ndf.domains.records import glossary_decide as gd

LAYOUT = gd.Layout("glossary.json", "docs/glossary.md", "docs/glossary", 50)


def _g(n_terms: int = 2) -> dict:
    return {
        "contexts": [{"id": "flow", "name": "工程", "meaning": "開発の工程"}, {"id": "empty", "name": "空"}],
        "terms": [{"context": "flow", "term": f"語{i}", "meaning": "意味"} for i in range(n_terms)],
    }


def test_render_files_writes_one_file_per_context_and_an_index() -> None:
    got = gd.render_files(_g(), LAYOUT)
    assert sorted(got) == ["docs/glossary.md", "docs/glossary/empty.md", "docs/glossary/flow.md"]
    assert "語1" in got["docs/glossary/flow.md"] and "語1" not in got["docs/glossary.md"]
    assert "docs/glossary/empty.md" in got["docs/glossary.md"]


def test_render_files_stops_without_files_when_a_context_is_over_the_limit() -> None:
    with pytest.raises(gd.RenderError) as err:
        gd.render_files(_g(60), LAYOUT)
    assert list(err.value.over) == ["flow"] and err.value.limit == 50


def test_stale_lists_only_differing_paths() -> None:
    assert gd.stale({"a": "x", "b": "y"}, {"a": "x", "b": None}) == ["b"]


def test_deprecated_in_meanings_skips_code_live_terms_and_kanji_neighbours() -> None:
    g = {
        "contexts": [{"id": "c", "name": "c"}],
        "terms": [
            {"context": "c", "term": "承認ゲート", "meaning": "旧語ではない。`旧語` はコード", "deprecated": ["旧語"]},
            {"context": "c", "term": "段階", "meaning": "段階の中の段と、単独の 段 がある", "deprecated_standalone": ["段"]},
            {"context": "c", "term": "ゲート", "meaning": "承認ゲートの略"},
        ],
    }
    found = gd.deprecated_in_meanings(g)
    assert {"context": "c", "term": "承認ゲート", "word": "旧語"} in found
    assert {"context": "c", "term": "段階", "word": "段"} in found
    assert all(f["term"] != "ゲート" for f in found)


def _root(tmp_path: Path, g: dict, **decl: object) -> Path:
    (tmp_path / ".ndf").mkdir()
    base = {"source": "glossary.json", "document": "docs/glossary.md"}
    (tmp_path / ".ndf" / "glossary.json").write_text(json.dumps({**base, **decl}), encoding="utf-8")
    (tmp_path / "glossary.json").write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_render_writes_then_check_reports_nothing(tmp_path: Path) -> None:
    root = _root(tmp_path, _g())
    assert glossary.render(root, check=True) != []
    written = glossary.render(root, check=False)
    assert {r["path"] for r in written} == {"docs/glossary.md", "docs/glossary/flow.md", "docs/glossary/empty.md"}
    assert glossary.render(root, check=True) == []


def test_layout_reads_document_dir_and_max_lines(tmp_path: Path) -> None:
    root = _root(tmp_path, _g(), document_dir="docs/terms", max_lines=10)
    assert glossary.layout(root) == gd.Layout("glossary.json", "docs/glossary.md", "docs/terms", 10)


def test_layout_without_declaration_raises(tmp_path: Path) -> None:
    with pytest.raises(glossary.GlossaryError):
        glossary.layout(tmp_path)


# 現状固定（I-018 の前）: 用語集の設定と正本を読むときの誤りと既定
def _decl_only(tmp_path: Path, data: bytes) -> Path:
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "glossary.json").write_bytes(data)
    return tmp_path


def test_layout_reports_unreadable_json_and_a_declaration_without_source_or_document(tmp_path: Path) -> None:
    root = _decl_only(tmp_path, b"{")
    with pytest.raises(glossary.GlossaryError, match=r"glossary\.json を読めない: "):
        glossary.layout(root)
    for data in (b"[]", json.dumps({"source": "glossary.json"}).encode("utf-8")):
        (root / ".ndf" / "glossary.json").write_bytes(data)
        with pytest.raises(glossary.GlossaryError) as exc:
            glossary.layout(root)
        assert str(exc.value) == "用語集の設定に source と document が無い"


def test_layout_falls_back_to_the_defaults_for_a_non_int_max_lines(tmp_path: Path) -> None:
    root = _decl_only(tmp_path, json.dumps({"source": "glossary.json", "document": "docs/g.md", "max_lines": "9"}).encode("utf-8"))
    assert glossary.layout(root) == gd.Layout("glossary.json", "docs/g.md", "docs/g", glossary.DEFAULT_MAX_LINES)


def test_load_reports_a_missing_or_malformed_source(tmp_path: Path) -> None:
    root = _decl_only(tmp_path, json.dumps({"source": "glossary.json", "document": "docs/g.md"}).encode("utf-8"))
    lay = glossary.layout(root)
    with pytest.raises(glossary.GlossaryError, match=r"glossary\.json を読めない: "):
        glossary.load(root, lay)
    (root / "glossary.json").write_text(json.dumps({"contexts": {}, "terms": []}), encoding="utf-8")
    with pytest.raises(glossary.GlossaryError) as exc:
        glossary.load(root, lay)
    assert str(exc.value) == "glossary.json に contexts と terms の配列が無い"
    (root / "glossary.json").write_text(json.dumps({"contexts": [], "terms": []}), encoding="utf-8")
    assert glossary.load(root, lay) == {"contexts": [], "terms": []}


# NOTE: 現状固定。UTF-8 でない宣言・正本は GlossaryError にならず、UnicodeDecodeError がそのまま出る
def test_layout_and_load_raise_on_bytes_that_are_not_utf8(tmp_path: Path) -> None:
    root = _decl_only(tmp_path, b"\xff")
    with pytest.raises(UnicodeDecodeError):
        glossary.layout(root)
    (root / ".ndf" / "glossary.json").write_text(json.dumps({"source": "glossary.json", "document": "docs/g.md"}), encoding="utf-8")
    (root / "glossary.json").write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        glossary.load(root, glossary.layout(root))
