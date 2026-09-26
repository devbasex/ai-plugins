"""テストスクリプトを構文木で検査する（再現可能性レビューチェックリストの 4 項目）。

検査する項目（rule 名）:

- ``page_role``: テスト関数に ``@pytest.mark.page_role(...)`` がある
  （関数・クラスの decorator か、モジュールの ``pytestmark``）
- ``role_pair``: ``@pytest.mark.role("<id>")`` を付けたテストが ``pwk_role_<id>`` fixture を受け取り、
  受け取る ``pwk_role_<id>`` が role の目印と食い違わない。
  目印の無い ``pwk_role_<id>`` だけのテストは通す（fixture がログインを担うため）
- ``hardcoded_url``: ``http://`` / ``https://`` で始まる文字列の直書きが無い
  （docstring は除く。``pwk_config.base_url`` から組み立てる）
- ``wait_until``: ``goto`` / ``reload`` / ``go_back`` / ``go_forward`` に ``wait_until=`` がある

残りの 4 項目（乱数や時刻への依存・テストデータの独立性・異常系の網羅・ndf plugin 非依存）は
構文木では判定できない。出力の ``unchecked_items`` に並べ、レビューする者が判断する。

標準出力は常に 1 つの JSON。

終了コード:
    0  違反なし
    1  違反あり
    2  引数の誤り・構文エラーのファイルがある・検査したテストが 0 件

Usage:
    python scripts/lint_scenario.py scenario-test/tests/
    python scripts/lint_scenario.py scenario-test/tests/test_form.py
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

CHECKED_RULES = ("page_role", "role_pair", "hardcoded_url", "wait_until")
UNCHECKED_ITEMS = (
    "再現可能性（乱数・時刻への依存）",
    "テストデータ独立性",
    "assertion 網羅性（異常系）",
    "ndf plugin 非依存",
)
NAVIGATION_METHODS = {"goto", "reload", "go_back", "go_forward"}
_URL = re.compile(r"^https?://", re.I)
_ROLE_FIXTURE = re.compile(r"^pwk_role_(?P<id>\w+)$")


def _marker_name(node: ast.expr) -> tuple[str, ast.Call | None] | None:
    """``pytest.mark.<name>`` / ``pytest.mark.<name>(...)`` なら (name, call) を返す。"""
    call = node if isinstance(node, ast.Call) else None
    target = node.func if call else node
    if (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Attribute)
        and target.value.attr == "mark"
    ):
        return target.attr, call
    return None


def _markers(nodes: list[ast.expr]) -> dict[str, list[ast.Call | None]]:
    found: dict[str, list[ast.Call | None]] = {}
    for node in nodes:
        m = _marker_name(node)
        if m:
            found.setdefault(m[0], []).append(m[1])
    return found


def _module_markers(tree: ast.Module) -> dict[str, list[ast.Call | None]]:
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets
        ):
            value = stmt.value
            items = list(value.elts) if isinstance(value, (ast.List, ast.Tuple)) else [value]
            return _markers(items)
    return {}


def _role_ids(calls: list[ast.Call | None]) -> set[str]:
    ids = set()
    for call in calls:
        if call is None:
            continue
        for arg in call.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                ids.add(arg.value)
    return ids


def _docstring_nodes(tree: ast.Module) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _iter_tests(tree: ast.Module):
    """(関数, 外側のクラス) を返す。pytest が集める名前だけを対象にする。"""
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name.startswith("test"):
            yield stmt, None
        elif isinstance(stmt, ast.ClassDef) and stmt.name.startswith("Test"):
            for sub in stmt.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                    yield sub, stmt


def _enclosing_test(tree: ast.Module) -> dict[int, str]:
    owner: dict[int, str] = {}
    for func, _cls in _iter_tests(tree):
        for node in ast.walk(func):
            owner[id(node)] = func.name
    return owner


def lint_tree(tree: ast.Module, file: str) -> tuple[int, list[dict[str, Any]]]:
    violations: list[dict[str, Any]] = []

    def add(rule: str, node: ast.AST, test: str | None, message: str) -> None:
        violations.append({"file": file, "line": node.lineno, "test": test,
                           "rule": rule, "message": message})

    module_marks = _module_markers(tree)
    tests = 0
    for func, cls in _iter_tests(tree):
        tests += 1
        marks: dict[str, list[ast.Call | None]] = {}
        for source in (module_marks, _markers(cls.decorator_list) if cls else {},
                       _markers(func.decorator_list)):
            for name, calls in source.items():
                marks.setdefault(name, []).extend(calls)

        if "page_role" not in marks:
            add("page_role", func, func.name, "@pytest.mark.page_role(...) が無い")

        params = [a.arg for a in (*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs)]
        fixture_ids = {m["id"] for p in params if (m := _ROLE_FIXTURE.match(p))}
        marker_ids = _role_ids(marks.get("role", []))
        for rid in sorted(marker_ids - fixture_ids):
            add("role_pair", func, func.name,
                f"@pytest.mark.role({rid!r}) があるが fixture pwk_role_{rid} を受け取っていない")
        if marker_ids:
            for rid in sorted(fixture_ids - marker_ids):
                add("role_pair", func, func.name,
                    f"fixture pwk_role_{rid} が @pytest.mark.role の {sorted(marker_ids)} と食い違う")

    docstrings = _docstring_nodes(tree)
    owner = _enclosing_test(tree)
    # f-string の部品は JoinedStr の側で見る
    fstring_parts = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values}
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            head = node.values[0] if node.values else None
            if isinstance(head, ast.Constant) and isinstance(head.value, str) and _URL.match(head.value):
                add("hardcoded_url", node, owner.get(id(node)),
                    f"URL の直書き: {head.value!r}… (pwk_config.base_url から組み立てる)")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings or id(node) in fstring_parts or not _URL.match(node.value):
                continue
            add("hardcoded_url", node, owner.get(id(node)),
                f"URL の直書き: {node.value!r} (pwk_config.base_url から組み立てる)")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in NAVIGATION_METHODS:
            if not any(k.arg == "wait_until" for k in node.keywords):
                add("wait_until", node, owner.get(id(node)),
                    f"{node.func.attr}() に wait_until= が無い")
    return tests, violations


def collect(paths: list[str]) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    missing: list[str] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(sorted(p.rglob("test_*.py")))
        elif p.is_file():
            files.append(p)
        else:
            missing.append(raw)
    return files, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="テストスクリプトを構文木で検査する")
    parser.add_argument("paths", nargs="+", help="テストのディレクトリかファイル")
    args = parser.parse_args(argv)

    files, missing = collect(args.paths)
    errors: list[dict[str, Any]] = [{"file": m, "message": "見つからない"} for m in missing]
    violations: list[dict[str, Any]] = []
    tests = 0
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except (SyntaxError, UnicodeDecodeError) as exc:
            errors.append({"file": str(f), "message": f"構文木を作れない: {exc}"})
            continue
        n, v = lint_tree(tree, str(f))
        tests += n
        violations.extend(v)
    if not errors and tests == 0:
        # 何も検査していない実行を合格にしない（空のディレクトリ・test_*.py が無い・test 関数が無い）
        errors.append({"file": " ".join(args.paths), "message": "検査したテストが 0 件"})

    violations.sort(key=lambda v: (v["file"], v["line"], v["rule"]))
    result = {
        "files": len(files),
        "tests": tests,
        "violations": violations,
        "errors": errors,
        "checked_rules": list(CHECKED_RULES),
        "unchecked_items": list(UNCHECKED_ITEMS),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        return 2
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
