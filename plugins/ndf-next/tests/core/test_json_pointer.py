"""JSON Pointer の分解・書き出し・たどり方の現状固定テスト。

同じ分解（`~1` → `/`・`~0` → `~`）が `branch_strategy_legacy.pointer_tokens` と、それを呼ぶ各所・自前で書いた
`release.plugins._bump_pointer` に散っている。1 つのモジュールへ寄せる前に、今の振る舞いを固定する。
"""

from __future__ import annotations

import copy
import json

import pytest

from ndf.core import branch_strategy as bs
from ndf.core import branch_strategy_legacy as legacy
from ndf.domains.project import strategy_migrate
from ndf.domains.project import strategy_write as bw
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
    assert legacy.pointer_tokens(pointer) == want


@pytest.mark.parametrize("key, want", [("a/b~c", "a~1b~0c"), ("~1", "~01"), (3, "3"), ("", "")])
def test_escape_token(key, want):
    assert bs.escape_token(key) == want


def test_pointer_round_trip():
    parts = ["a/b", "~1", 3, "", "~"]
    pointer = bs._pointer(parts)
    assert pointer == "/a~1b/~01/3//~0"
    assert legacy.pointer_tokens(pointer) == ["a/b", "~1", "3", "", "~"]
    assert bs._pointer([]) == ""


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
    assert legacy.has_pointer(DOC, pointer) is want


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
    assert bw.get_pointer(DOC, pointer) == want


def test_get_pointer_empty_returns_the_document_itself():
    assert bw.get_pointer(DOC, "") is DOC
    assert bw.get_pointer(None, "") is None
    assert bw.get_pointer(None, "/a") is None


def test_set_pointer_creates_objects_and_unescapes():
    doc: dict = {}
    bw.set_pointer(doc, "/a/b", 1)
    bw.set_pointer(doc, "/a/c~1d", 2)
    bw.set_pointer(doc, "/e~0", [3])
    bw.set_pointer(doc, "/a/b", 4)  # 上書き
    assert doc == {"a": {"b": 4, "c/d": 2}, "e~": [3]}


def test_set_pointer_failures():
    with pytest.raises(IndexError):
        bw.set_pointer({}, "", 1)  # 参照が 1 つも無い
    with pytest.raises(TypeError):
        bw.set_pointer({"a": 1}, "/a/b", 2)  # 途中が object でない


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
    """版数のファイルの読み方（`land.store_version`）が使う、配列もたどる `resolve`。見つからなければ値は None。"""
    assert bs.resolve(DOC, pointer)[1] == want


def test_remove_pointer():
    doc = copy.deepcopy(DOC)
    strategy_migrate._remove_pointer(doc, "/a/b~1c")
    assert "b/c" not in doc["a"]
    before = copy.deepcopy(doc)
    strategy_migrate._remove_pointer(doc, "/a/zz")  # 無いキー
    strategy_migrate._remove_pointer(doc, "/x/y/z")  # 無い途中
    strategy_migrate._remove_pointer(doc, "/n/z")  # 途中が object でない
    strategy_migrate._remove_pointer(doc, "/l/0")  # 配列はたどらない
    assert doc == before
    strategy_migrate._remove_pointer(doc, "/a/d~0e")
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
