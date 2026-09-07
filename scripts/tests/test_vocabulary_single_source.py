"""兆候と手法の呼び名が 1 か所にあることを確かめる（#444）。

**呼び名を持つのは `refactoring/references/vocabulary.md` だけである。**
`cross-refactoring` は読むだけで、自分では持たない。2 か所にあると、片方だけが
更新されて枠組みの出力と方法論の説明が食い違う。
"""
from __future__ import annotations

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
VOCAB_MD = ROOT / "plugins/ndf/skills/refactoring/references/vocabulary.md"
VOCAB_PY = ROOT / "plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/vocabulary.py"


def _table(heading: str) -> dict[str, str]:
    """`vocabulary.md` の見出し直後の表を、識別子 → 日本語の名前で返す。

    **列は見出しの名前で決める。** 並びが変わっても読み取りが壊れない。
    """
    text = VOCAB_MD.read_text(encoding="utf-8")
    body = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert body, f"{heading} の節が無い"
    rows = [ln for ln in body.group(1).splitlines() if ln.strip().startswith("|")]
    assert len(rows) >= 3, f"{heading} の表が短い"
    header = [c.strip() for c in rows[0].strip("|").split("|")]
    i_id, i_name = header.index("識別子"), header.index("日本語の名前")
    out: dict[str, str] = {}
    for ln in rows[2:]:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        out[cells[i_id].strip("`")] = cells[i_name]
    return out


def _literal(name: str) -> object:
    """`vocabulary.py` のトップレベルの代入を、実行せずに読む。"""
    tree = ast.parse(VOCAB_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        if isinstance(target, ast.Name) and target.id == name:
            value = node.value
            # `frozenset({...})` は呼び出し式であり、そのままでは読めない。
            # 中身の集合だけを取り出す。
            if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                    and value.func.id in {"frozenset", "set", "tuple", "list"}):
                value = value.args[0]
            return ast.literal_eval(value)
    raise AssertionError(f"{name} が見つからない")


def test_the_smells_match_the_table() -> None:
    """兆候の識別子と日本語の名前が、表と一致すること。"""
    assert _literal("SMELLS") == _table("兆候")


def test_the_techniques_match_the_table() -> None:
    """手法の識別子と日本語の名前が、表と一致すること。"""
    assert _literal("TECHNIQUES") == _table("手法")


def test_the_extraction_techniques_come_from_the_table() -> None:
    """差分予算の倍率が 3 の手法が、表と一致すること。"""
    text = VOCAB_MD.read_text(encoding="utf-8")
    body = re.search(r"^## 手法ごとの差分予算の倍率\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert body, "差分予算の節が無い"
    rows = [ln for ln in body.group(1).splitlines() if ln.strip().startswith("|")]
    header = [c.strip() for c in rows[0].strip("|").split("|")]
    i_id, i_factor = header.index("識別子"), header.index("倍率")
    from_table = {
        cells[i_id].strip("`")
        for ln in rows[2:]
        for cells in [[c.strip() for c in ln.strip("|").split("|")]]
        if cells[i_factor] == "3"
    }
    assert from_table == set(_literal("EXTRACTION_TECHNIQUES"))
