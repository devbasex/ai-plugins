"""DBOS を import する場所の決まりと、判断のループの依存の向き（AST だけで確かめる）。"""

from __future__ import annotations

import ast
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SRC = PLUGIN_ROOT / "src"


def _dbos_importers(root: Path) -> set[str]:
    found = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            if any(name.split(".")[0] == "dbos" for name in names):
                found.add(path.relative_to(PLUGIN_ROOT).as_posix())
    return found


def test_only_durable_imports_dbos():
    importers = _dbos_importers(PLUGIN_ROOT / "src") | _dbos_importers(PLUGIN_ROOT / "tests")
    assert importers == {"src/ndf/core/durable.py"}


def test_loop_reads_neither_durable_nor_dbos():
    tree = ast.parse((SRC / "ndf" / "core" / "loop.py").read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    assert not any(m.startswith(("dbos", "ndf")) for m in modules), modules
