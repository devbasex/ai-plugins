"""JSON Pointer の分解・書き出し・たどり方（`ndf.core.json_pointer`）の現状固定テスト。

分解（`~1` → `/`・`~0` → `~`）は `json_pointer` に 1 つにまとめた。自前で書いた `release.plugins._bump_pointer` も固定する。
"""

from __future__ import annotations

import copy
import json

import pytest

from ndf.core import json_pointer as jp
from ndf.domains.release import plugins

DOC = {"a": {"b/c": 1, "d~e": {"f": None}, "": 2}, "l": [{"x": 3}], "n": 0}


@pytest.mark.parametrize(
    "pointer, want",
    [
        ("", []),
        ("/", [""]),
        ("/a/b", ["a", "b"]),
        ("/a~1b/c~0d", ["a/b", "c~d"]),
        ("/~01", ["~1"]),  # `~1` を先に戻すため `~01` は `~1` になる（RFC 6901 の順）
        ("/~10", ["/0"]),
        ("a/b", ["b"]),  # 先頭の `/` が無くても最初の参照を捨てる
        ("/a/", ["a", ""]),
        ("//", ["", ""]),
    ],
)
def test_pointer_tokens(pointer, want):
    assert jp.tokens(pointer) == want


@pytest.mark.parametrize("key, want", [("a/b~c", "a~1b~0c"), ("~1", "~01"), (3, "3"), ("", "")])
def test_escape_token(key, want):
    assert jp.escape(key) == want


def test_pointer_round_trip():
    parts = ["a/b", "~1", 3, "", "~"]
    pointer = jp.join(parts)
    assert pointer == "/a~1b/~01/3//~0"
    assert jp.tokens(pointer) == ["a/b", "~1", "3", "", "~"]
    assert jp.join([]) == ""


@pytest.mark.parametrize(
    "pointer, want",
    [
        ("", True),
        ("/a", True),
        ("/a/b~1c", True),
        ("/a/d~0e/f", True),  # 値が None でもキーがあれば在る
        ("/a/", True),
        ("/a/x", False),
        ("/a/b~1c/z", False),  # 途中が object でない
        ("/l/0", False),  # 配列はたどらない
        ("/n", True),
    ],
)
def test_has_pointer(pointer, want):
    assert jp.has(DOC, pointer) is want


@pytest.mark.parametrize(
    "pointer, want",
    [
        ("/a/b~1c", 1),
        ("/a/d~0e", {"f": None}),
        ("/a/d~0e/f", None),
        ("/a/", 2),
        ("/a/x", None),
        ("/a/b~1c/z", None),
        ("/l/0", None),  # 配列はたどらない
        ("/n", 0),
    ],
)
def test_get_pointer(pointer, want):
    assert jp.value_of(DOC, pointer) == want


def test_get_pointer_empty_returns_the_document_itself():
    assert jp.value_of(DOC, "") is DOC
    assert jp.value_of(None, "") is None
    assert jp.value_of(None, "/a") is None


def test_set_pointer_creates_objects_and_unescapes():
    doc: dict = {}
    jp.put(doc, "/a/b", 1)
    jp.put(doc, "/a/c~1d", 2)
    jp.put(doc, "/e~0", [3])
    jp.put(doc, "/a/b", 4)  # 上書き
    assert doc == {"a": {"b": 4, "c/d": 2}, "e~": [3]}


def test_set_pointer_failures():
    with pytest.raises(IndexError):
        jp.put({}, "", 1)  # 参照が 1 つも無い
    with pytest.raises(TypeError):
        jp.put({"a": 1}, "/a/b", 2)  # 途中が object でない


@pytest.mark.parametrize(
    "pointer, want",
    [
        ("", DOC),
        ("/a/b~1c", 1),
        ("/l/0/x", 3),  # 配列は数字の参照でたどる
        ("/l/1", None),
        ("/l/-1", None),
        ("/l/x", None),
        ("/a/x", None),
        ("/n/0", None),
    ],
)
def test_land_pick(pointer, want):
    """版数のファイルの読み方（`land.read_version`）が使う、配列もたどる `resolve`。見つからなければ値は None。"""
    assert jp.resolve(DOC, pointer)[1] == want


def test_remove_pointer():
    doc = copy.deepcopy(DOC)
    jp.remove(doc, "/a/b~1c")
    assert "b/c" not in doc["a"]
    before = copy.deepcopy(doc)
    jp.remove(doc, "/a/zz")  # 無いキー
    jp.remove(doc, "/x/y/z")  # 無い途中
    jp.remove(doc, "/n/z")  # 途中が object でない
    jp.remove(doc, "/l/0")  # 配列はたどらない
    assert doc == before
    jp.remove(doc, "/a/d~0e")
    assert doc["a"] == {"": 2}


def test_bump_pointer(tmp_path):
    p = tmp_path / "plugin.json"
    p.write_text(json.dumps({"version": "1.0.0", "x": {"a/b": "1.0.0"}}), encoding="utf-8")
    assert plugins._bump_pointer(p, "/x/a~1b", "1.0.0", "1.1.0") is True
    assert json.loads(p.read_text(encoding="utf-8")) == {"version": "1.0.0", "x": {"a/b": "1.1.0"}}
    assert plugins._bump_pointer(p, "/version", "9.9.9", "2.0.0") is False  # 版が old でない
    assert plugins._bump_pointer(p, "/missing/version", "1.0.0", "2.0.0") is False
    assert plugins._bump_pointer(p, "", "1.0.0", "2.0.0") is False
    assert json.loads(p.read_text(encoding="utf-8"))["version"] == "1.0.0"
    assert plugins._bump_pointer(p, "/version", "1.0.0", "2.0.0") is True
    assert p.read_text(encoding="utf-8").endswith("\n")


def test_bump_pointer_follows_array_indexes(tmp_path):
    """スキーマが受け付ける配列の添字（`/packages/0/version`）も、読む側（`resolve`）と同じくたどって書き換える。"""
    p = tmp_path / "package.json"
    p.write_text(json.dumps({"packages": [{"version": "1.0.0"}, "1.0.0"]}), encoding="utf-8")
    assert plugins._bump_pointer(p, "/packages/0/version", "1.0.0", "1.1.0") is True
    assert plugins._bump_pointer(p, "/packages/1", "1.0.0", "1.1.0") is True
    assert plugins._bump_pointer(p, "/packages/2", "1.0.0", "1.1.0") is False
    assert json.loads(p.read_text(encoding="utf-8")) == {"packages": [{"version": "1.1.0"}, "1.1.0"]}
