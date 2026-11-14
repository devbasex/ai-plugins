from __future__ import annotations

import pytest

from ndf.checks import invariants
from ndf.checks.model import CheckError

# 旧いプラグインのパス。テストの本文も I3 が見るため、文字を組み立てて持つ
OLD = "/".join(("plugins", "ndf"))


def test_own_name_in_code_fails(plugin):
    plugin.write(
        "src/ndf/a.py",
        '"""ndf11 の説明"""\nP = "ndf11"\nQ = "a/ndf-next/b"\nR = "ndf11:design"\nS = "ndf-next"\nT = "ndf"\n'
        'U = ".ndf/"\nV = "/ndf:design"\nW = "ndf.checks.run"\nX = "src/ndf/a.py"\nY = "ndf11gen"\n',
    )
    plugin.write("bin/run", "#!/bin/sh\n# ndf11/ を見る\necho plugins/ndf-next/x\n")
    plugin.write("skills/a/SKILL.md", "/ndf11:design を呼ぶ\n")
    found = invariants.own_name(plugin.scope())
    lines = [("run", 3), ("a.py", 2), ("a.py", 3), ("a.py", 4), ("a.py", 6), ("a.py", 7), ("a.py", 8)]
    assert [(f.path.rsplit("/", 1)[-1], f.line) for f in found] == lines


def test_only_fixed_word_definitions_pass(plugin):
    plugin.write("src/ndf/identity.py", 'FINAL_NAME = "ndf"\nFIXED_WORDS = {\n    "declaration_dir": ".ndf",\n}\nZ = ".ndf"\n')
    plugin.write("src/ndf/b.py", 'FIXED_WORDS = {"d": ".ndf"}\n')
    found = invariants.own_name(plugin.scope())
    assert [(f.path.rsplit("/", 1)[-1], f.line) for f in found] == [("b.py", 1), ("identity.py", 5)]


def test_imports_outside_declared_fail(plugin):
    plugin.write("tests/x/test_a.py", f'import json\nimport requests\nfrom ndf import a\nP = "{OLD}/scripts"\nQ = "plugins/ndf-next"\n')
    found = invariants.old_imports(plugin.scope(), OLD)
    assert [(f.rule, f.line) for f in found] == [("I3", 2), ("I3", 4)]


def test_sibling_module_is_own(plugin):
    plugin.write("tests/x/_helper.py", "X = 1\n")
    plugin.write("tests/x/test_a.py", "from _helper import X\n")
    plugin.write("tests/y/test_b.py", "from _helper import X\n")
    found = invariants.old_imports(plugin.scope(), OLD)
    assert [(f.path.rsplit("/", 2)[-2], f.line) for f in found] == [("y", 1)]


def test_declared_dependency_is_allowed(plugin):
    plugin.write("src/ndf/a.py", "import httpx\nimport pytest\n")
    assert invariants.old_imports(plugin.scope(), OLD) == []


@pytest.fixture
def changed(plugin):
    base = plugin.commit("base")
    (plugin.repo / OLD).mkdir(parents=True)
    (plugin.repo / OLD / "x.py").write_text("x = 1\n")
    plugin.commit("old")
    return base


def test_old_plugin_diff_fails(plugin, rules, changed):
    found = invariants.old_plugin_diff(plugin.scope(base=changed), rules["invariants"])
    assert [(f.rule, f.path) for f in found] == [("I2", f"{OLD}/x.py")]


def test_immediate_fix_mark_allows_diff(plugin, rules, changed):
    mark = rules["invariants"]["immediate_fix_mark"]
    body = f"本文\n\n{mark} 旧い hook が止まる不具合を直す\n"
    assert invariants.old_plugin_diff(plugin.scope(base=changed, pr_body=body), rules["invariants"]) == []
    empty = f"{mark}\n"
    assert len(invariants.old_plugin_diff(plugin.scope(base=changed, pr_body=empty), rules["invariants"])) == 1


def test_no_base_skips_diff(plugin, rules, changed):
    assert invariants.old_plugin_diff(plugin.scope(), rules["invariants"]) == []


def test_unknown_base_stops(plugin, rules, changed):
    with pytest.raises(CheckError):
        invariants.old_plugin_diff(plugin.scope(base="no-such-ref"), rules["invariants"])
