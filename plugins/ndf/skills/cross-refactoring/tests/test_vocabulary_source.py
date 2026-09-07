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
