"""構造チェックの `limit-literal` と `sleep-loop` の規則（#2343 の不変条件 I3・I6、決定 4・5）。

- `limit-literal`: 上限・再試行の回数・待ちの間隔を数字のリテラルで書く。値は上限の宣言
  （`plugins/ndf/contracts/limits.json`）にあり、`lib/limits.py` から方針の名前で引く。見るのは次の 4 つで、
  0（待たない・打ち直さないの意味）は落とさない
  1. 名前の規則（`NAME_WORDS` のどれかを含む大文字の名前。`EXIT_` で始まる名前と `_CODE` で終わる名前を除く）に
     当たる定数への数字の代入
  2. 呼び出しのキーワード引数 `KEYS` への数字
  3. 同じ名前の引数の既定値の数字
  4. `.get("<KEYS のキー>", <数字>)` の 2 つ目の引数
- `sleep-loop`: `lib/waits.py` の外で、本体（入れ子の関数・クラス・`lambda` を除く）が名前の末尾を `sleep` とする
  関数を呼ぶ `for` / `while` のループ。再試行と待ちを見分けず、待ちのループは例外リストに理由つきで載せる

どちらもテストと実験版（`plugins/ndf/scripts/experimental/`）を見ない。`limit-literal` は宣言の読み手
（`lib/limits.py`）も見ない。例外リストの `name` は、`limit-literal` が定数か引数の名前、`sleep-loop` がループを
持つ関数の名前（クラスの中は `<クラス>.<関数>`、最上位はモジュールの名前）。
"""

from __future__ import annotations

import ast
import operator
import re

LIB = "plugins/ndf/scripts/"
EXPERIMENTAL = LIB + "experimental/"
LIMITS_READER = LIB + "lib/limits.py"
WAITS = LIB + "lib/waits.py"
NAME_WORDS = ("TIMEOUT", "RETRY", "RETRIES", "WAIT", "INTERVAL", "BUDGET", "DEADLINE", "POLL", "STALL", "MARGIN", "GRACE", "ATTEMPTS")
KEYS = frozenset(
    (
        "timeout",
        "max_wait",
        "interval",
        "max_interval",
        "wait",
        "deadline",
        "retries",
        "attempts",
        "grace",
        "per_call",
        "poll",
        "stall_timeout",
    )
)
_CONST = re.compile(r"^[A-Z][A-Z0-9_]*$")
_OPS = {ast.Mult: operator.mul, ast.Add: operator.add, ast.Sub: operator.sub, ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv}


def applies(rel: str) -> bool:
    """2 つの規則が見るファイルか（Python で、実験版でない。テストは走査の側で外す）。"""
    return rel.endswith(".py") and not rel.startswith(EXPERIMENTAL)


def _number(node: ast.AST | None) -> float | None:
    """数字だけでできた式（`30`・`-1`・`60 * 60`）の値。数字の式でなければ None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _number(node.operand)
        return None if v is None else (-v if isinstance(node.op, ast.USub) else v)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = _number(node.left), _number(node.right)
        if a is None or b is None or (b == 0 and isinstance(node.op, (ast.Div, ast.FloorDiv))):
            return None
        return _OPS[type(node.op)](a, b)
    return None


def _nonzero(node: ast.AST | None) -> bool:
    v = _number(node)
    return v is not None and v != 0


def limit_name(name: str) -> bool:
    """名前の規則（1）に当たる定数の名前か。"""
    if not _CONST.match(name) or name.startswith("EXIT_") or name.endswith("_CODE"):
        return False
    return any(w in name for w in NAME_WORDS)


def _targets(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Assign):
        out = []
        for t in node.targets:
            out += [n.id for n in ast.walk(t) if isinstance(n, ast.Name)]
        return out
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def _assigned_numbers(node: ast.Assign | ast.AnnAssign) -> list[str]:
    """0 でない数字を代入された名前（`A, B = 1, 2` の組は要素ごとに対にする）。"""
    out = _targets(node) if _nonzero(node.value) else []
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for t in targets:
        if isinstance(t, (ast.Tuple, ast.List)) and isinstance(node.value, (ast.Tuple, ast.List)) and len(t.elts) == len(node.value.elts):
            out += [e.id for e, v in zip(t.elts, node.value.elts) if isinstance(e, ast.Name) and _nonzero(v)]
    return out


def _defaults(args: ast.arguments) -> list[tuple[str, ast.AST]]:
    pos = args.posonlyargs + args.args
    out = list(zip([a.arg for a in pos[len(pos) - len(args.defaults) :]], args.defaults))
    out += [(a.arg, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None]
    return out


def limit_literals(text: str) -> list[tuple[str, int, str]]:
    """`limit-literal` に当たる箇所を `(名前, 行, 形)` で返す（読めない Python は空）。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    found: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            found += [(n, node.lineno, "定数") for n in _assigned_numbers(node) if limit_name(n)]
        elif isinstance(node, ast.Call):
            found += [(k.arg, node.lineno, "キーワード引数") for k in node.keywords if k.arg in KEYS and _nonzero(k.value)]
            f = node.func
            if (
                isinstance(f, ast.Attribute)
                and f.attr == "get"
                and len(node.args) == 2
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in KEYS
                and _nonzero(node.args[1])
            ):
                found.append((node.args[0].value, node.lineno, ".get の既定値"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            found += [(name, node.lineno, "引数の既定値") for name, d in _defaults(node.args) if name in KEYS and _nonzero(d)]
    return found


_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_LOOPS = (ast.For, ast.AsyncFor, ast.While)


def _own_nodes(stmts: list[ast.AST]):
    """文の並びの中の節（入れ子の関数・クラス・lambda の中を除く）。"""
    stack = [s for s in stmts if not isinstance(s, _NESTED)]
    while stack:
        n = stack.pop()
        yield n
        stack += [c for c in ast.iter_child_nodes(n) if not isinstance(c, _NESTED)]


def _calls_sleep(loop: ast.AST) -> bool:
    for n in _own_nodes(loop.body + loop.orelse):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name.endswith("sleep"):
                return True
    return False


def sleep_loops(text: str, module: str) -> list[tuple[str, int]]:
    """`sleep-loop` に当たるループを `(持つ関数の名前, 行)` で返す。同じ関数の 2 つ目からは数えない。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    found: dict[str, int] = {}

    def visit(body: list[ast.AST], owner: str, prefix: str) -> None:
        for n in _own_nodes(body):
            if isinstance(n, _LOOPS) and _calls_sleep(n):
                found.setdefault(owner, n.lineno)
        for child in _direct_defs(body):
            if isinstance(child, ast.ClassDef):
                visit(child.body, owner, prefix + child.name + ".")
            else:
                visit(child.body, prefix + child.name, prefix + child.name + ".")

    visit(tree.body, module, "")
    return sorted(found.items(), key=lambda kv: kv[1])


def _direct_defs(body: list[ast.AST]):
    """文の並びの中で、入れ子の関数・クラスの外にある関数とクラスの定義（`if` や `try` の中を含む）。"""
    stack = list(body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield n
        elif not isinstance(n, ast.Lambda):
            stack += list(ast.iter_child_nodes(n))


def violations(rel: str, text: str) -> list[dict]:
    """1 ファイルの `limit-literal` と `sleep-loop` の違反（構造チェックの違反の形）。"""
    if not applies(rel):
        return []
    out: list[dict] = []
    if rel != LIMITS_READER:
        seen: dict[str, list[str]] = {}
        for name, line, how in limit_literals(text):
            seen.setdefault(name, []).append(f"{line} 行（{how}）")
        for name, where in sorted(seen.items()):
            out.append(
                {
                    "kind": "limit-literal",
                    "path": rel,
                    "function": name,
                    "detail": "上限の数字を直に書く: " + "・".join(where) + "。上限の宣言の方針を lib/limits.py で引く",
                }
            )
    if rel != WAITS:
        module = rel.rsplit("/", 1)[-1].removesuffix(".py")
        for owner, line in sleep_loops(text, module):
            out.append(
                {
                    "kind": "sleep-loop",
                    "path": rel,
                    "function": owner,
                    "detail": f"{line} 行のループが眠る。再試行と待ちは lib/waits.py を通す",
                }
            )
    return out
