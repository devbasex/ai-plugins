from __future__ import annotations

from ndf.checks import structure


def test_lower_layer_reading_upper_layer_fails(plugin, rules):
    plugin.write("src/ndf/core/__init__.py", "")
    plugin.write("src/ndf/core/loop.py", "from ndf.progress import model\n")
    plugin.write("src/ndf/progress/__init__.py", "")
    plugin.write("src/ndf/progress/model.py", "from ndf.core import loop\n")
    found = structure.check(plugin.scope(), rules)
    assert [(f.rule, f.path.rsplit("/", 1)[-1]) for f in found] == [("layer", "loop.py")]


def test_durable_import_only_in_durable_module(plugin, rules):
    plugin.write("src/ndf/core/durable.py", "import dbos\n")
    plugin.write("src/ndf/core/plan.py", "from dbos import DBOS\n")
    found = structure.check(plugin.scope(), rules)
    assert [(f.rule, f.path.rsplit("/", 1)[-1]) for f in found] == [("durable-boundary", "plan.py")]


def test_decide_module_must_be_pure(plugin, rules):
    plugin.write("src/ndf/core/step_decide.py", "import os\nimport dataclasses\n\ndef f(p):\n    return p.exists()\n")
    found = structure.check(plugin.scope(), rules)
    assert [(f.rule, f.line) for f in found] == [("decide-pure", 1), ("decide-pure", 5)]


def test_line_limit_is_a_safety_net(plugin, rules):
    plugin.write("src/ndf/big.py", "x = 1\n" * 1000)
    assert structure.check(plugin.scope(), rules) == []
    plugin.write("src/ndf/big.py", "x = 1\n" * 1001)
    assert [f.rule for f in structure.check(plugin.scope(), rules)] == ["lines"]


def test_line_limit_covers_tests_and_scripts(plugin, rules):
    plugin.write("tests/x/test_big.py", "x = 1\n" * 1001)
    plugin.write("bin/big", "#!/bin/sh\n" + ":\n" * 1000)
    plugin.write("contracts/big.json", "[\n" + "1,\n" * 1000 + "1]\n")
    found = structure.check(plugin.scope(), rules)
    assert [(f.rule, f.path.rsplit("/", 1)[-1]) for f in found] == [("lines", "big"), ("lines", "test_big.py")]


def test_lazy_import_cycle_is_one_finding(plugin):
    plugin.write("src/ndf/a.py", "from ndf import b\n")
    plugin.write("src/ndf/b.py", "def f():\n    from ndf import c\n")
    plugin.write("src/ndf/c.py", "import ndf.a\n")
    plugin.write("src/ndf/d.py", "from ndf import a\n")
    found = structure.cycles(plugin.scope())
    assert len(found) == 1
    assert found[0].path.endswith("src/ndf/a.py")
    assert "b.py" in found[0].detail and "c.py" in found[0].detail and "d.py" not in found[0].detail


def test_relative_imports_are_resolved(plugin):
    plugin.write("src/ndf/pkg/__init__.py", "from . import x\n")
    plugin.write("src/ndf/pkg/x.py", "from .. import pkg\n")
    assert len(structure.cycles(plugin.scope())) == 1


def test_from_import_of_a_module_gets_the_rules(plugin, rules):
    plugin.write("src/ndf/core/__init__.py", "")
    plugin.write("src/ndf/core/loop.py", "from ndf import progress\n")
    plugin.write("src/ndf/core/plan_decide.py", "from ndf.core import durable\n")
    plugin.write("src/ndf/core/step_decide.py", "from ndf import adapters\n")
    found = structure.check(plugin.scope(), rules)
    assert sorted((f.rule, f.path.rsplit("/", 1)[-1]) for f in found) == [
        ("decide-pure", "plan_decide.py"),
        ("decide-pure", "step_decide.py"),
        ("layer", "loop.py"),
    ]
