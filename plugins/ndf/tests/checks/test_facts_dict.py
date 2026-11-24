"""規則 `const-fact` の辞書の定数の読み取り（キーと値の集まり）。#3667 の受け入れ条件 6。"""

from __future__ import annotations

import ast

from ndf.checks import facts


def read(text: str) -> dict[str, object]:
    return {c.name: c.value for c in facts.consts(ast.parse(text), "a.py")}


def found(plugin, rules) -> list[tuple[str, str]]:
    """指摘の（種類, 最初の定数の名前）。"""
    details = [f.detail for f in facts.check(plugin.scope(), rules)]
    return [(d.split(":", 1)[0], d.partition("（")[2].split(" ", 1)[0]) for d in details]


def test_a_dict_gives_its_keys_and_its_values():
    assert read('A = {"k1": "v1", "k2": "v2"}\n') == {"A": frozenset({"k1", "k2"}), "A.values": frozenset({"v1", "v2"})}


def test_values_that_are_not_all_strings_give_only_the_keys():
    assert read('A = {"k1": "v1", "k2": 2}\nB = {"k1": X, "k2": "v2"}\n') == {
        "A": frozenset({"k1", "k2"}),
        "B": frozenset({"k1", "k2"}),
    }


def test_a_dict_that_unpacks_another_gives_neither():
    assert read('A = {**B, "k1": "v1", "k2": "v2"}\n') == {}


def test_a_single_value_is_not_a_set():
    assert read('A = {"k1": "v", "k2": "v"}\n') == {"A": frozenset({"k1", "k2"})}
    assert read('A = {"k1": "v1"}\n') == {}


def test_copied_keys_are_still_the_same_set(plugin, rules):
    plugin.write("src/ndf/a.py", 'A = {"k1": 1, "k2": 2}\n')
    plugin.write("src/ndf/b.py", 'B = ("k1", "k2")\n')
    assert found(plugin, rules) == [("same-set", "A")]


def test_copied_values_are_the_same_set_under_the_values_name(plugin, rules):
    plugin.write("src/ndf/a.py", 'A = {"k1": "v1", "k2": "v2"}\n')
    plugin.write("src/ndf/b.py", 'B = ("v2", "v1")\n')
    assert found(plugin, rules) == [("same-set", "A.values")]


def test_values_that_copy_the_ids_of_a_table_are_a_table_set(rules):
    consts = facts.consts(ast.parse('A = {"k1": "deploy", "k2": "verify"}\n'), "a.py")
    [hit] = facts.table_hits(consts, {"stages": ["deploy", "verify"]}, rules["facts"]["min_str"])
    assert hit.detail.startswith("table-set: A.values ")
