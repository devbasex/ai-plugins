"""配る Skill の表（`manifests/skills.json`）の読み取りと、形の違う表を拒むこと。"""

from __future__ import annotations

import pytest

from ndf.domains.manifests import table
from ndf.parity import table as parity

from ._manifests import ROOT, _write_table


def test_the_table_lists_the_skills_s1a_moves():
    # 配る Skill の表は、S1a で移す Skill の名前の一覧（機能の対応表の Skill の行）から作った
    moved = {r["id"].split(":", 1)[1] for r in parity.load("features")["rows"] if r["kind"] == "skill" and r["to"] == "S1a"}
    assert {s.name for s in table.load(ROOT).skills} == moved


def test_skills_not_sent_to_every_runtime_say_why():
    found = table.load(ROOT)
    every = {r.name for r in found.runtimes}
    partial = [s for s in found.skills if set(s.runtimes) != every]
    assert partial and all(s.why for s in partial)
    reads_all = [r.name for r in found.runtimes if r.definition is None]
    assert reads_all and all(set(reads_all) <= set(s.runtimes) for s in found.skills)


@pytest.mark.parametrize(
    ("skills", "word"),
    [
        ([{"name": "a", "runtimes": ["codex"], "why": "x"}], "すべて読む"),
        ([{"name": "a", "runtimes": ["claude"]}], "why"),
        ([{"name": "a", "runtimes": ["claude", "codex"], "why": "x"}], "why"),
        ([{"name": "a", "runtimes": ["claude", "kiro"], "why": "x"}], "runtimes"),
        ([{"name": "a", "runtimes": ["claude", "codex"]}, {"name": "a", "runtimes": ["claude", "codex"]}], "2 回"),
        ([{"name": "../a", "runtimes": ["claude", "codex"]}], "名前"),
        ([{"runtimes": ["claude"]}], "形"),
    ],
)
def test_a_broken_table_is_refused(plugin, skills, word):
    _write_table(plugin, skills)
    with pytest.raises(table.TableError, match=word):
        table.load(plugin)


@pytest.mark.parametrize("runtimes", [{"claude": {"definition": None}}, {"claude": {"definition": 1, "policy": None}}, []])
def test_a_broken_runtime_row_is_refused(plugin, runtimes):
    _write_table(plugin, [], runtimes)
    with pytest.raises(table.TableError):
        table.load(plugin)
