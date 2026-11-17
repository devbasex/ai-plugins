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


def _module_file(name: str, src: Path) -> Path | None:
    base = src.joinpath(*name.split("."))
    for path in (base.with_suffix(".py"), base / "__init__.py"):
        if path.is_file():
            return path
    return None


def _imports(path: Path, module: str, src: Path) -> set[str]:
    """ファイルが読む絶対の名前。`from a import b` は a.b が `src` の下のモジュールならそれを、ほかは a を数える。"""
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found |= _from_names(node, package, src)
    return {n for n in found if n and n != "__future__"}


def _from_names(node: ast.ImportFrom, package: str, src: Path) -> set[str]:
    """`from a import b` の 1 セクションが読む絶対の名前（相対の基点は `package` から求める）。"""
    base = node.module or ""
    if node.level:
        parts = package.split(".")[: len(package.split(".")) - node.level + 1]
        base = ".".join([*parts, base] if base else parts)
    return {base} | {f"{base}.{a.name}" for a in node.names if _module_file(f"{base}.{a.name}", src)}


def _with_parents(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def closure(roots: list[str], src: Path) -> set[str]:
    """roots から import でたどれる名前（NDF の中は `src` の下のファイルを開いてたどる。外は名前だけ）。親のパッケージも含む。"""
    seen: set[str] = set()
    todo = [n for r in roots for n in _with_parents(r)]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        path = _module_file(name, src) if name.split(".")[0] == "ndf" else None
        if path is not None:
            todo += [p for n in _imports(path, name, src) for p in _with_parents(n)]
    return seen


def _top(name: str) -> str:
    return name.split(".")[0]


def _allowed(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def _entry_roots() -> list[str]:
    return [*TABLE["entry"]["roots"], *(g["module"] for g in tables.guards())]


def test_entry_closure_stays_within_the_table():
    names = closure(_entry_roots(), SRC)
    deps = set(TABLE["entry"]["dependencies"])
    bad = [
        n
        for n in sorted(names)
        if _top(n) not in sys.stdlib_module_names
        and _top(n) not in deps
        and not (_top(n) == "ndf" and _allowed(n, TABLE["entry"]["modules"]))
    ]
    assert bad == []


def test_entry_closure_does_not_reach_forbidden_modules():
    names = closure(_entry_roots(), SRC)
    hits = sorted(n for n in names for f in TABLE["entry"]["forbidden"] if n == f or n.startswith(f + "."))
    assert hits == []
    # bin/ndf（python -m ndf）と依存を入れ直す処理を通らない
    assert "ndf.cli" not in names and "ndf.__main__" not in names


@pytest.mark.parametrize("root", TABLE["stdlib_roots"])
def test_stdlib_only_roots_reach_only_the_standard_library(root):
    names = closure([root], SRC)
    bad = [n for n in sorted(names) if _top(n) != "ndf" and _top(n) not in sys.stdlib_module_names]
    bad += [n for n in sorted(names) if _top(n) == "ndf" and n not in TABLE["stdlib_only"]]
    assert bad == []


def test_hook_scripts_import_only_the_standard_library_and_stdlib_modules():
    # S1 が置く hooks/boot.py・hooks/prepare.py も同じ表で縛る（置かれていれば）
    for rel in TABLE["scripts"]:
        path = tables.PLUGIN_ROOT / rel
        if not path.is_file():
            continue
        names = {n for n in _imports(path, "hooks._script", SRC)}
        bad = [n for n in names if _top(n) != "ndf" and _top(n) not in sys.stdlib_module_names]
        bad += [
            n
            for n in names
            if _top(n) == "ndf"
            and n not in TABLE["stdlib_only"]
            and not closure([n], SRC) <= set(TABLE["stdlib_only"]) | set(sys.stdlib_module_names)
        ]
        assert bad == [], rel


def test_closure_follows_function_level_imports(tmp_path):
    # エントリポイントの本体は関数の中で import する（strict を依存より先に呼ぶため）。それも閉包に入る
    names = closure(["ndf.domains.hooks.main"], SRC)
    assert {"ndf.domains.hooks.dispatch", "ndf.domains.hooks.strict", "tree_sitter_bash"} <= names


# 現状固定（I-011）: closure は渡した SRC の下のファイルを開いてたどる。期待値は今の実装の出力である。
@pytest.mark.parametrize(
    ("roots", "expected"),
    [
        ([], set()),
        (["json"], {"json"}),
        (["os.path"], {"os", "os.path"}),
        (["tree_sitter_bash"], {"tree_sitter_bash"}),
        (["ndf.no_such_module"], {"ndf", "ndf.no_such_module"}),
        (
            ["ndf.domains.hooks.tables"],
            {
                "functools",
                "json",
                "ndf",
                "ndf.domains",
                "ndf.domains.hooks",
                "ndf.domains.hooks.tables",
                "pathlib",
                "typing",
            },
        ),
    ],
)
def test_characterize_closure_reads_files_under_src(roots, expected):
    assert closure(roots, SRC) == expected


# 現状固定（I-011）: 最小の木（一時ディレクトリを根に渡す）で、関数の中の import・相対 import・親の __init__.py・循環・外のモジュールの扱いを固定する。
MINI_TREE = {
    "ndf/__init__.py": "",
    "ndf/a/__init__.py": "import json\n",
    "ndf/a/b.py": "from . import c\nfrom .c import thing\nfrom .. import top\n\n\ndef f():\n    import ext_mod\n    from ndf.a import b\n",
    "ndf/a/c.py": "from ndf.a import b\nimport os.path\n",
    "ndf/top.py": "from __future__ import annotations\n",
}
MINI_ALL = {"ext_mod", "json", "ndf", "ndf.a", "ndf.a.b", "ndf.a.c", "ndf.top", "os", "os.path"}


@pytest.fixture
def mini_src(tmp_path):
    for rel, text in MINI_TREE.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("roots", "expected"),
    [
        (["ndf.a.b"], MINI_ALL),
        (["ndf.a.c"], MINI_ALL),
        (["ndf.top"], {"ndf", "ndf.top"}),
        (["ndf.missing"], {"ndf", "ndf.missing"}),
    ],
)
def test_characterize_closure_on_a_minimal_tree(mini_src, roots, expected):
    assert closure(roots, mini_src) == expected
