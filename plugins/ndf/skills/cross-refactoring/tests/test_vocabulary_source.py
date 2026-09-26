"""呼び名の表を読む経路を確かめる（#444）。

**枠組みは呼び名を自分では持たない。** 表を読み、読めなければ止める。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

import pytest

_HERE = pathlib.Path(__file__).resolve().parent
_VOCAB_PY = _HERE.parent / "scripts" / "refactor_lib" / "vocabulary.py"


def _load(path: pathlib.Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_names_come_from_the_table(vocabulary) -> None:
    """兆候と手法の呼び名が、表から読まれていること。"""
    table = vocabulary.VOCABULARY_TABLE.read_text(encoding="utf-8")
    for identifier, name in vocabulary.SMELLS.items():
        assert f"| `{identifier}` | {name} |" in table
    for identifier, name in vocabulary.TECHNIQUES.items():
        assert f"| `{identifier}` | {name} |" in table


def test_the_module_does_not_hold_the_names_itself() -> None:
    """呼び名の並びを、読む側が自分で持っていないこと。"""
    source = _VOCAB_PY.read_text(encoding="utf-8")
    assert "長すぎるメソッド" not in source
    assert "メソッドの抽出" not in source


def test_it_stops_when_the_table_is_missing(tmp_path: pathlib.Path) -> None:
    """表を読めないときに止まること。**握りつぶして進まない。**"""
    copy = tmp_path / "vocabulary.py"
    copy.write_text(
        _VOCAB_PY.read_text(encoding="utf-8").replace(
            'parents[3]\n    / "refactoring" / "references" / "vocabulary.md"',
            'parents[3]\n    / "refactoring" / "references" / "no-such-file.md"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception) as caught:
        _load(copy, "vocabulary_without_table")
    assert "呼び名の表を読めません" in str(caught.value)


def test_a_table_inside_a_code_fence_is_not_read(vocabulary, tmp_path, monkeypatch) -> None:
    """#1142 の D4 で節と表を `md` で読むようにして変わった入力。コードの囲みの中の `## ` や `|` の行は、
    節にも表にもならない（前は正規表現で行を拾い、囲みの中の例を表として読んでいた）。"""
    table = tmp_path / "vocabulary.md"
    table.write_text(
        "## 兆候\n\n```text\n| 識別子 | 日本語の名前 |\n| --- | --- |\n| `fake` | 例 |\n```\n\n"
        "| 識別子 | 日本語の名前 |\n| --- | --- |\n| `real` | 本物 |\n\n"
        "```md\n## 手法\n```\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(vocabulary, "VOCABULARY_TABLE", table)
    assert vocabulary._read_table("兆候", "日本語の名前") == {"real": "本物"}
    with pytest.raises(vocabulary.VocabularyUnavailable, match="「手法」の節がありません"):
        vocabulary._read_table("手法", "日本語の名前")
