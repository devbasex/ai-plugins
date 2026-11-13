from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.checks import facts
from ndf.checks.model import CheckError


def kinds(found) -> list[str]:
    return sorted(f.detail.split(":", 1)[0] for f in found)


def test_same_fact_in_two_files(plugin, rules):
    plugin.write("src/ndf/a.py", 'NAME = "release"\nKINDS = ("x", "y")\nLIMIT = 3\nSUB = ("p", "q")\n')
    plugin.write("src/ndf/b.py", 'OTHER = "release"\nALL = ["y", "x"]\nLIMIT = 3\nSUP = {"p", "q", "r"}\n')
    assert kinds(facts.check(plugin.scope(), rules)) == ["same-num", "same-set", "same-str", "subset"]


def test_same_file_and_short_values_are_not_counted(plugin, rules):
    plugin.write("src/ndf/a.py", 'A = "release"\nB = "release"\nC = "ok"\n_lower = "release"\n')
    plugin.write("src/ndf/b.py", 'D = "ok"\nE = 1.5\n')
    assert facts.check(plugin.scope(), rules) == []


def test_member_of_another_files_set(plugin, rules):
    plugin.write("src/ndf/a.py", 'ONE = "deploy"\n')
    plugin.write("src/ndf/b.py", 'ALL = frozenset({"deploy", "verify"})\n')
    assert kinds(facts.check(plugin.scope(), rules)) == ["member"]


SCHEMA = Path(__file__).resolve().parents[2] / "contracts" / "facts" / facts.SCHEMA_FILE


def table(rows, columns) -> str:
    return json.dumps({"version": 1, "summary": "s", "owner": "o", "columns": columns, "rows": rows})


def test_fact_table_shape_and_copied_ids(plugin, rules):
    plugin.write("contracts/facts/stages.json", table([{"id": "deploy"}, {"id": "verify"}], {}))
    plugin.write("contracts/facts/dup.json", table([{"id": "a"}, {"id": "a"}], {}))
    plugin.write("contracts/facts/fact.schema.json", SCHEMA.read_text(encoding="utf-8"))
    plugin.write("src/ndf/a.py", 'STAGES = ("deploy", "verify")\nONE = "verify"\n')
    found = facts.check(plugin.scope(), rules)
    assert sorted(f.rule for f in found) == ["const-fact", "const-fact", "fact-table"]
    assert kinds(f for f in found if f.rule == "const-fact") == ["table-set", "table-str"]


def test_fact_table_is_validated_by_schema(plugin, rules):
    plugin.write("contracts/facts/fact.schema.json", SCHEMA.read_text(encoding="utf-8"))
    columns = {"rank": {"type": "integer", "summary": "r"}, "tags": {"type": "array", "summary": "t", "optional": True}}
    plugin.write("contracts/facts/ok.json", table([{"id": "a", "rank": 1}, {"id": "b", "rank": 2, "tags": ["x"]}], columns))
    plugin.write("contracts/facts/no_owner.json", json.dumps({"version": 1, "summary": "s", "columns": {}, "rows": []}))
    plugin.write("contracts/facts/bad_rows.json", table([{"id": "a", "rank": "1"}, {"id": "b"}, {"id": "c", "rank": 3, "x": 1}], columns))
    plugin.write("contracts/facts/bad_column.json", table([], {"Rank": {"type": "float", "summary": "r"}}))
    found = [(f.path.rsplit("/", 1)[-1], f.detail) for f in facts.check(plugin.scope(), rules)]
    assert all(name != "ok.json" for name, _ in found)
    assert any(name == "no_owner.json" and "'owner'" in d for name, d in found)
    bad_rows = sorted(d for name, d in found if name == "bad_rows.json")
    assert len(bad_rows) == 3 and all(d.startswith("表の形: $.rows[") for d in bad_rows)
    assert len([d for name, d in found if name == "bad_column.json"]) == 2


def test_table_without_schema_beside_it_stops(plugin, rules):
    plugin.write("contracts/facts/stages.json", table([{"id": "deploy"}], {}))
    with pytest.raises(CheckError, match="表の共通の形を読めない"):
        facts.check(plugin.scope(), rules)


def test_domain_tables_are_checked_and_names_do_not_repeat(plugin, rules):
    plugin.write("contracts/facts/fact.schema.json", SCHEMA.read_text(encoding="utf-8"))
    plugin.write("contracts/facts/stages.json", table([{"id": "deploy"}], {}))
    plugin.write("contracts/domains/stages.json", table([{"id": "verify"}], {}))
    plugin.write("contracts/domains/seats.json", table([{"id": "a"}, {"id": "a"}], {}))
    found = facts.check(plugin.scope(), rules)
    details = sorted((f.path.rsplit("/", 2)[-2], f.detail) for f in found if f.rule == "fact-table")
    assert details == [("domains", "`id` の重なり: a"), ("domains", "表の名前 stages が別の置き場の表と重なる")]


# 現状固定テスト（I-002）: 列の宣言から作る行の形。`contracts/facts.py` の `_row_schema` と次の 2 点が違う（今の出力を固定する）。
# - `id` に `minLength` を付けない
# - `optional` は `True` のときだけ欠いてよい（真に見えるだけの値では必須のまま）


def test_row_schema_without_columns():
    assert facts.row_schema({}) == {
        "type": "object",
        "required": ["id"],
        "properties": {"id": {"type": "string"}},
        "additionalProperties": False,
    }


def test_row_schema_from_column_declarations():
    columns = {
        "rank": {"type": "integer", "summary": "r"},
        "tags": {"type": "array", "summary": "t", "optional": True},
        "note": {"type": "string", "summary": "n", "optional": False},
        "flag": {"type": "boolean", "summary": "f", "optional": 1},
    }
    assert facts.row_schema(columns) == {
        "type": "object",
        "required": ["id", "rank", "note", "flag"],
        "properties": {
            "rank": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "note": {"type": "string"},
            "flag": {"type": "boolean"},
            "id": {"type": "string"},
        },
        "additionalProperties": False,
    }


def test_row_schema_declared_id_is_overwritten_and_not_repeated_in_required():
    schema = facts.row_schema({"id": {"type": "integer", "summary": "i"}, "name": {"type": "string", "summary": "n"}})
    assert schema["properties"]["id"] == {"type": "string"}
    assert list(schema["properties"]) == ["id", "name"]
    assert schema["required"] == ["id", "name"]


def test_row_schema_accepts_empty_id():
    from jsonschema import Draft202012Validator

    assert list(Draft202012Validator(facts.row_schema({})).iter_errors({"id": ""})) == []


# 現状固定テスト（I-003）: 最上位の定数の拾い方。代入の形ごとに拾う・拾わないを固定する。

CONST_SOURCE = """\
import os
NAME = "release"
_PRIVATE = 3
A = B = ("x", "y")
LIMIT: int = 5
WIDTH: float
lower = "skip"
X, Y = "p", "q"
OBJ.ATTR = "skip"
FLAG = True
CALL = os.getenv("X")
ONE = ("only",)
SET = frozenset({"m", "n"})
KEYS = {"k1": 1, "k2": 2}
MIXED = ("a", 1)
AUG = 1
AUG += 1
def f():
    INNER = "skip"
if True:
    NESTED = "skip"
"""


def test_consts_picks_top_level_constants_by_assignment_form():
    import ast

    found = facts.consts(ast.parse(CONST_SOURCE), "src/ndf/a.py")
    assert [(c.line, c.name, c.value) for c in found] == [
        (2, "NAME", "release"),
        (3, "_PRIVATE", 3),
        (4, "A", frozenset({"x", "y"})),
        (4, "B", frozenset({"x", "y"})),
        (5, "LIMIT", 5),
        (13, "SET", frozenset({"m", "n"})),
        (14, "KEYS", frozenset({"k1", "k2"})),
        (16, "AUG", 1),
    ]
    assert {c.rel for c in found} == {"src/ndf/a.py"}
    assert found[0].place == "src/ndf/a.py:2 NAME"


# 現状固定テスト（I-004）: 組の種類ごとの出力の並び・位置・文言。種類の順（same-str → same-set → same-num → subset → member）も固定する。

GROUP_CONSTS = [
    facts.Const("b.py", 3, "OTHER", "release"),
    facts.Const("a.py", 1, "NAME", "release"),
    facts.Const("a.py", 9, "SHORT", "rel"),
    facts.Const("b.py", 9, "SHORT2", "rel"),
    facts.Const("a.py", 2, "KINDS", frozenset({"x", "y"})),
    facts.Const("b.py", 2, "ALL", frozenset({"x", "y"})),
    facts.Const("c.py", 1, "SUP", frozenset({"x", "y", "z"})),
    facts.Const("a.py", 3, "LIMIT", 3),
    facts.Const("b.py", 4, "LIMIT", 3),
    facts.Const("c.py", 2, "LIMIT", 3.0),
    facts.Const("b.py", 5, "OTHERNUM", 3),
    facts.Const("c.py", 3, "ONE", "zzzz"),
    facts.Const("a.py", 4, "SAME", frozenset({"zzzz", "w"})),
    facts.Const("a.py", 5, "LOCAL", "wwww"),
]


def test_groups_reports_each_kind_in_order():
    found = [(f.rule, f.path, f.line, f.detail) for f in facts.groups(GROUP_CONSTS, 4)]
    assert found == [
        ("const-fact", "a.py", 1, "same-str: 'release'（NAME と b.py:3 OTHER）"),
        ("const-fact", "a.py", 2, "same-set: 同じ集まり（KINDS と b.py:2 ALL）"),
        ("const-fact", "a.py", 3, "same-num: 同じ名前と数 3（LIMIT と b.py:4 LIMIT・c.py:2 LIMIT）"),
        ("const-fact", "a.py", 2, "subset: 集まりが別のファイルの集まりに含まれる（KINDS と c.py:1 SUP）"),
        ("const-fact", "b.py", 2, "subset: 集まりが別のファイルの集まりに含まれる（ALL と c.py:1 SUP）"),
        ("const-fact", "a.py", 4, "member: 'zzzz' が別のファイルの集まりの要素（SAME と c.py:3 ONE）"),
    ]


def test_groups_min_str_is_inclusive_lower_bound():
    found = [f.detail for f in facts.groups(GROUP_CONSTS, 3)]
    assert "same-str: 'rel'（SHORT と b.py:9 SHORT2）" in found
    assert len(found) == 7


def test_groups_without_constants_is_empty():
    assert facts.groups([], 4) == []
