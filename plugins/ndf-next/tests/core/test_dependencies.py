"""DBOS を import する場所の決まりと、判断のループの依存の向き（AST だけで確かめる）。"""

from __future__ import annotations

import ast
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SRC = PLUGIN_ROOT / "src"


def _imports_dbos(node: ast.AST) -> bool:
    """AST のノード 1 つが DBOS の絶対 import か（相対 import は数えない）。"""
    if isinstance(node, ast.Import):
        names = [alias.name for alias in node.names]
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        names = [node.module]
    else:
        return False
    return any(name.split(".")[0] == "dbos" for name in names)


def _dbos_importers(root: Path, base: Path) -> set[str]:
    """`root` の下で DBOS を読むファイル（`base` からの相対パス）。"""
    found = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        if any(_imports_dbos(node) for node in ast.walk(tree)):
            found.add(path.relative_to(base).as_posix())
    return found


def test_dbos_importers_finds_only_absolute_dbos_imports(tmp_path):
    """現状固定: import の構文ごとに、DBOS を読むファイルとして数えるかどうか。"""
    sources = {
        "plain.py": "import dbos\n",
        "alias_multi.py": "import os, dbos.sub as d\n",
        "from_abs.py": "from dbos import DBOS\n",
        "from_sub.py": "from dbos.sub import x\n",
        "similar.py": "import dboss\nfrom dbosx import y\n",
        "relative.py": "from . import dbos\nfrom .dbos import z\n",
        "not_import.py": "x = 'dbos'\n",
        "nested/deep.py": "import json\nimport dbos\n",
    }
    for name, text in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    assert _dbos_importers(tmp_path, tmp_path) == {"plain.py", "alias_multi.py", "from_abs.py", "from_sub.py", "nested/deep.py"}


def test_only_durable_imports_dbos():
    importers = _dbos_importers(PLUGIN_ROOT / "src", PLUGIN_ROOT) | _dbos_importers(PLUGIN_ROOT / "tests", PLUGIN_ROOT)
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
