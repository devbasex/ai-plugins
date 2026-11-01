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


def scripts_root(rel: str) -> str | None:
    """モジュールを含む最も近い `scripts/` ディレクトリ（入口がそこから起動され、`sys.path` の先頭になる）。"""
    parts = Path(rel).parent.parts
    for i in range(len(parts), 0, -1):
        if parts[i - 1] == "scripts":
            return Path(*parts[:i]).as_posix() + "/"
    return None


def imported_files(root: Path, rel: str, tree: ast.AST, roots: Iterable[str] = IMPORT_ROOTS) -> set[str]:
    """モジュールが import するファイル。相対 import はモジュールの場所から `level` だけ上がって解き、
    絶対 import は `roots` から解く（`from ..paths import x` を `<親のパッケージ>/paths.py` へ）。"""
    roots = tuple(roots)
    here = Path(rel).parent
    out: set[str | None] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {module_file(root, a.name, roots) for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            bases = roots
            if node.level:
                pkg = here
                for _ in range(node.level - 1):
                    pkg = pkg.parent
                bases = (pkg.as_posix() + "/",)
                if not mod and (root / pkg / "__init__.py").is_file():
                    out.add((pkg / "__init__.py").as_posix())
            if mod:
                out.add(module_file(root, mod, bases))
            out |= {module_file(root, f"{mod}.{a.name}" if mod else a.name, bases) for a in node.names}
    out.discard(None)
    out.discard(rel)
    return out  # type: ignore[return-value]


def module_file(root: Path, name: str, roots: Iterable[str] = IMPORT_ROOTS) -> str | None:
    """import の名前に当たるファイル（root からの相対パス）。見つからなければ None。"""
    for base in roots:
        stem = base + name.replace(".", "/")
        for cand in (stem + ".py", stem + "/__init__.py"):
            if (root / cand).is_file():
                return cand
    return None
