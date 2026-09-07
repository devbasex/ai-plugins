"""モジュール間の境界を機械で確かめる（#441）。

**人が見ている限り、次の変更でまた混ざる。** 分割（#438）で循環が見つかったのは、
責務のコメントが依存を反映していなかったためである。同じことは繰り返される。

見るのは 2 つ。

| 見るもの | なぜ |
| --- | --- |
| 依存に循環が無いこと | 循環があると、どちらが土台なのかが決まらない |
| モジュールをまたいで非公開名を渡していないこと | 渡していると、外から見た公開 API が確定しない |
"""
from __future__ import annotations

import ast
import pathlib

LIB = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "refactor_lib"


def _module_name(path: pathlib.Path) -> str:
    return str(path.relative_to(LIB)).replace("/", ".")[: -len(".py")]


def _imports(path: pathlib.Path) -> list[tuple[str, list[str]]]:
    """`from ... import ...` を (取り込み元のモジュール名, 名前) で返す。

    相対の指定は `refactor_lib` の中の絶対の名前へ直す。`.` の数がさかのぼる階層で、
    1 つ目は自分の親を指す。
    """
    out: list[tuple[str, list[str]]] = []
    here = _module_name(path).split(".")
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        base = here[: len(here) - node.level]
        target = ".".join(base + ([node.module] if node.module else []))
        out.append((target, [a.name for a in node.names]))
    return out


def _modules() -> list[pathlib.Path]:
    return sorted(p for p in LIB.rglob("*.py") if p.name != "__init__.py")


def test_no_private_name_crosses_a_module() -> None:
    """モジュールをまたいで `_` 始まりの名前を渡さないこと。

    渡していると、そのモジュールが何を公開しているのかが外から決まらない。
    """
    crossing = [
        f"{_module_name(p)} ← {target}.{name}"
        for p in _modules()
        for target, names in _imports(p)
        for name in names
        if name.startswith("_") and target != ""
    ]
    assert crossing == []


def test_the_dependency_graph_has_no_cycle() -> None:
    """依存に循環が無いこと。"""
    graph = {
        _module_name(p): {t for t, _ in _imports(p) if t}
        for p in _modules()
    }
    seen: set[str] = set()
    stack: list[str] = []

    def walk(node: str) -> list[str]:
        if node in stack:
            return stack[stack.index(node):] + [node]
        if node in seen:
            return []
        seen.add(node)
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            cycle = walk(nxt)
            if cycle:
                return cycle
        stack.pop()
        return []

    for start in sorted(graph):
        cycle = walk(start)
        assert cycle == [], f"循環: {' → '.join(cycle)}"


def test_the_commands_layer_has_no_sideways_dependency() -> None:
    """`commands` 層どうしが互いを取り込まないこと。

    コマンドは入口から呼ばれる単位であり、互いの内部を使うと、どちらが先に
    決まるのかが読めなくなる。共有するものは下の層へ置く。
    """
    sideways = [
        f"{_module_name(p)} ← {target}"
        for p in _modules()
        if _module_name(p).startswith("commands.")
        for target, _ in _imports(p)
        if target.startswith("commands.")
    ]
    assert sideways == []
