from __future__ import annotations

import json

from ndf.checks import facts


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


def test_fact_table_shape_and_copied_ids(plugin, rules):
    plugin.write("contracts/facts/stages.json", json.dumps({"rows": [{"id": "deploy"}, {"id": "verify"}]}))
    plugin.write("contracts/facts/bad.json", json.dumps({"rows": [{"id": "a"}, {"id": "a"}, {"x": 1}]}))
    plugin.write("contracts/facts/fact.schema.json", "{}")
    plugin.write("src/ndf/a.py", 'STAGES = ("deploy", "verify")\nONE = "verify"\n')
    found = facts.check(plugin.scope(), rules)
    assert sorted(f.rule for f in found) == ["const-fact", "const-fact", "fact-table", "fact-table"]
    assert kinds(f for f in found if f.rule == "const-fact") == ["table-set", "table-str"]
