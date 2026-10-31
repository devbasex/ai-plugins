"""`docs/presentations/` の生成スクリプトの資料が作れること（#2065）。

資料のディレクトリ（`<日付>-<topic>/`）ごとに、生成スクリプトを実行して pptx を検査する。
- 生成が終了コード 0 で、章扉を除いたページ数が台本と構成メモの表の行数に等しい
- 章扉を除く全ページのスピーカーノートが、台本の同じ番号の読み上げと一致する。章扉はノートを持たない。
  改行と空行は比べない（資料によってはノートに台本の改行と空行を残す）
- 生成スクリプトが写した枠組み（`slides/lib/`）を使い、その内容が README に書いた写した時点のハッシュと一致する
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "ndf" / "skills" / "release" / "tests"))

from slide_deck_support import ensure_node_modules, read_pptx, run_node, script_readings  # noqa: E402

# 章扉の帯（枠組みの FRAME.section.band。インチ）。章扉だけがこの図形を持つ
SECTION_BAND = (0.0, 0.0, 0.36, 5.62)

DECKS = sorted(p.parents[1] for p in (ROOT / "docs" / "presentations").glob("*/slides/build-deck.js"))


def _notes_rows(notes: Path) -> list[int]:
    """構成メモの `## 構成` の節の表の番号の列。"""
    text = notes.read_text(encoding="utf-8")
    section = text.split("## 構成", 1)[1].split("\n## ", 1)[0]
    return [int(m.group(1)) for m in re.finditer(r"^\|\s*(\d+)\s*\|", section, re.M)]


def _flat(text: str) -> str:
    """改行と空行を除いた読み上げ。ノートの改行の形は資料ごとに違ってよい。"""
    return re.sub(r"\s*\n\s*", "", text)


def _is_section(slide) -> bool:
    return any(s.kind == "sp" and (s.x, s.y, s.w, s.h) == SECTION_BAND for s in slide.shapes)


def _lib_hash(lib: Path) -> str:
    return hashlib.sha256(b"".join(p.read_bytes() for p in sorted(lib.glob("*.js")))).hexdigest()


def test_decks_exist() -> None:
    assert DECKS, "docs/presentations/ に生成スクリプトの資料が無い"


@pytest.mark.parametrize("deck", DECKS, ids=[d.name for d in DECKS])
def test_deck_builds_with_notes_from_script(deck: Path, tmp_path: Path) -> None:
    slides = deck / "slides"
    ensure_node_modules(slides)
    out = tmp_path / "deck.pptx"
    cp = run_node(slides / "build-deck.js", out, cwd=deck)
    assert cp.returncode == 0, cp.stderr

    readings = script_readings(deck / "script.md")
    info = read_pptx(out)
    sections = [p for p in info["slides"] if _is_section(p)]
    pages = [p for p in info["slides"] if not _is_section(p)]
    assert len(pages) == len(readings)
    assert _notes_rows(deck / "notes.md") == list(range(1, len(pages) + 1))
    for page in sections:
        assert not page.notes, "章扉にスピーカーノートがある"
    for no, page in enumerate(pages, start=1):
        assert page.notes, f"台本の {no} ページ目にスピーカーノートが無い"
        assert _flat(page.notes) == _flat(readings[no]), f"台本の {no} ページ目のノートが台本と違う"


@pytest.mark.parametrize("deck", DECKS, ids=[d.name for d in DECKS])
def test_deck_uses_copied_frame(deck: Path) -> None:
    src = (deck / "slides" / "build-deck.js").read_text(encoding="utf-8")
    assert 'require("./lib/deck-frame")' in src
    recorded = re.search(r"`([0-9a-f]{64})`", (deck / "README.md").read_text(encoding="utf-8"))
    assert recorded, "README に写した時点の lib/ のハッシュが無い"
    assert _lib_hash(deck / "slides" / "lib") == recorded.group(1)


def test_skill_plugin_development_has_21_pages_and_6_sections(tmp_path: Path) -> None:
    deck = ROOT / "docs" / "presentations" / "2026-10-17-skill-plugin-development"
    assert len(script_readings(deck / "script.md")) == 21
    assert _notes_rows(deck / "notes.md") == list(range(1, 22))
    ensure_node_modules(deck / "slides")
    out = tmp_path / "deck.pptx"
    assert run_node(deck / "slides" / "build-deck.js", out, cwd=deck).returncode == 0
    slides = read_pptx(out)["slides"]
    assert len(slides) == 27
    assert sum(_is_section(p) for p in slides) == 6
