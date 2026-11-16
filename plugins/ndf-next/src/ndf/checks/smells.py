"""兆候の規則（AI が陥りやすい作り）のうち、ruff の規則に無いものを構文木で数える。

規則の定義（何を兆候と数えるか）は `rules.json` の `smells` と、同じディレクトリの `README.md` の表にある。どの規則も 1 つの関数が
`(行, 規則)` を返し、`scan` がそれらをまとめる。ruff で数える規則（`by` が `ruff`）は `lint` が数える（決定 36）。
境界の印による許しは `marks` が当てる。
"""

from __future__ import annotations

import ast
from typing import Any

LOOPS = (ast.For, ast.AsyncFor, ast.While)
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
Hit = tuple[int, str]


def dotted(node: ast.AST) -> str:
    """`a.b.c` の名前。名前と属性だけでできていなければ空の文字列。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = dotted(node.value)
        return f"{head}.{node.attr}" if head else ""
    return ""


def _is_external(call: ast.Call, calls: frozenset[str], modules: frozenset[str]) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in calls
    name = dotted(func)
    return bool(name) and name.split(".")[0] in modules


def _loop_parts(node: ast.AST) -> tuple[list[ast.AST], list[ast.AST]]:
    """ループの、1 回だけ評価する部分と、回ごとに評価する部分。"""
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return [node.target, node.iter], [*node.body, *node.orelse]
    if isinstance(node, ast.While):
        return [], [node.test, *node.body, *node.orelse]
    gens = node.generators
    once = [gens[0].iter]
    rest = [gens[0].target, *gens[0].ifs, *[p for g in gens[1:] for p in (g.target, g.iter, *g.ifs)]]
    elts = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
    return once, rest + elts


def loop_calls(tree: ast.Module, calls: frozenset[str], modules: frozenset[str]) -> list[int]:
    """ループの中の外部呼び出し（NDF201）を、呼び出しを直に含む最も内側のループの行で返す。"""
    found: set[int] = set()
    stack: list[tuple[ast.AST, int | None]] = [(tree, None)]
    while stack:
        node, loop = stack.pop()
        if isinstance(node, ast.Call) and loop is not None and _is_external(node, calls, modules):
            found.add(loop)
        if isinstance(node, (*LOOPS, *COMPREHENSIONS)):
            once, each = _loop_parts(node)
            stack += [(c, loop) for c in once] + [(c, node.lineno) for c in each]
            continue
        inner = None if isinstance(node, SCOPES) else loop
        stack += [(c, inner) for c in ast.iter_child_nodes(node)]
    return sorted(found)


def elif_chains(tree: ast.Module, min_elif: int) -> list[int]:
    """`elif` が `min_elif` 本以上続く連鎖（NDF301）を、連鎖の先頭の `if` の行で返す。"""
    inner: set[int] = set()
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or id(node) in inner:
            continue
        count, cur = 0, node
        while len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
            cur = cur.orelse[0]
            inner.add(id(cur))
            count += 1
        if count >= min_elif:
            found.append(node.lineno)
    return sorted(found)


def caught_types(handler: ast.ExceptHandler) -> tuple[str, ...]:
    """`except` が受ける型の名前（属性の最後の名前）。素の `except` は `("BaseException",)`。"""
    if handler.type is None:
        return ("BaseException",)
    elts = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return tuple(dotted(e).rsplit(".", 1)[-1] for e in elts)


def _quiet(body: list[ast.stmt]) -> bool:
    def const_return(s: ast.stmt) -> bool:
        return isinstance(s, ast.Return) and (s.value is None or isinstance(s.value, ast.Constant))

    return any(const_return(s) for s in body) and all(isinstance(s, (ast.Pass, ast.Continue)) or const_return(s) for s in body)


def handlers(tree: ast.Module) -> tuple[list[Hit], dict[int, tuple[str, ...]]]:
    """`except` の兆候（NDF401）と、`except` の行ごとの受ける型を返す。"""
    hits: list[Hit] = []
    caught: dict[int, tuple[str, ...]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            caught[node.lineno] = caught_types(node)
            if _quiet(node.body):
                hits.append((node.lineno, "NDF401"))
    return hits, caught


def _empty(node: ast.AST) -> bool:
    if isinstance(node, ast.Dict):
        return not node.keys
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return not node.elts
    return isinstance(node, ast.Constant) and not isinstance(node.value, bool) and node.value in ("", 0, None)


def calls_and_fallbacks(tree: ast.Module, boundary_receivers: frozenset[str]) -> list[Hit]:
    """`contextlib.suppress`（NDF402）・空の既定への `or`（NDF501）・既定値つきの `.get`（NDF502）を返す。"""
    hits: list[Hit] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and _empty(node.values[-1]):
            hits.append((node.lineno, "NDF501"))
        if not isinstance(node, ast.Call):
            continue
        name = dotted(node.func)
        if name in ("suppress", "contextlib.suppress"):
            hits.append((node.lineno, "NDF402"))
        is_get = isinstance(node.func, ast.Attribute) and node.func.attr == "get"
        if is_get and len(node.args) == 2 and not node.keywords and dotted(node.func.value) not in boundary_receivers:
            hits.append((node.lineno, "NDF502"))
    return hits


def long_functions(tree: ast.Module, max_lines: int) -> list[int]:
    """`def` の行から最後の行までが `max_lines` を超える関数（NDF701）の `def` の行。"""
    return [n.lineno for n in ast.walk(tree) if isinstance(n, FUNCTIONS) and n.end_lineno - n.lineno + 1 > max_lines]


def scan(tree: ast.Module, smells: dict[str, Any]) -> tuple[list[Hit], dict[int, tuple[str, ...]]]:
    """1 ファイルのすべての兆候と、`except` の行ごとの受ける型を、同じ構文木から集める。"""
    p201 = smells["NDF201"]["params"]
    calls = loop_calls(tree, frozenset(p201["calls"]), frozenset(p201["modules"]))
    hits = [(line, "NDF201") for line in calls]
    hits += [(line, "NDF301") for line in elif_chains(tree, smells["NDF301"]["params"]["min_elif"])]
    handler_hits, caught = handlers(tree)
    hits += handler_hits
    hits += calls_and_fallbacks(tree, frozenset(smells["NDF502"]["params"]["boundary_receivers"]))
    hits += [(line, "NDF701") for line in long_functions(tree, smells["NDF701"]["params"]["max_lines"])]
    return sorted(set(hits)), caught
