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
