from __future__ import annotations

import json

import pytest

from ndf.checks import child_deps
from ndf.checks.model import CheckError


def row(packages=(), needs=(), order=1, lane="a", closed=False):
    return {"packages": list(packages), "needs": list(needs), "order": order, "lane": lane, "closed": closed}


def table(plugin, children, tables=None):
    path = plugin.root / child_deps.CHILDREN_REL
    path.write_text(json.dumps({"children": children, "tables": tables or {}}))
    return child_deps.load_children(path, child_deps.CHILDREN_REL)


def findings(plugin, children, tables=None):
    return [(f.path.rsplit("/", 1)[-1], f.line, f.detail) for f in child_deps.check(plugin.scope(), table(plugin, children, tables), 2)]


BASE = {"A": row(["ndf.a"]), "B": row(["ndf.b"], ["A"], 2), "C": row(["ndf.c"], ["B"], 3), "D": row(["ndf.d"], order=2, lane="b")}


def test_transitive_import_inside_closure_passes(plugin):
    plugin.write("src/ndf/a/__init__.py", "")
    plugin.write("src/ndf/b.py", "X = 1\n")
    plugin.write("src/ndf/c.py", "from ndf import b\nfrom ndf.a import x\n")
    plugin.write("src/ndf/a/x.py", "Y = 1\n")
    plugin.write("src/ndf/d.py", "Z = 1\n")
    assert findings(plugin, BASE) == []


def test_import_outside_closure_fails(plugin):
    plugin.write("src/ndf/a.py", "X = 1\n")
    plugin.write("src/ndf/d.py", "import ndf.a\n")
    plugin.write("src/ndf/b.py", "from ndf import d\n")
    found = findings(plugin, BASE)
    assert [(p, line) for p, line, _ in found] == [("b.py", 1), ("d.py", 1)]
    assert "D の先に要るものに無い A の ndf.a を読む（IG1）" in found[1][2]


def test_addendum_closure_extends_original_modules(plugin):
    plugin.write("src/ndf/a.py", "X = 1\n")
    plugin.write("src/ndf/d.py", "import ndf.a\n")
    assert findings(plugin, {**BASE, "D+": row(needs=["D", "A"], order=3, lane="b")}) == []


def test_module_without_owner_fails(plugin):
    plugin.write("src/ndf/e/__init__.py", "")
    plugin.write("src/ndf/e/f.py", "X = 1\n")
    found = findings(plugin, BASE)
    assert [(p, line) for p, line, _ in found] == [("f.py", 1)]
    assert "ndf.e.f" in found[0][2]


def test_three_open_children_in_one_slot_fail(plugin):
    children = {**BASE, "E": row(order=2, lane="b"), "F": row(order=2, lane="b")}
    found = findings(plugin, children)
    assert len(found) == 1 and "レーン b の順番 2 に閉じていない子が 3 本" in found[0][2]
    children["F"]["closed"] = True
    assert findings(plugin, children) == []


def test_order_not_after_needs_fails(plugin):
    found = findings(plugin, {**BASE, "C": row(["ndf.c"], ["B"], 2)})
    assert [(p, d) for p, _, d in found] == [("children.json", "C の順番 2 が先に要る子（B）の順番より大きくない（IG2）")]


def test_closed_child_order_is_not_checked(plugin):
    """閉じた子は流し終えたので、後の子が足した依存を先に要るものへ書いても順番を問わない。"""
    assert findings(plugin, {**BASE, "C": row(["ndf.c"], ["B"], 2, closed=True)}) == []


def test_parallel_writers_of_one_table_fail(plugin):
    tables = {"contracts/limits.json": ["A", "C"]}
    assert findings(plugin, BASE, tables) == []
    found = findings(plugin, BASE, {"contracts/limits.json": ["C", "D"]})
    assert len(found) == 1 and "表 contracts/limits.json へ C と D が並行で書く" in found[0][2]
    closed = {**BASE, "D": row(["ndf.d"], order=2, lane="b", closed=True)}
    assert findings(plugin, closed, {"contracts/limits.json": ["C", "D"]}) == []


def test_cycle_in_needs_stops(plugin):
    children = table(plugin, {"A": row(needs=["B"]), "B": row(needs=["A"], order=2)})
    with pytest.raises(CheckError, match="輪"):
        child_deps.closure("A", children)


@pytest.mark.parametrize(
    "children",
    [
        {"A": {"packages": [], "needs": [], "order": "1", "lane": "a", "closed": False}},
        {"A": row(needs=["Z"])},
        {"A": row(["ndf.a"]), "B": row(["ndf.a"])},
    ],
)
def test_broken_table_stops(plugin, children):
    with pytest.raises(CheckError):
        table(plugin, children)


def test_owner_is_the_longest_package(plugin):
    children = table(plugin, {"A": row(["ndf.p"]), "B": row(["ndf.p.q"])})
    assert [child_deps.owner(m, children) for m in ("ndf.p.x", "ndf.p.q.y", "ndf.pq", "ndf.p")] == ["A", "B", None, "A"]
