"""エントリポイントから import でたどれるモジュールが表に収まる（M3-H3・IH2・IH7）。構文木で関数の中の import もたどる。"""

from __future__ import annotations

import ast
import fnmatch
import sys
from pathlib import Path

import pytest

from ndf.domains.hooks import tables

SRC = tables.PLUGIN_ROOT / "src"
TABLE = tables.load("hooks/imports.json")


def _module_file(name: str) -> Path | None:
    base = SRC.joinpath(*name.split("."))
    for path in (base.with_suffix(".py"), base / "__init__.py"):
        if path.is_file():
            return path
    return None


def _imports(path: Path, module: str) -> set[str]:
    """ファイルが読む絶対の名前。`from a import b` は a.b がモジュールならそれを、ほかは a を数える。"""
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*parts, base] if base else parts)
            found.add(base)
            found |= {f"{base}.{a.name}" for a in node.names if _module_file(f"{base}.{a.name}")}
    return {n for n in found if n and n != "__future__"}


def _with_parents(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def closure(roots: list[str]) -> set[str]:
    """roots から import でたどれる名前（NDF の中はファイルを開いてたどる。外は名前だけ）。親のパッケージも含む。"""
    seen: set[str] = set()
    todo = [n for r in roots for n in _with_parents(r)]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        path = _module_file(name) if name.split(".")[0] == "ndf" else None
        if path is not None:
            todo += [p for n in _imports(path, name) for p in _with_parents(n)]
    return seen


def _top(name: str) -> str:
    return name.split(".")[0]


def _allowed(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def _entry_roots() -> list[str]:
    return [*TABLE["entry"]["roots"], *(g["module"] for g in tables.guards())]


def test_entry_closure_stays_within_the_table():
    names = closure(_entry_roots())
    deps = set(TABLE["entry"]["dependencies"])
    bad = [
        n
        for n in sorted(names)
        if _top(n) not in sys.stdlib_module_names and _top(n) not in deps and not (_top(n) == "ndf" and _allowed(n, TABLE["entry"]["modules"]))
    ]
    assert bad == []


def test_entry_closure_does_not_reach_forbidden_modules():
    names = closure(_entry_roots())
    hits = sorted(n for n in names for f in TABLE["entry"]["forbidden"] if n == f or n.startswith(f + "."))
    assert hits == []
    # bin/ndf（python -m ndf）と依存を入れ直す処理を通らない
    assert "ndf.cli" not in names and "ndf.__main__" not in names


@pytest.mark.parametrize("root", TABLE["stdlib_roots"])
def test_stdlib_only_roots_reach_only_the_standard_library(root):
    names = closure([root])
    bad = [n for n in sorted(names) if _top(n) != "ndf" and _top(n) not in sys.stdlib_module_names]
    bad += [n for n in sorted(names) if _top(n) == "ndf" and n not in TABLE["stdlib_only"]]
    assert bad == []


def test_hook_scripts_import_only_the_standard_library_and_stdlib_modules():
    # S1 が置く hooks/boot.py・hooks/prepare.py も同じ表で縛る（置かれていれば）
    for rel in TABLE["scripts"]:
        path = tables.PLUGIN_ROOT / rel
        if not path.is_file():
            continue
        names = {n for n in _imports(path, "hooks._script")}
        bad = [n for n in names if _top(n) != "ndf" and _top(n) not in sys.stdlib_module_names]
        bad += [n for n in names if _top(n) == "ndf" and n not in TABLE["stdlib_only"] and not closure([n]) <= set(TABLE["stdlib_only"]) | set(sys.stdlib_module_names)]
        assert bad == [], rel


def test_closure_follows_function_level_imports(tmp_path):
    # エントリポイントの本体は関数の中で import する（strict を依存より先に呼ぶため）。それも閉包に入る
    names = closure(["ndf.domains.hooks.main"])
    assert {"ndf.domains.hooks.dispatch", "ndf.domains.hooks.strict", "tree_sitter_bash"} <= names
