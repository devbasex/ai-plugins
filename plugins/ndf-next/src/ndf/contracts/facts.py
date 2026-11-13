"""事実の表（`contracts/facts/<名前>.json`）の読み手。

2 つ以上の読み手が使う事実（終了コードの区分・領域の語彙など）を表に 1 か所で持ち、コードは値を写さずに引く。
表の共通の形は `contracts/facts/fact.schema.json` にあり、行の形は表の `columns` の宣言から作って確かめる。
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from jsonschema import Draft202012Validator

from ndf import contracts

FACTS_DIR = "facts"
SCHEMA = f"{FACTS_DIR}/fact.schema.json"
NAME = re.compile(r"^[a-z][a-z0-9-]*$")
_JSON_TYPES = {"boolean": "boolean", "string": "string", "integer": "integer", "array": "array"}


class FactsError(contracts.ContractError):
    """表が無い・形が違う・引いた行や列が無い。"""


class Table(NamedTuple):
    name: str
    summary: str
    owner: str
    columns: dict[str, dict[str, Any]]
    rows: tuple[dict[str, Any], ...]


_cache: dict[str, Table] = {}
contracts.on_reset(_cache.clear)


def _row_schema(columns: dict[str, dict[str, Any]]) -> dict[str, Any]:
    props: dict[str, Any] = {"id": {"type": "string", "minLength": 1}}
    for col, decl in columns.items():
        kind = _JSON_TYPES[decl["type"]]
        props[col] = {"type": "array", "items": {"type": "string"}} if kind == "array" else {"type": kind}
    required = ["id", *(c for c, d in columns.items() if not d.get("optional"))]
    return {"type": "object", "additionalProperties": False, "required": required, "properties": props}


def _validate(raw: Any, name: str) -> Table:
    where = f"{FACTS_DIR}/{name}.json"
    errs = contracts.schema_errors(raw, SCHEMA)
    if errs:
        raise FactsError(f"事実の表 {where} の形が違う: {'; '.join(errs)}")
    validator = Draft202012Validator(_row_schema(raw["columns"]))
    for i, row in enumerate(raw["rows"]):
        bad = sorted(e.message for e in validator.iter_errors(row))
        if bad:
            raise FactsError(f"事実の表 {where} の行 {i}（{row.get('id')!r}）の形が違う: {'; '.join(bad)}")
    ids = [r["id"] for r in raw["rows"]]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise FactsError(f"事実の表 {where} に同じ id が 2 つある: {', '.join(dup)}")
    return Table(name, raw["summary"], raw["owner"], dict(raw["columns"]), tuple(raw["rows"]))


def names() -> tuple[str, ...]:
    """置き場にある表の名前（辞書順。共通の形のファイルは含まない）。"""
    found = (contracts.contracts_dir() / FACTS_DIR).glob("*.json")
    return tuple(sorted(p.stem for p in found if p.name != "fact.schema.json"))


def table(name: str) -> Table:
    """表 `facts/<name>.json` を 1 プロセスに 1 回読んで返す。"""
    if not NAME.match(name):
        raise FactsError(f"表の名前が違う形: {name!r}")
    if name not in _cache:
        try:
            raw = contracts.load_json(f"{FACTS_DIR}/{name}.json")
        except contracts.ContractError as exc:
            raise FactsError(str(exc)) from None
        _cache[name] = _validate(raw, name)
    return _cache[name]


def _require_columns(t: Table, cols) -> None:
    unknown = sorted(c for c in cols if c != "id" and c not in t.columns)
    if unknown:
        raise FactsError(f"事実の表 {t.name} に列 {', '.join(unknown)} が無い")


def rows(name: str, **where: Any) -> tuple[dict[str, Any], ...]:
    """条件（列 = 値）に合う行を表の並びで返す。列を持たない行は条件に合わない。"""
    t = table(name)
    _require_columns(t, where)
    return tuple(r for r in t.rows if all(c in r and r[c] == v for c, v in where.items()))


def ids(name: str, **where: Any) -> tuple[str, ...]:
    """条件に合う行の `id` を表の並びで返す。"""
    return tuple(r["id"] for r in rows(name, **where))


def row(name: str, id: str) -> dict[str, Any]:
    """`id` の行。無ければ `FactsError`。"""
    for r in table(name).rows:
        if r["id"] == id:
            return r
    raise FactsError(f"事実の表 {name} に行 {id!r} が無い")


def column(name: str, col: str) -> dict[str, Any]:
    """列 `col` を持つ行の `id` → 値（表の並び）。列を持たない行（任意の列）は含まない。"""
    t = table(name)
    _require_columns(t, [col])
    return {r["id"]: r[col] for r in t.rows if col in r}


def ref_errors(name: str, policy_names: set[str]) -> list[str]:
    """列の `ref`（`limits.json#<接頭辞>` か `facts/<表>`）の先に値があるかを確かめる。"""
    t = table(name)
    errs = []
    for col, decl in t.columns.items():
        ref = decl.get("ref")
        if not ref:
            continue
        values = [(r["id"], r[col]) for r in t.rows if col in r]
        if ref.startswith("limits.json#"):
            prefix = ref.split("#", 1)[1]
            errs += [
                f"{name}:{rid} の {col} {v!r} の方針 {prefix}{v} が上限の宣言に無い"
                for rid, v in values
                if f"{prefix}{v}" not in policy_names
            ]
        else:
            other = ref.split("/", 1)[1]
            known = set(ids(other))
            errs += [f"{name}:{rid} の {col} {v!r} が表 {other} に無い" for rid, v in values if v not in known]
    return errs


def parse_where(args: list[str]) -> dict[str, Any]:
    """シェルから渡された `列=値` を条件に直す（`true`・`false` は真偽、数字だけは整数、ほかは文字列）。"""
    out: dict[str, Any] = {}
    for arg in args:
        if "=" not in arg:
            raise FactsError(f"条件は 列=値 で書く: {arg!r}")
        col, value = arg.split("=", 1)
        if value in ("true", "false"):
            out[col] = value == "true"
        elif re.fullmatch(r"-?[0-9]+", value):
            out[col] = int(value)
        else:
            out[col] = value
    return out
