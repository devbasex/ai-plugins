"""事実の表（`contracts/facts/<名前>.json` と `contracts/domains/<名前>.json`）の読み手。

2 つ以上の読み手が使う事実（終了コードの区分・領域の語彙など）を表に 1 か所で持ち、コードは値を写さずに引く。
`facts/` は契約そのものの表（D1）、`domains/` は領域の表（ランタイム・収束ループ・宣言の置き場など。D2）を置く。
表の名前は 2 つの置き場をまたいで 1 つに決まり、読み手は名前だけで引く（置き場を知らない）。
表の共通の形は `contracts/facts/fact.schema.json` にあり、行の形は表の `columns` の宣言から作って確かめる。
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from jsonschema import Draft202012Validator

from ndf import contracts

FACTS_DIR = "facts"
DOMAINS_DIR = "domains"
# 表を置く置き場（引く順）。表の名前は置き場をまたいで重ならない
TABLE_DIRS = (FACTS_DIR, DOMAINS_DIR)
# 表の共通の形のファイル（置き場に表と並ぶが、表ではない）
SCHEMA_FILE = "fact.schema.json"
SCHEMA = f"{FACTS_DIR}/{SCHEMA_FILE}"
NAME = re.compile(r"^[a-z][a-z0-9-]*$")
# 列の ref の 2 つの形（`limits.json#<接頭辞>` と `<置き場>/<表>`）のうち前者の頭。limits.json は limits.DECLARATION と同じ
LIMITS_REF = "limits.json#"


class FactsError(contracts.ContractError):
    """表が無い・形が違う・引いた行や列が無い。"""


class Table(NamedTuple):
    name: str
    summary: str
    owner: str
    columns: dict[str, dict[str, Any]]
    rows: tuple[dict[str, Any], ...]


def _row_schema(columns: dict[str, dict[str, Any]]) -> dict[str, Any]:
    props: dict[str, Any] = {"id": {"type": "string", "minLength": 1}}
    for col, decl in columns.items():
        kind = decl["type"]
        props[col] = {"type": "array", "items": {"type": "string"}} if kind == "array" else {"type": kind}
    required = ["id", *(c for c, d in columns.items() if c != "id" and not d.get("optional"))]
    return {"type": "object", "additionalProperties": False, "required": required, "properties": props}


def _validate_rows(raw: dict[str, Any], where: str) -> None:
    """行を列の宣言から作った形で確かめ、最初に形の違う行で `FactsError`。"""
    validator = Draft202012Validator(_row_schema(raw["columns"]))
    for i, row in enumerate(raw["rows"]):
        bad = sorted(e.message for e in validator.iter_errors(row))
        if bad:
            raise FactsError(f"事実の表 {where} の行 {i}（{row.get('id')!r}）の形が違う: {'; '.join(bad)}")


def _validate(raw: Any, name: str, where: str) -> Table:
    contracts.require_schema(raw, SCHEMA, FactsError, f"事実の表 {where}")
    _validate_rows(raw, where)
    dup = contracts.duplicates(r["id"] for r in raw["rows"])
    if dup:
        raise FactsError(f"事実の表 {where} に同じ id が 2 つある: {', '.join(dup)}")
    return Table(name, raw["summary"], raw["owner"], dict(raw["columns"]), tuple(raw["rows"]))


def _found() -> dict[str, list[str]]:
    """表の名前 → 表を持つ置き場の並び（共通の形のファイルは含まない）。"""
    found: dict[str, list[str]] = {}
    for d in TABLE_DIRS:
        for p in sorted((contracts.contracts_dir() / d).glob("*.json")):
            if p.name != SCHEMA_FILE:
                found.setdefault(p.stem, []).append(d)
    return found


def names() -> tuple[str, ...]:
    """置き場にある表の名前（辞書順）。"""
    return tuple(sorted(_found()))


def place(name: str) -> str:
    """表 `name` の置き場（`contracts/` からの相対の `<置き場>/<name>.json`）。

    2 つの置き場が同じ名前を持てば `FactsError`。どちらにも無ければ `facts/` の候補を返す（読む側が無いことを報告する）。
    """
    found = _found()
    dirs = found[name] if name in found else [FACTS_DIR]
    if len(dirs) > 1:
        raise FactsError(f"事実の表 {name} が 2 つの置き場にある: {', '.join(f'{d}/{name}.json' for d in dirs)}")
    return f"{dirs[0]}/{name}.json"


def table(name: str) -> Table:
    """表 `facts/<name>.json` を 1 プロセスに 1 回読んで返す。"""
    if not NAME.match(name):
        raise FactsError(f"表の名前が違う形: {name!r}")
    return _read(name)


@contracts.cached_reader
def _read(name: str) -> Table:
    where = place(name)
    return _validate(contracts.load_as(where, FactsError), name, where)


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


def value(name: str, id: str, col: str) -> Any:
    """`id` の行の列 `col` の値。行が無い・列が宣言に無い・行がその列を持たなければ `FactsError`。"""
    found = row(name, id)
    _require_columns(table(name), [col])
    if col not in found:
        raise FactsError(f"事実の表 {name} の行 {id} は列 {col} を持たない")
    return found[col]


def column(name: str, col: str) -> dict[str, Any]:
    """列 `col` を持つ行の `id` → 値（表の並び）。列を持たない行（任意の列）は含まない。"""
    t = table(name)
    _require_columns(t, [col])
    return {r["id"]: r[col] for r in t.rows if col in r}


def _limits_ref_errors(name: str, col: str, prefix: str, values: list[tuple[str, Any]], policy_names: set[str]) -> list[str]:
    return [f"{name}:{rid} の {col} {v!r} の方針 {prefix}{v} が上限の宣言に無い" for rid, v in values if f"{prefix}{v}" not in policy_names]


def _table_ref_errors(name: str, col: str, ref: str, values: list[tuple[str, Any]]) -> list[str]:
    other = ref.split("/", 1)[1]
    if place(other) != f"{ref}.json":
        return [f"{name} の列 {col} の ref {ref} の表は {place(other)} にある"]
    known = set(ids(other))
    return [f"{name}:{rid} の {col} {v!r} が表 {other} に無い" for rid, v in values if v not in known]


def ref_errors(name: str, policy_names: set[str]) -> list[str]:
    """列の `ref`（`limits.json#<接頭辞>` か `<置き場>/<表>`）の先に値があるかを確かめる。"""
    t = table(name)
    errs = []
    for col, decl in t.columns.items():
        ref = decl.get("ref")
        if not ref:
            continue
        values = [(r["id"], r[col]) for r in t.rows if col in r]
        if ref.startswith(LIMITS_REF):
            errs += _limits_ref_errors(name, col, ref.removeprefix(LIMITS_REF), values, policy_names)
        else:
            errs += _table_ref_errors(name, col, ref, values)
    return errs


def _coerce(value: str) -> Any:
    """シェルから渡された値を型へ直す（`true`・`false` は真偽、数字だけは整数、ほかは文字列）。"""
    if value in ("true", "false"):
        return value == "true"
    if re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    return value


def parse_where(args: list[str]) -> dict[str, Any]:
    """シェルから渡された `列=値` を条件に直す（値の型は `_coerce`）。"""
    out: dict[str, Any] = {}
    for arg in args:
        if "=" not in arg:
            raise FactsError(f"条件は 列=値 で書く: {arg!r}")
        col, value = arg.split("=", 1)
        out[col] = _coerce(value)
    return out
