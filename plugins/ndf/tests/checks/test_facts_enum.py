"""規則 `const-fact` の、スキーマの `enum` との突き合わせ（`enum-set`・`enum-subset`）。#3667 の受け入れ条件 4。"""

from __future__ import annotations

import json

import pytest

from ndf.checks import facts
from ndf.checks.model import CheckError


@pytest.fixture
def found(plugin, rules):
    """`enum` の値のスキーマと定数 1 行を書いた plugin の、指摘の種類と定数の名前の文。"""

    def run(code: str, enum: list, path: str = "contracts/schemas/a.schema.json") -> list[str]:
        plugin.write(path, json.dumps({"properties": {"kind": {"enum": enum}}}))
        plugin.write("src/ndf/a.py", code)
        return [f.detail.partition(" が")[0] for f in facts.check(plugin.scope(), rules) if f.rule == "const-fact"]

    return run


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ('A = ("c", "b", "a")\n', ["enum-set: A"]),
        ('A = {"a": 1, "b": 2, "c": 3}\n', ["enum-set: A"]),
        ('A = frozenset({"a", "b"})\n', ["enum-subset: A"]),
        ('A = {"x": "a", "y": "c"}\n', ["enum-subset: A.values"]),
        ('A = ("x", "y")\n', []),
        ('A = ("a", "x")\n', []),
        ('A = ("a", "b", "c", "d")\n', []),
        ('A = "a"\n', []),
    ],
)
def test_a_constant_within_the_enum_is_a_finding(found, code, expected):
    assert found(code, ["a", "b", "c"]) == expected


def test_an_enum_of_two_is_compared_and_one_is_not(found):
    assert found('A = ("a", "b")\n', ["a", "b"]) == ["enum-set: A"]
    assert found('A = ("a", "b")\n', ["a", None]) == []


def test_one_finding_names_every_schema_the_constant_copies(plugin, rules):
    for name in ("a", "b"):
        plugin.write(f"contracts/schemas/{name}.schema.json", json.dumps({"$defs": {"d": {"anyOf": [{"enum": ["x", "y"]}]}}}))
    plugin.write("src/ndf/a.py", 'A = ("x", "y")\n')
    [finding] = facts.check(plugin.scope(), rules)
    assert "schemas/a.schema.json#/$defs/d/anyOf/0" in finding.detail and "schemas/b.schema.json#" in finding.detail


def test_a_generated_schema_is_not_compared(found):
    assert found('A = ("a", "b")\n', ["a", "b"], "contracts/schemas/io/a.args.schema.json") == []
    assert found('A = ("a", "b")\n', ["a", "b"], "contracts/schemas/io/a.result.schema.json") == ["enum-set: A"]


def test_a_broken_schema_stops_the_check(plugin, rules):
    plugin.write("contracts/schemas/a.schema.json", "{")
    with pytest.raises(CheckError, match="スキーマを読めない"):
        facts.check(plugin.scope(), rules)
