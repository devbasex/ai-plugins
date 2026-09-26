"""`state.py` と `review_lib/` の境界を機械で確かめる（#1142 の C2）。

`state.py` は副命令の引数の解析と `main` だけを持ち、中身は `review_lib/` の 21 本が持つ。
import の向きは設計（`issues/issue-1142-design-modules.md` の review_lib の節）の一方向にする。

| 見るもの | なぜ |
| --- | --- |
| import が下の層へだけ向くこと | 循環があると、どちらが土台なのかが決まらない |
| `commands/` どうしが import し合わないこと | 副命令の本体が別の副命令の中身に寄りかかると、1 本だけを読んで直せない |
| 名前でなくモジュールを取り込むこと | テストは定義したモジュールの上で差し替える。名前を取り込むと差し替えが効かない |
| `state.py` が副命令の本体を持たないこと | エントリポイントに本体が戻ると、分けた意味が無くなる |
"""
from __future__ import annotations

import ast
import pathlib

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
LIB = SCRIPTS / "review_lib"

# 層。import は自分より小さい層へだけ向く
LAYERS = {
    "review_lib": 0,
    "review_lib.store": 1, "review_lib.github": 1, "review_lib.categories": 1, "review_lib.review_focus": 1,
    "review_lib.workspace": 2, "review_lib.ci": 2, "review_lib.findings": 2, "review_lib.posts": 2,
    "review_lib.fix_result": 2,
    "review_lib.participants": 3,
    "review_lib.matching": 4,
}
COMMANDS_LAYER = 5


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(SCRIPTS).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _layer(name: str) -> int:
    if name.startswith("review_lib.commands"):
        return COMMANDS_LAYER
    return LAYERS[name]


def _modules() -> list[pathlib.Path]:
    return sorted(LIB.rglob("*.py"))


def _review_lib_imports(path: pathlib.Path) -> list[tuple[str, str]]:
    """`review_lib` の中を指す import を (取り込み元, 取り込んだもの) で返す。"""
    out = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            out += [(a.name, a.name) for a in node.names if a.name.split(".")[0] == "review_lib"]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "review_lib":
            out += [(node.module, a.name) for a in node.names]
    return out


def _is_module(dotted: str) -> bool:
    path = SCRIPTS / dotted.replace(".", "/")
    return path.with_suffix(".py").exists() or (path / "__init__.py").exists()


def _target(module: str, name: str) -> str:
    """`from <module> import <name>` が取り込むモジュール（名前ならその定義元の `module`）。"""
    return f"{module}.{name}" if module != name and _is_module(f"{module}.{name}") else module


def test_the_package_has_the_designed_modules() -> None:
    commands = {"init", "start_round", "read_result", "verify_findings", "collect_critiques", "judge", "loop",
                "merge_fix", "report"}
    expected = set(LAYERS) | {"review_lib.commands"} | {f"review_lib.commands.{c}" for c in commands}
    assert {_module_name(p) for p in _modules()} == expected


@pytest.mark.parametrize("path", _modules(), ids=lambda p: _module_name(p))
def test_imports_point_to_a_lower_layer(path: pathlib.Path) -> None:
    here = _module_name(path)
    wrong = []
    for module, name in _review_lib_imports(path):
        target = _target(module, name)
        if here.startswith("review_lib.commands") and target.startswith("review_lib.commands"):
            wrong.append(f"{here} → {target}（commands どうし）")
        elif _layer(target) >= _layer(here):
            wrong.append(f"{here} → {target}")
    assert wrong == []


@pytest.mark.parametrize("path", [*_modules(), SCRIPTS / "state.py"], ids=lambda p: _module_name(p))
def test_modules_are_imported_not_names(path: pathlib.Path) -> None:
    """`from review_lib.x import 関数` の形で名前を取り込まない（差し替えが効かなくなる）。"""
    names = []
    for module, name in _review_lib_imports(path):
        if module != name and not _is_module(f"{module}.{name}"):
            names.append(f"{module}.{name}")
    assert names == []


def test_the_state_script_only_parses_the_arguments() -> None:
    """`state.py` は副命令の登録・引数の型・`main` だけを持つ。"""
    tree = ast.parse((SCRIPTS / "state.py").read_text(encoding="utf-8"))
    functions = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    allowed = {"build_parser", "main", "_runtime_or_none", "_runtime_list", "_seat_arg"}
    assert [f for f in functions if f not in allowed and not (f.startswith("_add_") and f.endswith("_parser"))] == []
    assert not [n for n in tree.body if isinstance(n, ast.ClassDef)]
