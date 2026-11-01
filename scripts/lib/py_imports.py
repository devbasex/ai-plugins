"""NDF のスクリプトが import する名前と、その名前のファイルをたどる共通部品。

`scripts/check-script-structure.py` と `scripts/check-ndf-contracts.py` が同じ規則でたどる。
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

LIB = "plugins/ndf/scripts/"
IMPORT_ROOTS = (LIB, LIB + "lib/")


def imported(rel: str, text: str, roots: Iterable[str] = IMPORT_ROOTS) -> set[str]:
    """モジュールが import する名前（関数の中の import も含む。`from . import x` は `<パッケージ>.x`）。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    return imported_tree(rel, tree, roots)


def imported_tree(rel: str, tree: ast.AST, roots: Iterable[str] = IMPORT_ROOTS) -> set[str]:
    """`imported` の構文木を受ける版（構文木を 1 回だけ作る呼び手のため）。"""
    pkg = Path(rel).parent.name if Path(rel).parent.as_posix() + "/" not in tuple(roots) else ""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level and pkg:
                base = f"{pkg}.{base}" if base else pkg
            names.add(base) if base else None
            names |= {f"{base}.{a.name}" if base else a.name for a in node.names}
    return names


def module_file(root: Path, name: str, roots: Iterable[str] = IMPORT_ROOTS) -> str | None:
    """import の名前に当たるファイル（root からの相対パス）。見つからなければ None。"""
    for base in roots:
        stem = base + name.replace(".", "/")
        for cand in (stem + ".py", stem + "/__init__.py"):
            if (root / cand).is_file():
                return cand
    return None
