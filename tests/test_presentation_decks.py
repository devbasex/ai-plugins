"""`docs/presentations/` の生成スクリプトの資料が作れること（#2065）。

資料のディレクトリ（`<日付>-<topic>/`）ごとに、生成スクリプトを実行して pptx を検査する。
- 生成が終了コード 0 で、ページ数が台本と構成メモの表の行数に等しい
- 全ページのスピーカーノートが、台本の同じ番号の読み上げと一致する
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

DECKS = sorted(p.parents[1] for p in (ROOT / "docs" / "presentations").glob("*/slides/build-deck.js"))


def _notes_rows(notes: Path) -> list[int]:
    """構成メモの `## 構成` の節の表の番号の列。"""
    text = notes.read_text(encoding="utf-8")
    section = text.split("## 構成", 1)[1].split("\n## ", 1)[0]
    return [int(m.group(1)) for m in re.finditer(r"^\|\s*(\d+)\s*\|", section, re.M)]


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
    pages = info["slides"]
    assert len(pages) == len(readings)
    assert _notes_rows(deck / "notes.md") == list(range(1, len(pages) + 1))
    for no, page in enumerate(pages, start=1):
        assert page.notes, f"{no} ページ目にスピーカーノートが無い"
        assert page.notes == readings[no], f"{no} ページ目のノートが台本と違う"


@pytest.mark.parametrize("deck", DECKS, ids=[d.name for d in DECKS])
def test_deck_uses_copied_frame(deck: Path) -> None:
    src = (deck / "slides" / "build-deck.js").read_text(encoding="utf-8")
    assert 'require("./lib/deck-frame")' in src
    recorded = re.search(r"`([0-9a-f]{64})`", (deck / "README.md").read_text(encoding="utf-8"))
    assert recorded, "README に写した時点の lib/ のハッシュが無い"
    assert _lib_hash(deck / "slides" / "lib") == recorded.group(1)


def test_skill_plugin_development_has_23_pages() -> None:
    deck = ROOT / "docs" / "presentations" / "2026-10-17-skill-plugin-development"
    assert len(script_readings(deck / "script.md")) == 23
    assert _notes_rows(deck / "notes.md") == list(range(1, 24))
