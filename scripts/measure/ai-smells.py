#!/usr/bin/env python3
"""#1142 の基準: AI が陥りやすい作りの 7 種類の兆候を、テストと試行を除く NDF の Python から構文木で数える。

要求の「AI が陥りやすい作り」の表（2026-11-13、develop b14c8be6a）の数え方のまま変えない。作り直しの前と後を
同じコマンドで打ち、種類ごとの数と箇所の一覧を JSON で出す。数は兆候の候補で、すべてが直す対象ではない。

    python3 scripts/measure/ai-smells.py [plugins/ndf] [--summary]

`--summary` は箇所の一覧を省き、数だけを出す。見る範囲は `<root>` の下の `*.py` のうち、パスに `/tests/` と
`/experimental/` を含まないもの（git の追跡に依らない）。
"""

import argparse
import ast
import collections
import json
import pathlib
import subprocess

# ループの中で数える外部の呼び出し（関数名か属性名）
CALL_IO = {"run", "check_output", "check_call", "Popen", "gh", "gh_json", "git", "_git", "gh_api", "call_gh"}
LOOPS = (ast.For, ast.While, ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)
NESTS = (ast.If, ast.For, ast.While, ast.Try, ast.With)
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
# 同じ名前の関数の数から外す名前（エントリポイントの決まった形）
COMMON_NAMES = ("main", "build_parser", "cmd")


def callee(node):
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def depth(node, d=0):
    """入れ子の深さ。内側の関数の定義へは入らない。"""
    m = d
    for ch in ast.iter_child_nodes(node):
        if isinstance(ch, FUNCS):
            continue
        m = max(m, depth(ch, d + 1 if isinstance(ch, NESTS) else d))
    return m


def elif_len(node):
    """`if` から続く `elif` の本数。連鎖の途中の `if` からも数える（基準の数え方のまま）。"""
    n = 0
    while isinstance(node, ast.If) and len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
        n += 1
        node = node.orelse[0]
    return n


def is_swallow(handler):
    def quiet(b):
        if isinstance(b, (ast.Pass, ast.Continue)):
            return True
        return isinstance(b, ast.Return) and (b.value is None or isinstance(b.value, ast.Constant))

    return all(quiet(b) for b in handler.body)


def is_broad(handler):
    t = handler.type
    return t is None or (isinstance(t, ast.Name) and t.id in ("Exception", "BaseException"))


def is_empty_fallback(node):
    last = node.values[-1]
    if isinstance(last, ast.Dict):
        return not last.keys
    if isinstance(last, ast.List):
        return not last.elts
    return isinstance(last, ast.Constant) and last.value in ("", 0, None)


class Tally:
    def __init__(self):
        self.count = collections.Counter()
        self.where = collections.defaultdict(list)
        self.const_values = collections.defaultdict(set)
        self.str_literals = collections.defaultdict(set)
        self.func_names = collections.defaultdict(set)
        self.files = self.lines = self.functions = 0

    def hit(self, key, loc):
        self.count[key] += 1
        self.where[key].append(loc)

    def constants(self, rel, tree):
        for st in tree.body:
            if not isinstance(st, (ast.Assign, ast.AnnAssign)):
                continue
            for t in st.targets if isinstance(st, ast.Assign) else [st.target]:
                if isinstance(t, ast.Name) and t.id.isupper() and len(t.id) > 1:
                    self.count["module_constants"] += 1
                    self.constant_value(rel, t.id, st.value)

    def constant_value(self, rel, name, value):
        try:
            v = ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError):  # リテラルでない値は数えない
            return
        if isinstance(v, (tuple, list, frozenset, set)) and len(v) >= 2:
            self.const_values[json.dumps(sorted(map(str, v)), ensure_ascii=False)].add(f"{rel}:{name}")
        elif isinstance(v, str) and len(v) >= 6:
            self.const_values[json.dumps(v, ensure_ascii=False)].add(f"{rel}:{name}")

    def function(self, rel, node):
        self.functions += 1
        self.func_names[node.name].add(rel)
        loc = f"{rel}:{node.lineno}:{node.name}"
        if (node.end_lineno or node.lineno) - node.lineno + 1 > 60:
            self.hit("func_over_60_lines", loc)
        if len(node.args.args) + len(node.args.kwonlyargs) > 6:
            self.hit("func_over_6_params", loc)
        if depth(node) >= 4:
            self.hit("func_nesting_ge4", loc)
        for sub in ast.walk(node):
            if isinstance(sub, ast.If) and elif_len(sub) >= 4:
                self.hit("elif_chain_ge5_branches", f"{rel}:{sub.lineno}:{node.name}")
        if sum(isinstance(s, ast.If) for s in ast.walk(node)) >= 10:
            self.hit("func_ge10_ifs", loc)

    def handler(self, rel, node):
        self.count["except_handlers"] += 1
        loc = f"{rel}:{node.lineno}"
        broad = is_broad(node)
        if broad:
            self.count["except_broad"] += 1
        if is_swallow(node):
            self.hit("except_swallow", loc)
            if broad:
                self.hit("except_broad_swallow", loc)

    def loop(self, rel, node):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and callee(sub) in CALL_IO:
                self.hit("io_call_in_loop", f"{rel}:{sub.lineno}:{callee(sub)}")
                return

    def node(self, rel, node):
        if isinstance(node, FUNCS):
            self.function(rel, node)
        elif isinstance(node, ast.ExceptHandler):
            self.handler(rel, node)
        elif isinstance(node, LOOPS):
            self.loop(rel, node)
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and is_empty_fallback(node):
            self.hit("or_empty_fallback", f"{rel}:{node.lineno}")
        elif isinstance(node, ast.Call) and callee(node) == "get" and len(node.args) == 2:
            self.hit("dict_get_with_default", f"{rel}:{node.lineno}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if 8 <= len(node.value) <= 80 and "\n" not in node.value:
                self.str_literals[node.value].add(rel)

    def file(self, root, p):
        rel = str(p.relative_to(root))
        try:
            src = p.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (SyntaxError, UnicodeDecodeError):  # 読めないファイルは数えず件数だけ残す
            self.count["unparsable"] += 1
            return
        self.files += 1
        self.lines += src.count("\n")
        self.constants(rel, tree)
        for node in ast.walk(tree):
            self.node(rel, node)


def head(root):
    r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() or None


def report(t, summary):
    dup_consts = {k: sorted(v) for k, v in t.const_values.items() if len({x.split(":")[0] for x in v}) >= 2}
    dup_strings = {k: sorted(v) for k, v in t.str_literals.items() if len(v) >= 4}
    dup_funcs = {k: sorted(v) for k, v in t.func_names.items() if len(v) >= 2 and not k.startswith("__") and k not in COMMON_NAMES}
    c = t.count

    def item(label, key, where=None):
        out = {"what": label, "count": c[key] if where is None else len(where)}
        if not summary:
            out["locations"] = t.where[key] if where is None else where
        return out

    kinds = {
        "1_constant_duplication": [
            item("同じ値を 2 つ以上のファイルの最上位の定数に持つ値", "", dup_consts),
            {"what": "最上位の定数の総数", "count": c["module_constants"]},
        ],
        "2_io_call_in_loop": [item("ループ・内包表記の中の外部呼び出し", "io_call_in_loop")],
        "3_branching": [
            item("elif 5 本以上の連鎖", "elif_chain_ge5_branches"),
            item("入れ子の深さ 4 以上の関数", "func_nesting_ge4"),
            item("if 10 個以上の関数", "func_ge10_ifs"),
        ],
        "4_swallowed_exception": [
            item("本体が pass・定数の return・continue だけの except", "except_swallow"),
            item("うち Exception・BaseException・素の except", "except_broad_swallow"),
            {"what": "広い except の総数", "count": c["except_broad"]},
            {"what": "except の総数", "count": c["except_handlers"]},
        ],
        "5_fallback": [
            item("x or {} / [] / '' / 0 / None", "or_empty_fallback"),
            item("既定値つきの .get(k, 既定)", "dict_get_with_default"),
        ],
        "6_copy_duplication": [
            item("2 つ以上のファイルで同じ名前の関数", "", dup_funcs),
            item("4 つ以上のファイルに出る 8〜80 字の文字列", "", dup_strings),
        ],
        "7_long_function": [
            item("60 行を超える関数", "func_over_60_lines"),
            item("引数 7 個以上の関数", "func_over_6_params"),
        ],
    }
    return {"files": t.files, "lines": t.lines, "functions": t.functions, "unparsable": c["unparsable"], "kinds": kinds}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("root", nargs="?", default="plugins/ndf")
    ap.add_argument("--summary", action="store_true", help="箇所の一覧を省き、数だけを出す")
    args = ap.parse_args()
    root = pathlib.Path(args.root)
    t = Tally()
    for p in sorted(root.rglob("*.py")):
        if "/tests/" not in str(p) and "/experimental/" not in str(p):
            t.file(root, p)
    out = {"root": str(root), "commit": head(root), **report(t, args.summary)}
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
