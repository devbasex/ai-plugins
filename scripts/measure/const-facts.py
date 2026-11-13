#!/usr/bin/env python3
"""#1142 の「定数からデータへ」: 同じ事実を 2 か所以上の最上位の定数に持つ箇所を構文木で拾う。

移行の前（基準）と後を同じコマンドで打つ。見るのは `plugins/ndf` の下の `.py` と `.sh` のうち、テストと
`experimental/` を除くもの（定数）と、テストのファイル（一致を縛る assert と、定数の値を写した字）。

    python3 scripts/measure/const-facts.py [plugins/ndf] [--json] [--limit N]

拾う組の種類（`kind`）:

- `same-set`: 文字列の集まり（tuple・list・set・frozenset・dict の鍵・シェルの配列と表の 1 列目）が、
  2 つ以上のファイルの定数で同じ集合になる。並びまで同じなら `ordered` が真
- `subset`: 2 つ以上の文字列を持つ集まりが、別のファイルの定数の集まりに含まれる（`same-set` を除く）
- `affix`: 集まりの 6 字以上の 2 つ以上の要素が、別のファイルの定数の集まりの要素の接頭辞か接尾辞になる
  （例: `-design.md` と `issues/*-design.md`）
- `same-str`: 4 字以上の同じ文字列を、2 つ以上のファイルの定数が丸ごと持つ
- `member`: 4 字以上の文字列の定数が、別のファイルの定数の集まりの要素になる
- `same-num`: 同じ名前で同じ数値の定数が 2 つ以上のファイルにある（閾値の重複）
- `test-bind`: テストの assert が、別々のモジュールの 2 つの定数の一致を縛る
- `test-parse`: テストが定数の定義の字（`NAME=…`）を正規表現で読む（シェルの表を写して縛る）
- `test-literal`: テストの字の集まり（3 つ以上の文字列）が、定数の集まりと同じ集合になる（値を写して縛る）

結果は既定で人が読む一覧、`--json` で 1 行 JSON（`{"tool", "status", "summary", "items", "metrics"}`）。
"""

import argparse
import ast
import collections
import itertools
import json
import pathlib
import re

CONST = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
SH_ASSIGN = re.compile(r"^(?:readonly |declare -[a-zA-Z]+ )?([A-Z_][A-Z0-9_]*)=(.*)$")
MIN_STR = 4
AFFIX_MIN = 6
SRC_PARSE = re.compile(r"^\^?([A-Z][A-Z0-9_]{2,})=")


def is_test(p):
    return any(x in ("tests", "test") for x in p.parts) or p.name.startswith("test_") or p.name == "conftest.py"


def lit(node):
    """定数の値を Python の値で返す。字・数・字の集まり・字を鍵に持つ dict だけを読み、ほかは None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        vals = [lit(e) for e in node.elts]
        return tuple(vals) if vals and all(isinstance(v, str) for v in vals) else None
    if isinstance(node, ast.Dict):
        keys = [lit(k) if k is not None else None for k in node.keys]
        return {"keys": tuple(keys)} if keys and all(isinstance(k, str) for k in keys) else None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("frozenset", "set", "tuple", "sorted", "list"):
        if len(node.args) == 1 and not node.keywords:
            return lit(node.args[0])
    return None


def py_consts(path, rel):
    try:
        tree = ast.parse(path.read_text(errors="ignore"))
    except SyntaxError:
        return []
    out = []
    for n in tree.body:
        if isinstance(n, ast.Assign):
            targets, value = n.targets, n.value
        elif isinstance(n, ast.AnnAssign) and n.value is not None:
            targets, value = [n.target], n.value
        else:
            continue
        v = lit(value)
        if v is None:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and CONST.match(t.id):
                out.append(const(rel, t.id, v, n.lineno))
    return out


def const(rel, name, v, line):
    if isinstance(v, dict):
        return {"file": rel, "name": name, "line": line, "seq": v["keys"], "str": None, "num": None}
    if isinstance(v, tuple):
        return {"file": rel, "name": name, "line": line, "seq": v, "str": None, "num": None}
    if isinstance(v, str):
        return {"file": rel, "name": name, "line": line, "seq": None, "str": v, "num": None}
    return {"file": rel, "name": name, "line": line, "seq": None, "str": None, "num": v}


def sh_value(raw, lines, i):
    """シェルの右辺を読む。複数行の引用は閉じるまで読み進める。戻り値は (値, 次の行)。"""
    if raw.startswith("("):
        buf = raw
        while ")" not in buf and i < len(lines):
            buf += " " + lines[i]
            i += 1
        body = buf[1 : buf.index(")")]
        words = re.findall(r"\"([^\"]*)\"|'([^']*)'|(\S+)", body)
        vals = [a or b or c for a, b, c in words]
        if not vals or any("$" in w for w in vals):
            return None, i
        return tuple(vals), i
    m = re.match(r"^(\$?)(['\"])", raw)
    if not m:
        return None, i
    quote, ansi = m.group(2), m.group(1) == "$"
    body = raw[len(m.group(0)) :]
    while not re.search(rf"(?<!\\){quote}\s*(#.*)?$", body) and i < len(lines):
        body += "\n" + lines[i]
        i += 1
    body = re.sub(rf"(?<!\\){quote}\s*(#.*)?$", "", body)
    if quote == '"' and "$" in body:
        return None, i
    if ansi:
        body = body.replace("\\t", "\t").replace("\\n", "\n")
    rows = [r for r in body.split("\n") if r.strip()]
    if len(rows) > 1:
        return tuple(r.split("\t")[0] for r in rows), i
    if "\t" in body:
        return tuple(body.split("\t")), i
    return body, i


def sh_consts(path, rel):
    lines = path.read_text(errors="ignore").splitlines()
    out, i = [], 0
    while i < len(lines):
        m = SH_ASSIGN.match(lines[i])
        line = i + 1
        i += 1
        if not m:
            continue
        v, i = sh_value(m.group(2), lines, i)
        if isinstance(v, str) and re.fullmatch(r"-?\d+", v):
            v = int(v)
        if v is not None and v != "":
            out.append(const(rel, m.group(1), v, line))
    return out


def names_in(node):
    """式の中の、モジュールの属性として読んだ定数の名前（「モジュール.名前」）を返す。"""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and CONST.match(n.attr):
            base = n.value.id if isinstance(n.value, ast.Name) else ast.unparse(n.value)
            out.add(f"{base}.{n.attr}")
    return out


def bound(left, right):
    """両辺が別のモジュールの定数を読むなら、縛る名前の組を返す。"""
    lb, rb = {x.rsplit(".", 1)[0] for x in left}, {x.rsplit(".", 1)[0] for x in right}
    return sorted(left | right) if left and right and lb.isdisjoint(rb) else None


def test_scan(path, rel, by_set, const_names):
    try:
        tree = ast.parse(path.read_text(errors="ignore"))
    except SyntaxError:
        return []
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assert) and isinstance(n.test, ast.Compare) and len(n.test.comparators) == 1:
            names = bound(names_in(n.test.left), names_in(n.test.comparators[0]))
            if names:
                out.append({"kind": "test-bind", "file": rel, "line": n.lineno, "names": names})
        elif (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in ("assertEqual", "assertCountEqual")
            and len(n.args) >= 2
        ):
            names = bound(names_in(n.args[0]), names_in(n.args[1]))
            if names:
                out.append({"kind": "test-bind", "file": rel, "line": n.lineno, "names": names})
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            m = SRC_PARSE.search(n.value)
            if m and m.group(1) in const_names:
                out.append({"kind": "test-parse", "file": rel, "line": n.lineno, "names": [m.group(1)]})
        if isinstance(n, (ast.Tuple, ast.List, ast.Set)):
            v = lit(n)
            if isinstance(v, tuple) and len(set(v)) >= 3 and frozenset(v) in by_set:
                hits = by_set[frozenset(v)]
                out.append(
                    {"kind": "test-literal", "file": rel, "line": n.lineno, "names": sorted({f"{c['file']}:{c['name']}" for c in hits})}
                )
    return out


def ref(c):
    return f"{c['file']}:{c['line']}:{c['name']}"


def scan(root):
    files = sorted(p for p in root.rglob("*") if p.suffix in (".py", ".sh") and p.is_file() and ".worktrees" not in p.parts)
    consts, tests = [], []
    for p in files:
        rel = str(p.relative_to(root))
        if is_test(p):
            if p.suffix == ".py":
                tests.append((p, rel))
            continue
        if "experimental" in p.parts:
            continue
        consts.extend(py_consts(p, rel) if p.suffix == ".py" else sh_consts(p, rel))
    return consts, tests


def groups(consts):
    items = []
    seqs = [c for c in consts if c["seq"] and len(set(c["seq"])) >= 2]
    by_set = collections.defaultdict(list)
    for c in seqs:
        by_set[frozenset(c["seq"])].append(c)
    for s, cs in by_set.items():
        if len({c["file"] for c in cs}) >= 2:
            ordered = len({c["seq"] for c in cs}) == 1
            items.append({"kind": "same-set", "ordered": ordered, "values": sorted(s), "at": [ref(c) for c in cs]})
    for a, b in itertools.permutations(seqs, 2):
        sa, sb = frozenset(a["seq"]), frozenset(b["seq"])
        if a["file"] == b["file"] or sa == sb:
            continue
        if sa < sb:
            items.append({"kind": "subset", "values": sorted(sa), "at": [ref(a), ref(b)]})
            continue
        hit = {x for x in sa if len(x) >= AFFIX_MIN and any(x != y and (y.endswith(x) or y.startswith(x)) and y != x + "s" for y in sb)}
        if len(hit) >= 2 and not (sa & sb):
            items.append({"kind": "affix", "values": sorted(hit), "at": [ref(a), ref(b)]})
    strs = [c for c in consts if c["str"] and len(c["str"]) >= MIN_STR]
    by_str = collections.defaultdict(list)
    for c in strs:
        by_str[c["str"]].append(c)
    for s, cs in by_str.items():
        if len({c["file"] for c in cs}) >= 2:
            items.append({"kind": "same-str", "values": [s], "at": [ref(c) for c in cs]})
    elem = collections.defaultdict(list)
    for c in seqs:
        for x in set(c["seq"]):
            elem[x].append(c)
    for s, cs in by_str.items():
        for c in cs:
            others = [o for o in elem.get(s, []) if o["file"] != c["file"]]
            if others:
                items.append({"kind": "member", "values": [s], "at": [ref(c)] + [ref(o) for o in others]})
    by_num = collections.defaultdict(list)
    for c in consts:
        if c["num"] is not None:
            by_num[(c["name"], c["num"])].append(c)
    for (name, v), cs in by_num.items():
        if len({c["file"] for c in cs}) >= 2:
            items.append({"kind": "same-num", "values": [f"{name}={v}"], "at": [ref(c) for c in cs]})
    return items, by_set


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("root", nargs="?", default="plugins/ndf")
    ap.add_argument("--json", action="store_true", help="1 行 JSON で出す")
    ap.add_argument("--limit", type=int, default=0, help="種類ごとに出す組の数の上限（0 は全部）")
    args = ap.parse_args()
    root = pathlib.Path(args.root)
    consts, tests = scan(root)
    items, by_set = groups(consts)
    for p, rel in tests:
        items.extend(test_scan(p, rel, by_set, {c["name"] for c in consts}))
    counts = collections.Counter(i["kind"] for i in items)
    involved = {a.rsplit(":", 2)[0] + ":" + a.rsplit(":", 1)[1] for i in items if not i["kind"].startswith("test") for a in i["at"]}
    metrics = {
        "constants": len(consts),
        "constants_in_groups": len(involved),
        "groups": sum(v for k, v in counts.items() if not k.startswith("test")),
        **{k.replace("-", "_"): v for k, v in sorted(counts.items())},
    }
    summary = (
        f"定数 {len(consts)} 個のうち {len(involved)} 個が組に入る。組 {metrics['groups']}（"
        + "・".join(f"{k} {v}" for k, v in sorted(counts.items()))
        + "）"
    )
    if args.json:
        print(
            json.dumps({"tool": "const-facts", "status": "ok", "summary": summary, "items": items, "metrics": metrics}, ensure_ascii=False)
        )
        return
    print(summary)
    for kind in ("same-set", "subset", "affix", "same-str", "member", "same-num", "test-bind", "test-parse", "test-literal"):
        rows = [i for i in items if i["kind"] == kind]
        print(f"{kind}: {len(rows)}")
        for i in rows[: args.limit or None]:
            where = i.get("at") or [f"{i['file']}:{i['line']}"]
            vals = i.get("values") or i.get("names")
            print(f"  {' / '.join(where)}  {vals if len(str(vals)) < 160 else str(vals)[:157] + '...'}")


if __name__ == "__main__":
    main()
