"""JSON の読みと原子的な書き込み（lib/jsonio.py・#1142 の L0）。無い・壊れた・形が違うときの扱いを引数で選ぶ。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import jsonio  # noqa: E402


def test_read_raises_by_default(tmp_path):
    with pytest.raises(jsonio.JsonReadError) as e:
        jsonio.read(tmp_path / "none.json")
    assert e.value.kind == "missing"
    (tmp_path / "bad.json").write_text("{")
    with pytest.raises(jsonio.JsonReadError) as e:
        jsonio.read(tmp_path / "bad.json")
    assert e.value.kind == "broken"
    (tmp_path / "list.json").write_text("[1]")
    with pytest.raises(jsonio.JsonReadError) as e:
        jsonio.read(tmp_path / "list.json", want=dict)
    assert e.value.kind == "type"


def test_read_returns_the_given_values(tmp_path):
    assert jsonio.read(tmp_path / "none.json", missing=None) is None
    (tmp_path / "bad.json").write_text("{")
    assert jsonio.read(tmp_path / "bad.json", broken={}) == {}
    (tmp_path / "list.json").write_text("[1]")
    assert jsonio.read(tmp_path / "list.json", broken={}, want=dict) == {}
    assert jsonio.read(tmp_path / "list.json") == [1]


def test_missing_value_does_not_hide_a_broken_file(tmp_path):
    (tmp_path / "bad.json").write_text("{")
    with pytest.raises(jsonio.JsonReadError):
        jsonio.read(tmp_path / "bad.json", missing={})


def test_json_read_error_is_a_value_error(tmp_path):
    with pytest.raises(ValueError):
        jsonio.read(str(tmp_path / "none.json"))


def test_write_atomic_writes_and_leaves_no_temp(tmp_path):
    path = tmp_path / "d" / "s.json"
    jsonio.write_atomic(path, {"a": "日本"})
    assert jsonio.read(path) == {"a": "日本"}
    assert "日本" in path.read_text(encoding="utf-8")
    assert list(path.parent.iterdir()) == [path]


def test_write_atomic_keeps_the_old_file_on_failure(tmp_path):
    path = tmp_path / "s.json"
    jsonio.write_atomic(path, {"a": 1})
    with pytest.raises(TypeError):
        jsonio.write_atomic(path, {"a": object()})
    assert jsonio.read(path) == {"a": 1}
    assert list(tmp_path.iterdir()) == [path]
