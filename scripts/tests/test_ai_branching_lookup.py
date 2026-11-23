"""`scripts/measure/ai-branching.py` の `_Conversation._lookup` の今の振る舞いを固定する（I-020 の直しの前）。

固定する点:
- 鍵が空か、どの鍵も履歴に無ければ None
- 履歴に無い鍵は飛ばし、ある鍵の中で順番（seq）が最も新しいものの (行, トップレベル, 名前) を返す
- 順番が同じなら、渡した鍵の並びで先のものを返す
"""

import importlib.util
from pathlib import Path

import pytest

AB = Path(__file__).resolve().parent.parent / "measure" / "ai-branching.py"


@pytest.fixture(scope="module")
def ab():
    spec = importlib.util.spec_from_file_location("ai_branching_lookup_under_test", AB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def conv(ab):
    c = ab._Conversation()
    c.latest = {
        "id:a": (1, {"r": "a"}, {"t": "a"}, None),
        "id:b": (3, {"r": "b"}, {"t": "b"}, "B"),
        "group:g|x": (2, {"r": "x"}, {"t": "x"}, "x"),
    }
    return c


def test_empty_keys_is_none(conv):
    assert conv._lookup([]) is None


def test_unknown_keys_only_is_none(conv):
    assert conv._lookup(["id:zz", "group:g|zz"]) is None


def test_empty_history_is_none(ab):
    assert ab._Conversation()._lookup(["id:a"]) is None


def test_single_known_key_returns_entry_without_seq(conv):
    assert conv._lookup(["id:a"]) == ({"r": "a"}, {"t": "a"}, None)


def test_unknown_keys_are_skipped(conv):
    assert conv._lookup(["id:zz", "group:g|x", "id:yy"]) == ({"r": "x"}, {"t": "x"}, "x")


@pytest.mark.parametrize(
    "keys",
    [["id:a", "id:b", "group:g|x"], ["group:g|x", "id:b", "id:a"], ["id:b", "id:a"]],
)
def test_newest_seq_wins_regardless_of_key_order(conv, keys):
    assert conv._lookup(keys) == ({"r": "b"}, {"t": "b"}, "B")


def test_tie_on_seq_returns_first_in_key_order(ab):
    c = ab._Conversation()
    c.latest = {
        "id:p": (5, {"r": "p"}, {"t": "p"}, "p"),
        "id:q": (5, {"r": "q"}, {"t": "q"}, "q"),
    }
    assert c._lookup(["id:p", "id:q"])[0] == {"r": "p"}
    assert c._lookup(["id:q", "id:p"])[0] == {"r": "q"}


def test_duplicate_keys_return_same_entry(conv):
    assert conv._lookup(["id:a", "id:a"]) == ({"r": "a"}, {"t": "a"}, None)


def test_returned_row_is_the_stored_object(conv):
    row = conv.latest["id:b"][1]
    assert conv._lookup(["id:b"])[0] is row
