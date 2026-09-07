"""兆候と手法の呼び名が 1 か所にあることを確かめる（#444）。

**呼び名を持つのは `refactoring/references/vocabulary.md` だけである。**
`cross-refactoring` は読むだけで、自分では持たない。2 か所にあると、片方だけが
更新されて枠組みの出力と方法論の説明が食い違う。

**読み込んだ結果と表を突き合わせる。** 読む側のリテラルを見る形は採らない
（読む側は呼び名を持たなくなったため、比べる相手が無い）。
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILLS = ROOT / "plugins/ndf/skills"
VOCAB_MD = SKILLS / "refactoring/references/vocabulary.md"
VOCAB_PY = SKILLS / "cross-refactoring/scripts/refactor_lib/vocabulary.py"
CODE_SMELLS = SKILLS / "refactoring/references/code-smells.md"
CATALOG = SKILLS / "refactoring/references/refactoring-catalog.md"


def _table(heading: str, value_column: str = "日本語の名前") -> dict[str, str]:
    """呼び名の表の 1 節を、識別子 → 値で返す。列は見出しの名前で決める。"""
    text = VOCAB_MD.read_text(encoding="utf-8")
    body = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert body, f"{heading} の節が無い"
    rows = [ln for ln in body.group(1).splitlines() if ln.strip().startswith("|")]
    assert len(rows) >= 3, f"{heading} の表が短い"
    header = [c.strip() for c in rows[0].strip("|").split("|")]
    i_id, i_value = header.index("識別子"), header.index(value_column)
    return {
        cells[i_id].strip("`"): cells[i_value]
        for line in rows[2:]
        for cells in [[c.strip() for c in line.strip("|").split("|")]]
    }


@pytest.fixture(scope="module")
def vocabulary() -> types.ModuleType:
    """読む側を、その位置のまま読み込む。"""
    sys.path.insert(0, str(VOCAB_PY.parents[1]))
    spec = importlib.util.spec_from_file_location("refactor_lib.vocabulary", VOCAB_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_smells_come_from_the_table(vocabulary: types.ModuleType) -> None:
    """読み込んだ兆候が、表と一致すること。"""
    assert vocabulary.SMELLS == _table("兆候")


def test_the_techniques_come_from_the_table(vocabulary: types.ModuleType) -> None:
    """読み込んだ手法が、表と一致すること。"""
    assert vocabulary.TECHNIQUES == _table("手法")


def test_the_budget_factors_come_from_the_table(vocabulary: types.ModuleType) -> None:
    """倍率が高い手法が、表と一致すること。"""
    high = {
        name for name, factor in _table("手法ごとの差分予算の倍率", "倍率").items()
        if factor == str(vocabulary.EXTRACTION_DIFF_BUDGET_FACTOR)
    }
    assert high == set(vocabulary.EXTRACTION_TECHNIQUES)


def test_the_reader_does_not_hold_the_names() -> None:
    """読む側が呼び名を自分で持たないこと。"""
    source = VOCAB_PY.read_text(encoding="utf-8")
    assert "長すぎるメソッド" not in source
    assert "メソッドの抽出" not in source


def test_every_smell_is_explained() -> None:
    """兆候の識別子が、説明の表にも現れること。"""
    text = CODE_SMELLS.read_text(encoding="utf-8")
    missing = [name for name in _table("兆候") if f"`{name}`" not in text]
    assert missing == []


def test_every_technique_is_explained() -> None:
    """手法の識別子が、カタログか説明の表に現れること。

    **カタログに項目を置かない手法がある**（`code-smells.md` の ★ の 5 件）。
    そちらは説明の表が識別子を持つ。
    """
    text = CATALOG.read_text(encoding="utf-8") + CODE_SMELLS.read_text(encoding="utf-8")
    missing = [name for name in _table("手法") if f"`{name}`" not in text]
    assert missing == []
