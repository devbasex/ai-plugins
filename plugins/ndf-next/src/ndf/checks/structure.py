"""構造の規則: ファイルの行数の安全網・層の向き・耐久の層の境界・判断のモジュールの純粋さ・import の輪。

ファイルは責務で分ける。行数の上限（`max_lines`）は分け忘れを拾う安全網で、上限に合わせて切るためのものではない（RF2）。
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Any

from ndf.checks import imports
from ndf.checks.model import Finding, Scope, Source, module_name


def _layer(module: str, layers: list[str]) -> int | None:
    hits = [i for i, prefix in enumerate(layers) if module == prefix or module.startswith(prefix + ".")]
    return hits[0] if hits else None


def _within(module: str, prefixes: list[str]) -> bool:
    return any(module == p or module.startswith(p + ".") for p in prefixes)


def _pointed(imp: imports.Import) -> list[str]:
    """import が指す名前。`from pkg import mod` は `pkg` と `pkg.mod` の両方を指す（`mod` がモジュールでも名前でも規則を当てる）。"""
    return [imp.module, *(f"{imp.module}.{n}" for n in imp.names if n != "*")]


def _first(names: list[str], hit: Callable[[str], bool]) -> str | None:
    return next((n for n in names if hit(n)), None)


def _import_findings(rel: str, module: str, imps: list[imports.Import], rules: dict[str, Any]) -> list[Finding]:
    """1 つのモジュールの import に、層の向き・耐久の層の境界・判断のモジュールの純粋さを当てる。1 つの import で規則ごとに 1 件。"""
    found = []
    layers = rules["layers"]
    mine = _layer(module, layers)
    is_decide = module.rsplit(".", 1)[-1].endswith(rules["decide_suffix"])

    def above(name: str) -> bool:
        theirs = _layer(name, layers)
        return mine is not None and theirs is not None and theirs > mine

    for imp in imps:
        names = _pointed(imp)
        upper = _first(names, above)
        if upper is not None:
            found.append(Finding("layer", rel, imp.line, f"下の層 {layers[mine]} が上の層 {layers[_layer(upper, layers)]} を読む"))
        durable = _first(names, lambda n: _within(n, rules["durable_imports"]))
        if durable is not None and module != rules["durable_module"]:
            found.append(Finding("durable-boundary", rel, imp.line, f"{durable} を読めるのは {rules['durable_module']} だけ"))
        impure = _first(names, lambda n: _within(n, rules["decide_forbidden_imports"]))
        if is_decide and impure is not None:
            found.append(Finding("decide-pure", rel, imp.line, f"判断のモジュールが {impure} を読む"))
    return found


def _decide_calls(rel: str, tree: ast.Module, forbidden: frozenset[str]) -> list[Finding]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        if name in forbidden:
            found.append(Finding("decide-pure", rel, node.lineno, f"判断のモジュールが {name}() を呼ぶ"))
    return found


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    """`src/` の下のモジュールに構造の規則（輪を除く）を当てる。"""
    conf = rules["structure"]
    found = []
    for src in scope.code():
        lines = len(src.text.splitlines())
        if lines > conf["max_lines"]:
            found.append(Finding("lines", src.rel, 1, f"{lines} 行（安全網 {conf['max_lines']} 行）。責務で分ける"))
        module = module_name(scope, src)
        imps = imports.collect(src.tree, module, src.path.name == "__init__.py")
        found += _import_findings(src.rel, module, imps, conf)
        if module.rsplit(".", 1)[-1].endswith(conf["decide_suffix"]):
            found += _decide_calls(src.rel, src.tree, frozenset(conf["decide_forbidden_calls"]))
    return found


class _Tarjan:
    """Tarjan の方法で強連結成分を求める（再帰を使わない）。"""

    def __init__(self, graph: dict[str, set[str]]) -> None:
        self.graph = graph
        self.index: dict[str, int] = {}
        self.low: dict[str, int] = {}
        self.stack: list[str] = []
        self.on_stack: set[str] = set()
        self.comps: list[list[str]] = []

    def run(self) -> list[list[str]]:
        for root in sorted(self.graph):
            if root not in self.index:
                work = [self._enter(root)]
                while work:
                    self._step(work)
        return self.comps

    def _enter(self, node: str) -> tuple[str, object]:
        self.index[node] = self.low[node] = len(self.index)
        self.stack.append(node)
        self.on_stack.add(node)
        return node, iter(sorted(self.graph[node]))

    def _step(self, work: list) -> None:
        node, children = work[-1]
        child = next(children, None)
        if child is not None and child not in self.index:
            work.append(self._enter(child))
        elif child is not None:
            if child in self.on_stack:
                self.low[node] = min(self.low[node], self.index[child])
        else:
            work.pop()
            if work:
                parent = work[-1][0]
                self.low[parent] = min(self.low[parent], self.low[node])
            if self.low[node] == self.index[node]:
                self._pop_component(node)

    def _pop_component(self, node: str) -> None:
        comp = []
        top = None
        while top != node:
            top = self.stack.pop()
            self.on_stack.discard(top)
            comp.append(top)
        self.comps.append(sorted(comp))


def cycles(scope: Scope) -> list[Finding]:
    """`src/` の下のモジュールの import の輪（関数の中の import を含む）を、輪ごとに 1 件返す。"""
    by_module: dict[str, Source] = {module_name(scope, s): s for s in scope.code()}
    known = set(by_module)
    graph: dict[str, set[str]] = {m: set() for m in known}
    for module, src in by_module.items():
        for imp in imports.collect(src.tree, module, src.path.name == "__init__.py"):
            graph[module].update(t for t in imports.targets(imp, known) if t != module)
    found = []
    for comp in _Tarjan(graph).run():
        if len(comp) > 1:
            paths = sorted(by_module[m].rel for m in comp)
            found.append(Finding("cycle", paths[0], 1, "import の輪: " + " → ".join(paths)))
    return found
