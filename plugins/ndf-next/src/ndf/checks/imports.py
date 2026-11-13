"""構文木から import を集める（構造の規則・輪・I3 が共有する）。

関数の中の import（遅延の import）も同じく数える。輪を関数の中へ逃がしても輪として残るようにするためである。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class Import:
    line: int
    module: str
    names: tuple[str, ...]


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


def targets(imp: Import, known: set[str]) -> list[str]:
    """import が読む、`known` の中のモジュール。`from pkg import mod` は `pkg.mod` があればそれを指す。"""
    subs = [f"{imp.module}.{n}" for n in imp.names if f"{imp.module}.{n}" in known]
    if subs and len(subs) == len(imp.names):
        return subs
    name = imp.module
    while name and name not in known:
        name = name.rpartition(".")[0]
    return subs + ([name] if name else [])
