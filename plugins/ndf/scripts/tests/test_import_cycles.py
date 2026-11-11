"""#2988: 結果を出す層と cross-refactoring の台帳のまわりに import の輪が無い。

各ファイルの import を `ast` で集めて有向グラフにし、到達で輪を判定する。
- `lib/`: 最上位・関数の中・`if TYPE_CHECKING:` の中の import と、`importlib.import_module` の文字列の定数（遅延を含む）
- `refactor_lib/`: 最上位の相対の import だけ（関数の中と `if TYPE_CHECKING:` の中は数えない）
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
LIB = SCRIPTS / "lib"
REFACTOR_LIB = SCRIPTS.parent / "skills" / "cross-refactoring" / "scripts" / "refactor_lib"
LOWEST = ("step_result", "deps", "run_core")
LEDGER_SET = ("gitfacts", "publish", "plan", "ledger")


def _modules(d: Path) -> dict[str, ast.Module]:
    return {p.stem: ast.parse(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.py")) if p.stem != "__init__"}


def _import_module_name(node: ast.AST) -> str | None:
    """`importlib.import_module("x")` の文字列の定数（動的に組み立てた名前は数えない）。"""
    if not (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)):
        return None
    f = node.func
    if isinstance(f, ast.Attribute) and f.attr == "import_module" or isinstance(f, ast.Name) and f.id == "import_module":
        return node.args[0].value if isinstance(node.args[0].value, str) else None
    return None


def lazy_edges(d: Path) -> dict[str, set[str]]:
    """`d` の平らなモジュールの間の import の辺（遅延を含む）。"""
    mods = _modules(d)
    edges: dict[str, set[str]] = {m: set() for m in mods}
    for m, tree in mods.items():
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            elif (name := _import_module_name(node)) is not None:
                names = [name.split(".")[0]]
            edges[m] |= {n for n in names if n in mods and n != m}
    return edges


def _is_type_checking(node: ast.If) -> bool:
    t = node.test
    return isinstance(t, ast.Name) and t.id == "TYPE_CHECKING" or isinstance(t, ast.Attribute) and t.attr == "TYPE_CHECKING"


def _top_level(nodes: list[ast.stmt]):
    """読み込みのときに動く文（関数の本体と `if TYPE_CHECKING:` の中を除く）。"""
    for n in nodes:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(n, ast.If) and _is_type_checking(n):
            yield from _top_level(n.orelse)
            continue
        yield n
        for field in ("body", "orelse", "finalbody", "handlers"):
            yield from _top_level([c for c in getattr(n, field, []) if isinstance(c, (ast.stmt, ast.ExceptHandler))])


def top_level_relative_edges(d: Path) -> dict[str, set[str]]:
    """パッケージ `d` の中のモジュールの間の、最上位の相対の import の辺。"""
    mods = _modules(d)
    edges: dict[str, set[str]] = {m: set() for m in mods}
    for m, tree in mods.items():
        for node in _top_level(tree.body):
            if not (isinstance(node, ast.ImportFrom) and node.level == 1):
                continue
            names = [node.module.split(".")[0]] if node.module else [a.name for a in node.names]
            edges[m] |= {n for n in names if n in mods and n != m}
    return edges


def reachable(edges: dict[str, set[str]], start: str) -> set[str]:
    """`start` から辺を 1 本以上辿って届くモジュール。"""
    seen: set[str] = set()
    stack = list(edges.get(start, ()))
    while stack:
        n = stack.pop()
        if n not in seen:
            seen.add(n)
            stack.extend(edges.get(n, ()))
    return seen


def in_same_cycle(edges: dict[str, set[str]], a: str, b: str) -> bool:
    return b in reachable(edges, a) and a in reachable(edges, b)


def test_step_result_does_not_import_deps_or_run_core():
    """受け入れ条件 1・I6: 結果を出す層は、どの形でも依存の確認と実行の核を読まない。"""
    assert not lazy_edges(LIB)["step_result"] & {"deps", "run_core"}


@pytest.mark.parametrize("a, b", [(a, b) for i, a in enumerate(LOWEST) for b in LOWEST[i + 1 :]])
def test_the_lowest_layer_has_no_cycle_even_with_lazy_imports(a, b):
    """受け入れ条件 5・I7: 結果を出す層・依存の確認・実行の核のうち 2 つを含む輪が、遅延を含む辺に無い。"""
    assert not in_same_cycle(lazy_edges(LIB), a, b)


@pytest.mark.parametrize("name", LEDGER_SET)
def test_refactor_lib_ledger_set_has_no_top_level_cycle(name):
    """受け入れ条件 3・5・I8: gitfacts・publish・plan・ledger のどれも、最上位の import で自分へ戻れない。"""
    assert name not in reachable(top_level_relative_edges(REFACTOR_LIB), name)


def test_the_cycle_check_reports_a_cycle_and_only_a_cycle():
    """輪を足すと落ちる: 判定は輪を含む辺の組で輪を報告し、輪の無い組で報告しない。"""
    looped = {"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}}
    assert in_same_cycle(looped, "a", "c") and "b" in reachable(looped, "b")
    assert not in_same_cycle(looped, "a", "d") and "d" not in reachable(looped, "d")
    chain = {"a": {"b"}, "b": {"c"}, "c": set()}
    assert not any(n in reachable(chain, n) for n in chain)


def test_the_edge_collectors_see_lazy_and_top_level_imports(tmp_path):
    """遅延の辺は関数の中と import_module の文字列を数え、最上位の辺は関数の中と TYPE_CHECKING の中を数えない。"""
    flat = tmp_path / "flat"
    flat.mkdir()
    (flat / "a.py").write_text("def f():\n    import b\n")
    (flat / "b.py").write_text("import importlib\ndef g():\n    importlib.import_module('c')\n")
    (flat / "c.py").write_text("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import a\n")
    assert lazy_edges(flat) == {"a": {"b"}, "b": {"c"}, "c": {"a"}}
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "x.py").write_text("from . import y\ndef f():\n    from . import z\n")
    (pkg / "y.py").write_text("try:\n    from .z import v\nexcept ImportError:\n    pass\n")
    (pkg / "z.py").write_text("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from . import x\n")
    assert top_level_relative_edges(pkg) == {"x": {"y"}, "y": {"z"}, "z": set()}
