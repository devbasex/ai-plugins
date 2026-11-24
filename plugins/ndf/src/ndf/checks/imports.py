"""構文木から import を集める（構造の規則・輪・I3 が共有する）。

関数の中の import（遅延の import）も同じく数える。輪を関数の中へ逃がしても輪として残るようにするためである。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class Import:
    line: int
    module: str
    names: tuple[str, ...]


def is_under(module: str, package: str) -> bool:
    """モジュールがパッケージそのものか、その下にあるか（`ndf.pq` は `ndf.p` の下でない）。"""
    return module == package or module.startswith(package + ".")


def _absolute(node: ast.ImportFrom, module: str, is_package: bool) -> str:
    if not node.level:
        return str(node.module)  # 絶対の import は必ず名前を持つ
    base = module.split(".") if is_package else module.split(".")[:-1]
    base = base[: len(base) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def collect(tree: ast.Module, module: str = "", is_package: bool = False) -> list[Import]:
    """`import a.b` と `from a import b` を、絶対の名前で返す。相対の import は `module` から解く。"""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [Import(node.lineno, alias.name, ()) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = tuple(alias.name for alias in node.names)
            found.append(Import(node.lineno, _absolute(node, module, is_package), names))
    return found


def _own_statements(scope: ast.AST) -> Iterator[ast.AST]:
    """囲み（モジュール・関数・クラス）の中の文。内側の関数とクラスの中と、式の中へは降りない。"""
    for node in ast.iter_child_nodes(scope):
        if isinstance(node, ast.expr):
            continue
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield from _own_statements(node)


def bindings(scope: ast.AST, module: str = "", is_package: bool = False) -> dict[str, str]:
    """囲み 1 つの import が束ねる名前 → 完全な名前（内側の関数の import は、その関数の囲みだけに束ねる）。

    `from a import b as c` は `c` → `a.b`、`import a.b` は `a` → `a`。
    """
    found = {}
    for node in _own_statements(scope):
        if isinstance(node, ast.Import):
            found.update(
                (alias.asname, alias.name) if alias.asname else (root, root) for alias in node.names for root in [alias.name.split(".")[0]]
            )
        elif isinstance(node, ast.ImportFrom):
            base = _absolute(node, module, is_package)
            found.update((alias.asname if alias.asname else alias.name, f"{base}.{alias.name}") for alias in node.names)
    return found


def targets(imp: Import, known: set[str]) -> list[str]:
    """import が読む、`known` の中のモジュール。`from pkg import mod` は `pkg.mod` があればそれを指す。"""
    subs = [f"{imp.module}.{n}" for n in imp.names if f"{imp.module}.{n}" in known]
    if subs and len(subs) == len(imp.names):
        return subs
    name = imp.module
    while name and name not in known:
        name = name.rpartition(".")[0]
    return subs + ([name] if name else [])
