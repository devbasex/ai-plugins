"""事実の重複の規則: 同じ事実を 2 つ以上のファイルの最上位の定数に持たない（`const-fact`）。事実の表の形（`fact-table`）。

事実の表（`contracts/facts/*.json`）がある事実は、表から読む。表の形は表と同じ置き場の `fact.schema.json`（I-DT2・DC1）と列の宣言から作る行の形を
`jsonschema`（draft 2020-12）で確かめる（設計の決定 46・47。実行時の読み手は形を検査しない）。表の `id` を定数へ写したものも重複に数える。
"""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ndf.checks.model import CheckError, Finding, Scope
from ndf.contracts.facts import SCHEMA_FILE

CONST = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
SET_CALLS = frozenset({"set", "frozenset", "tuple", "list"})


@dataclass(frozen=True)
class Const:
    rel: str
    line: int
    name: str
    value: object

    @property
    def place(self) -> str:
        return f"{self.rel}:{self.line} {self.name}"


def _strings(elts: list[ast.expr]) -> frozenset[str] | None:
    vals = [e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return frozenset(vals) if len(vals) == len(elts) and len(vals) >= 2 else None


def lit(node: ast.expr) -> object:
    """定数の右辺を、文字列・数・文字列の集まり（2 つ以上）として読む。読めない形は `None`。"""
    if isinstance(node, ast.Constant) and not isinstance(node.value, bool):
        return node.value if isinstance(node.value, (str, int, float)) else None
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return _strings(node.elts)
    if isinstance(node, ast.Dict):
        return _strings([k for k in node.keys if k is not None]) if None not in node.keys else None
    is_set_call = isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in SET_CALLS
    return lit(node.args[0]) if is_set_call and len(node.args) == 1 else None


def consts(tree: ast.Module, rel: str) -> list[Const]:
    """モジュールの最上位の大文字の定数のうち、`lit` が読めたもの。"""
    found = []
    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign) and node.value is not None
            else []
        )
        names = [t.id for t in targets if isinstance(t, ast.Name) and CONST.match(t.id)]
        value = lit(node.value) if names else None
        found += [Const(rel, node.lineno, n, value) for n in names if value is not None]
    return found


def _spread(group: list[Const]) -> bool:
    return len({c.rel for c in group}) >= 2


def _report(kind: str, group: list[Const], what: str) -> Finding:
    first, *rest = sorted(group, key=lambda c: (c.rel, c.line))
    others = "・".join(c.place for c in rest)
    return Finding("const-fact", first.rel, first.line, f"{kind}: {what}（{first.name} と {others}）")


def _by_value(items: list[Const], key) -> dict[object, list[Const]]:
    groups: dict[object, list[Const]] = defaultdict(list)
    for c in items:
        groups[key(c)].append(c)
    return groups


def groups(all_consts: list[Const], min_str: int) -> list[Finding]:
    """`same-str`・`same-set`・`same-num`・`subset`・`member` の組を返す。どの組も 2 つ以上のファイルにまたがる。"""
    strs = [c for c in all_consts if isinstance(c.value, str) and len(c.value) >= min_str]
    sets = [c for c in all_consts if isinstance(c.value, frozenset)]
    nums = [c for c in all_consts if isinstance(c.value, (int, float))]
    found = [_report("same-str", g, repr(v)) for v, g in _by_value(strs, lambda c: c.value).items() if _spread(g)]
    found += [_report("same-set", g, "同じ集まり") for g in _by_value(sets, lambda c: c.value).values() if _spread(g)]
    found += [_report("same-num", g, f"同じ名前と数 {k[1]}") for k, g in _by_value(nums, lambda c: (c.name, c.value)).items() if _spread(g)]
    found += [
        _report("subset", [a, b], "集まりが別のファイルの集まりに含まれる")
        for a in sets
        for b in sets
        if a.rel != b.rel and a.value < b.value
    ]
    found += [
        _report("member", [s, b], f"{s.value!r} が別のファイルの集まりの要素")
        for s in strs
        for b in sets
        if s.rel != b.rel and s.value in b.value
    ]
    return found


@cache
def table_validator(schema_path: Path) -> Draft202012Validator:
    """表の共通の形（表と同じ置き場の `fact.schema.json`）の検証器。1 プロセスで置き場ごとに 1 回だけ読む。

    無い・読めない・スキーマとして誤りなら `CheckError`（表の形を確かめられないまま通さない）。
    """
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, ValueError, SchemaError) as exc:
        raise CheckError(f"表の共通の形を読めない: {schema_path}: {exc}") from exc
    return Draft202012Validator(schema)


def _column_schema(decl: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}} if decl["type"] == "array" else {"type": decl["type"]}


def row_schema(columns: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """列の宣言から行の形を作る。宣言に無い列を持たず、`optional` でない列を欠かず、列の型に合う。"""
    props = {name: _column_schema(decl) for name, decl in columns.items()}
    props["id"] = {"type": "string"}
    required = ["id", *(name for name, decl in columns.items() if name != "id" and decl.get("optional") is not True)]
    return {"type": "object", "required": required, "properties": props, "additionalProperties": False}


def shape_errors(data: object, common: Draft202012Validator) -> list[str]:
    """表の形の違反。共通の形に合ってから、列の宣言から作った行の形で各行を確かめる。"""
    errors = sorted(common.iter_errors(data), key=lambda e: (e.json_path, e.message))
    if not errors:
        rows = Draft202012Validator({"properties": {"rows": {"items": row_schema(data["columns"])}}})
        errors = sorted(rows.iter_errors(data), key=lambda e: (e.json_path, e.message))
    return [f"{e.json_path}: {e.message}" for e in errors]


def _table_rows(path: Path, rel: str, common: Draft202012Validator) -> tuple[list[str], list[Finding]]:
    """事実の表 1 本の `id` の並びと、形の違反。形が違えば `id` を返さない。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], [Finding("fact-table", rel, 1, f"表を読めない: {exc}")]
    bad = [Finding("fact-table", rel, 1, f"表の形: {m}") for m in shape_errors(data, common)]
    if bad:
        return [], bad
    ids = [r["id"] for r in data["rows"]]
    dups = sorted({i for i in ids if ids.count(i) > 1})
    return ids, [Finding("fact-table", rel, 1, f"`id` の重なり: {d}") for d in dups]


def tables(scope: Scope, tables_dir: str) -> tuple[dict[str, list[str]], list[Finding]]:
    """事実の表を読み、表の名前 → `id` の並びと、形の違反を返す。表が無ければ空。"""
    base = scope.plugin_root / tables_dir
    paths = [p for p in sorted(base.glob("*.json")) if not p.name.endswith(".schema.json")] if base.is_dir() else []
    found: list[Finding] = []
    ids: dict[str, list[str]] = {}
    if not paths:
        return ids, found
    common = table_validator(base / SCHEMA_FILE)
    for path in paths:
        ids[path.stem], bad = _table_rows(path, scope.rel(path), common)
        found += bad
    return ids, found


def table_hits(all_consts: list[Const], table_ids: dict[str, list[str]], min_str: int) -> list[Finding]:
    """表の `id` を 2 つ以上含む集まり（`table-set`）と、表の `id` に等しい文字列（`table-str`）。"""
    found = []
    for name, ids in table_ids.items():
        idset = set(ids)
        for c in all_consts:
            hit = (
                len(c.value & idset) >= 2
                if isinstance(c.value, frozenset)
                else (isinstance(c.value, str) and len(c.value) >= min_str and c.value in idset)
            )
            if hit:
                kind = "table-set" if isinstance(c.value, frozenset) else "table-str"
                found.append(Finding("const-fact", c.rel, c.line, f"{kind}: {c.name} が表 {name} の id を写す"))
    return found


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    conf = rules["facts"]
    all_consts = [c for src in scope.code() for c in consts(src.tree, src.rel)]
    table_ids, found = tables(scope, conf["tables_dir"])
    return found + groups(all_consts, conf["min_str"]) + table_hits(all_consts, table_ids, conf["min_str"])
