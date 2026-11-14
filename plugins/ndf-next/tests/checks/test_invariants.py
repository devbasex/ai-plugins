from __future__ import annotations

import pytest

from ndf.checks import invariants
from ndf.checks.model import CheckError

# 旧いプラグインのパス。テストの本文も I3 が見るため、文字を組み立てて持つ
OLD = "/".join(("plugins", "ndf"))


def test_own_name_in_code_fails(plugin, where):
    plugin.write(
        "src/ndf/a.py",
        '"""ndf11 の説明"""\nP = "ndf11"\nQ = "a/ndf-next/b"\nR = "ndf11:design"\nS = "ndf-next"\nT = "ndf"\n'
        'U = ".ndf/"\nV = "/ndf:design"\nW = "ndf.checks.run"\nX = "src/ndf/a.py"\nY = "ndf11gen"\n',
    )
    plugin.write("bin/run", "#!/bin/sh\n# ndf11/ を見る\necho plugins/ndf-next/x\n")
    plugin.write("skills/a/SKILL.md", "/ndf11:design を呼ぶ\n")
    found = invariants.own_name(plugin.scope())
    lines = [("run", 3), ("a.py", 2), ("a.py", 3), ("a.py", 4), ("a.py", 6), ("a.py", 7), ("a.py", 8)]
    assert where(found) == lines


def test_only_fixed_word_definitions_pass(plugin, where):
    plugin.write("src/ndf/identity.py", 'FINAL_NAME = "ndf"\nFIXED_WORDS = {\n    "declaration_dir": ".ndf",\n}\nZ = ".ndf"\n')
    plugin.write("src/ndf/b.py", 'FIXED_WORDS = {"d": ".ndf"}\n')
    found = invariants.own_name(plugin.scope())
    assert where(found) == [("b.py", 1), ("identity.py", 5)]


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


def test_pytest_pythonpath_module_is_own(plugin):
    plugin.write("tests/support/_server.py", "X = 1\n")
    plugin.write("tests/x/test_a.py", "from _server import X\nimport _absent\n")
    found = invariants.old_imports(plugin.scope(), OLD)
    assert [(f.rule, f.line) for f in found] == [("I3", 2)]


def test_declared_dependency_is_allowed(plugin):
    plugin.write("src/ndf/a.py", "import httpx\nimport pytest\n")
    assert invariants.old_imports(plugin.scope(), OLD) == []


def _old_change(plugin):
    (plugin.repo / OLD).mkdir(parents=True, exist_ok=True)
    (plugin.repo / OLD / "x.py").write_text("x = 1\n")


@pytest.fixture
def changed(plugin):
    """新しいプラグインの子の PR: 新しいプラグインと旧いプラグインの両方を変える。"""
    base = plugin.commit("base")
    _old_change(plugin)
    plugin.write("src/ndf/new.py", "y = 1\n")
    plugin.commit("old")
    return base


@pytest.fixture
def old_only(plugin):
    """旧いプラグインだけを変える PR。"""
    base = plugin.commit("base")
    _old_change(plugin)
    plugin.commit("old")
    return base


def port_line(rules, value="#3343"):
    return f"{rules['invariants']['port_mark']} {value}\n"


def test_old_plugin_diff_fails(plugin, rules, changed):
    found = invariants.old_plugin_diff(plugin.scope(base=changed), rules["invariants"])
    assert [(f.rule, f.path) for f in found] == [("port", f"{OLD}/x.py"), ("I2", f"{OLD}/x.py")]


def test_immediate_fix_mark_allows_diff(plugin, rules, changed):
    mark = rules["invariants"]["immediate_fix_mark"]
    body = f"本文\n\n{mark} 旧い hook が止まる不具合を直す\n{port_line(rules)}"
    assert invariants.old_plugin_diff(plugin.scope(base=changed, pr_body=body), rules["invariants"]) == []
    empty = f"{mark}\n{port_line(rules)}"
    assert [f.rule for f in invariants.old_plugin_diff(plugin.scope(base=changed, pr_body=empty), rules["invariants"])] == ["I2"]


def test_old_only_diff_needs_port_line_but_not_i2(plugin, rules, old_only):
    conf = rules["invariants"]
    assert [(f.rule, f.path) for f in invariants.old_plugin_diff(plugin.scope(base=old_only), conf)] == [("port", f"{OLD}/x.py")]
    for body in (port_line(rules), f"{conf['no_port_mark']} 旧いプラグインだけの文書の誤字\n"):
        assert invariants.old_plugin_diff(plugin.scope(base=old_only, pr_body=body), conf) == []
    for body in (port_line(rules, "あとで"), f"{conf['no_port_mark']}\n"):
        assert [f.rule for f in invariants.old_plugin_diff(plugin.scope(base=old_only, pr_body=body), conf)] == ["port"]


def test_no_base_skips_diff(plugin, rules, changed):
    assert invariants.old_plugin_diff(plugin.scope(), rules["invariants"]) == []


def test_unknown_base_stops(plugin, rules, changed):
    with pytest.raises(CheckError):
        invariants.old_plugin_diff(plugin.scope(base="no-such-ref"), rules["invariants"])
