"""形の番号の上げ忘れを落とす（決定 84）。耐久ワークフローの関数のソースの hash を FORMAT_SOURCE_HASH と比べる。"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from ndf.core import durable

SRC = Path(__file__).resolve().parents[2] / "src"


def _is_workflow_decorator(node: ast.expr) -> bool:
    """`@durable.workflow` か、durable.py の中の `@workflow` か。"""
    if isinstance(node, ast.Attribute):
        return node.attr == "workflow" and isinstance(node.value, ast.Name) and node.value.id == "durable"
    return isinstance(node, ast.Name) and node.id == "workflow"


def workflow_source_hash(src: Path) -> str:
    """`src` の下の耐久ワークフローの関数（下請けの関数は含まない）のソースを、パスと名前の順に並べた sha256。"""
    parts = []
    for path in sorted(src.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(_is_workflow_decorator(d) for d in node.decorator_list):
                parts.append(f"{path.relative_to(src).as_posix()}:{node.name}\n{ast.get_source_segment(text, node)}\n")
    return hashlib.sha256("".join(sorted(parts)).encode()).hexdigest()


def test_format_source_hash_matches_the_workflow_functions():
    """耐久ワークフローの関数を変えたら、FORMAT を上げるか、呼び出しの順が変わらないと判断して hash だけを書き直す。"""
    assert workflow_source_hash(SRC) == durable.FORMAT_SOURCE_HASH


def test_workflow_source_hash_changes_with_a_workflow_body(tmp_path):
    module = tmp_path / "ndf" / "plan.py"
    module.parent.mkdir(parents=True)
    module.write_text("from ndf.core import durable\n\n@durable.workflow\ndef run():\n    return 1\n")
    before = workflow_source_hash(tmp_path)
    helper = tmp_path / "ndf" / "helper.py"
    helper.write_text("def helper():\n    return 2\n")
    assert workflow_source_hash(tmp_path) == before

    module.write_text("from ndf.core import durable\n\n@durable.workflow\ndef run():\n    return 2\n")
    assert workflow_source_hash(tmp_path) != before
