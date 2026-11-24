"""規則 `internal-names`: 内部データの名前が、名前の対応表（`contracts/data/internal-names.json`）のその種類に使ってよい名前か。

見るのは、種類のスキーマ（`contracts/schemas/internal/<種類>.schema.json`）の欄と、種類つきで記録を書く関数（`rules.json` の
`internal_names.writers` を呼ぶ関数）の中の辞書のキーである。名前の対応表が持つ名前（使う名前か消した別名）と
時刻の欄だけを確かめ、種類に固有のほかの欄は見ない（書き手の欄はスキーマが閉じて持つ）。
"""

from __future__ import annotations

import ast
import json
from collections import defaultdict
from collections.abc import Iterator
from typing import Any

from ndf import contracts
from ndf.checks.model import CheckError, Finding, Scope, Source
from ndf.checks.spec_model import SCHEMA_SUFFIX
from ndf.contracts import internal

RULE = "internal-names"
SCHEMA_PREFIX = "contracts/schemas/internal/"
# 時刻の欄の形（共通の欄のスキーマの定義）
TIME_REF = "#/$defs/time"


def allowed() -> dict[str, frozenset[str]]:
    """種類ごとに、使ってよい名前の集まり。名前の対応表を読めなければ `ContractError`。"""
    out: dict[str, set[str]] = defaultdict(set)
    for row in internal.names().values():
        for kind in row["where"]:
            out[kind].add(row["key"])
    return {kind: frozenset(keys) for kind, keys in out.items()}


def known() -> frozenset[str]:
    """名前の対応表が持つ名前（使う名前と消した別名）。"""
    return frozenset(name for row in internal.names().values() for name in (row["key"], *row["aliases_removed"]))


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    """内部データのスキーマと書き手が、その種類に使えない名前を使っている箇所を指摘にする。"""
    try:
        by_kind, names = allowed(), known()
    except contracts.ContractError as exc:
        raise CheckError(str(exc)) from exc
    found = []
    for src in scope.sources:
        rel = scope.plugin_rel(src)
        if rel.startswith(SCHEMA_PREFIX) and rel.endswith(SCHEMA_SUFFIX):
            kind = rel.removeprefix(SCHEMA_PREFIX).removesuffix(SCHEMA_SUFFIX)
            found += [_finding(src, line, name, {kind}) for name, line in _schema_names(src, names) if name not in _of(by_kind, {kind})]
    for src in scope.code():
        found += _writer_findings(src, by_kind, names, rules["internal_names"]["writers"])
    return found


def _writer_findings(
    src: Source, by_kind: dict[str, frozenset[str]], names: frozenset[str], writers: dict[str, int | None]
) -> list[Finding]:
    """記録を書く関数の中の辞書のキーのうち、名前の対応表が持つ名前で、その種類に使えないもの。"""
    found = []
    for function in _functions(src.tree):
        kinds = _kinds(function, writers)
        used = _dict_keys(function) if kinds else []
        found += [_finding(src, line, name, kinds) for name, line in used if name in names and name not in _of(by_kind, kinds)]
    return found


def _of(by_kind: dict[str, frozenset[str]], kinds: set[str]) -> frozenset[str]:
    """種類のどれかに使ってよい名前。"""
    return frozenset(name for k in kinds if k in by_kind for name in by_kind[k])


def _finding(src: Source, line: int, name: str, kinds: set[str]) -> Finding:
    detail = f"{'・'.join(sorted(kinds))} に使えない名前 {name}（名前の対応表 {internal.NAMES} の where に種類が無い）"
    return Finding(RULE, src.rel, line, detail)


def _schema_names(src: Source, names: frozenset[str]) -> list[tuple[str, int]]:
    """スキーマの欄のうち、名前の対応表が持つ名前と時刻の欄の、名前と行。"""
    try:
        schema = json.loads(src.text)
    except ValueError as exc:
        raise CheckError(f"内部データのスキーマを読めない: {src.rel}: {exc}") from exc
    lines = src.text.split("\n")
    out = []
    for name, spec in _properties(schema):
        if name in names or _is_time(spec):
            out.append((name, next((n for n, text in enumerate(lines, 1) if f'"{name}":' in text), 1)))
    return out


def _properties(node: Any) -> Iterator[tuple[str, Any]]:
    """スキーマの `properties` の欄（入れ子を含む）。"""
    if isinstance(node, list):
        for x in node:
            yield from _properties(x)
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if key == "properties" and isinstance(value, dict):
            yield from value.items()
            yield from _properties(list(value.values()))
        else:
            yield from _properties(value)


def _is_time(spec: Any) -> bool:
    return isinstance(spec, dict) and (spec.get("format") == "date-time" or str(spec.get("$ref")).endswith(TIME_REF))


def _functions(tree: ast.Module) -> list[ast.AST]:
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _kinds(function: ast.AST, writers: dict[str, int | None]) -> set[str]:
    """関数が書く内部データの種類: 書き込みの関数へ文字列で渡す種類。"""
    out: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or _name(node.func) not in writers:
            continue
        index = writers[_name(node.func)]
        given = [k.value for k in node.keywords if k.arg == "kind"]
        given += [node.args[index]] if index is not None and len(node.args) > index else []
        out |= {g.value for g in given if isinstance(g, ast.Constant) and isinstance(g.value, str)}
    return out


def _name(func: ast.AST) -> str:
    """呼び出しの名前の末尾（`jsonl.append` → `append`）。名前でなければ空。"""
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _dict_keys(function: ast.AST) -> list[tuple[str, int]]:
    """関数の中の、辞書の文字列のキーと、添字への代入の文字列のキー。"""
    out = []
    for node in ast.walk(function):
        keys: list[ast.expr | None] = []
        if isinstance(node, ast.Dict):
            keys = node.keys
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            keys = [node.slice]
        out += [(k.value, k.lineno) for k in keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    return out
