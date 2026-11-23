"""事実の重複の規則: 同じ事実を 2 つ以上のファイルの最上位の定数に持たない（`const-fact`）。事実の表の形（`fact-table`）。

事実の表（`contracts/facts/*.json` と `contracts/domains/*.json`）がある事実は、表から読む。表の形は `contracts/facts/fact.schema.json`（I-DT2・DC1）と列の設定から作る行の形を
`jsonschema`（draft 2020-12）で確かめる（設計の決定 46・47。実行時の読み手は形を検査しない）。表の `id` を定数へ写したものも重複に数える。
ただし `shared_names` が真の表は、`id` が他の名前空間と共有する名前なので、定数との検証（`table-set`・`table-str`）から外す（形は確かめる）。

手書きのスキーマの `enum`（`enum-set`・`enum-subset`）と、上限の設定の数（`limit-num`）の写しも重複に数える。上限の設定の数と比べるのは、
名前が上限・回数・待ちを表す定数だけで、語と値のキーの対応は `rules.json` の `facts.limit_kinds`、定数のまま置く値と理由は
`facts.limit_constants` が持つ。
"""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from fnmatch import fnmatch
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ndf.checks.model import CheckError, Finding, Scope, module_name, module_rel, read_contract
from ndf.contracts import limits
from ndf.contracts.facts import FACTS_DIR, SCHEMA_FILE, TABLE_DIRS
from ndf.contracts.facts import row_schema as contract_row_schema

CONST = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
SET_CALLS = frozenset({"set", "frozenset", "tuple", "list"})


@dataclass(frozen=True)
class Const:
    rel: str
    line: int
    name: str
    value: object

    @property
    def location(self) -> str:
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


def dict_values(node: ast.expr) -> frozenset[str] | None:
    """辞書の定数の値の集まり。値がすべて文字列で、集まりが 2 つ以上のときだけ返す。"""
    values = _strings(node.values) if isinstance(node, ast.Dict) else None
    return values if values is not None and len(values) >= 2 else None


def _targets(node: ast.stmt) -> list[ast.expr]:
    """文が値を代入する先。代入（`Assign`）と値のある注釈付きの代入（`AnnAssign`）のほかは空。"""
    if isinstance(node, ast.Assign):
        return node.targets
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return [node.target]
    return []


def consts(tree: ast.Module, rel: str) -> list[Const]:
    """モジュールの最上位の大文字の定数のうち、`lit` が読めたもの。辞書の定数は、値の集まりを `<名前>.values` として足す。"""
    found = []
    for node in tree.body:
        names = [t.id for t in _targets(node) if isinstance(t, ast.Name) and CONST.match(t.id)]
        if not names:
            continue
        read = (("", lit(node.value)), (".values", dict_values(node.value)))
        found += [Const(rel, node.lineno, n + suffix, value) for n in names for suffix, value in read if value is not None]
    return found


def _spread(group: list[Const]) -> bool:
    return len({c.rel for c in group}) >= 2


def _report(kind: str, group: list[Const], what: str) -> Finding:
    first, *rest = sorted(group, key=lambda c: (c.rel, c.line))
    others = "・".join(c.location for c in rest)
    return Finding("const-fact", first.rel, first.line, f"{kind}: {what}（{first.name} と {others}）")


def _by_value(items: list[Const], key) -> dict[object, list[Const]]:
    groups: dict[object, list[Const]] = defaultdict(list)
    for c in items:
        groups[key(c)].append(c)
    return groups


def _same_values(strs: list[Const], sets: list[Const], nums: list[Const]) -> list[Finding]:
    """同じ値（`same-str`・`same-set`）と同じ名前と数（`same-num`）で集めた組のうち、2 つ以上のファイルにまたがるもの。"""
    found = [_report("same-str", g, repr(v)) for v, g in _by_value(strs, lambda c: c.value).items() if _spread(g)]
    found += [_report("same-set", g, "同じ集まり") for g in _by_value(sets, lambda c: c.value).values() if _spread(g)]
    found += [_report("same-num", g, f"同じ名前と数 {k[1]}") for k, g in _by_value(nums, lambda c: (c.name, c.value)).items() if _spread(g)]
    return found


def _subsets(sets: list[Const]) -> list[Finding]:
    """集まりが別のファイルの集まりに真に含まれる組（`subset`）。"""
    return [
        _report("subset", [a, b], "集まりが別のファイルの集まりに含まれる")
        for a in sets
        for b in sets
        if a.rel != b.rel and a.value < b.value
    ]


def _members(strs: list[Const], sets: list[Const]) -> list[Finding]:
    """文字列が別のファイルの集まりの要素である組（`member`）。"""
    return [
        _report("member", [s, b], f"{s.value!r} が別のファイルの集まりの要素")
        for s in strs
        for b in sets
        if s.rel != b.rel and s.value in b.value
    ]


def groups(all_consts: list[Const], min_str: int) -> list[Finding]:
    """`same-str`・`same-set`・`same-num`・`subset`・`member` の組を返す。どの組も 2 つ以上のファイルにまたがる。"""
    strs = [c for c in all_consts if isinstance(c.value, str) and len(c.value) >= min_str]
    sets = [c for c in all_consts if isinstance(c.value, frozenset)]
    nums = [c for c in all_consts if isinstance(c.value, (int, float))]
    return _same_values(strs, sets, nums) + _subsets(sets) + _members(strs, sets)


@cache
def table_validator(schema_path: Path) -> Draft202012Validator:
    """表の共通の形（表と同じ保存先の `fact.schema.json`）の検証器。1 プロセスで保存先ごとに 1 回だけ読む。

    無い・読めない・スキーマとして誤りなら `CheckError`（表の形を確かめられないまま通さない）。
    """
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, ValueError, SchemaError) as exc:
        raise CheckError(f"表の共通の形を読めない: {schema_path}: {exc}") from exc
    return Draft202012Validator(schema)


def row_schema(columns: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """列の設定から作る行の形（`ndf.contracts.facts.row_schema`）。`id` は列の設定によらず文字列だけを求める。"""
    schema = contract_row_schema(columns)
    schema["properties"]["id"] = {"type": "string"}
    return schema


def shape_errors(data: object, common: Draft202012Validator) -> list[str]:
    """表の形の違反。共通の形に合ってから、列の設定から作った行の形で各行を確かめる。"""
    errors = sorted(common.iter_errors(data), key=lambda e: (e.json_path, e.message))
    if not errors:
        rows = Draft202012Validator({"properties": {"rows": {"items": row_schema(data["columns"])}}})
        errors = sorted(rows.iter_errors(data), key=lambda e: (e.json_path, e.message))
    return [f"{e.json_path}: {e.message}" for e in errors]


def _table_rows(path: Path, rel: str, common: Draft202012Validator) -> tuple[list[str], list[Finding]]:
    """事実の表 1 本の検証に使う `id` の並びと、形の違反。形が違うか、`shared_names` が真なら `id` を返さない。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], [Finding("fact-table", rel, 1, f"表を読めない: {exc}")]
    bad = [Finding("fact-table", rel, 1, f"表の形: {m}") for m in shape_errors(data, common)]
    if bad:
        return [], bad
    ids = [r["id"] for r in data["rows"]]
    dups = sorted({i for i in ids if ids.count(i) > 1})
    return [] if data.get("shared_names") is True else ids, [Finding("fact-table", rel, 1, f"`id` の重なり: {d}") for d in dups]


def _table_paths(scope: Scope, contracts_dir: str) -> list[Path]:
    """表のファイル（保存先の並び・名前の辞書順。`*.schema.json` を除く）。"""
    paths = []
    for d in TABLE_DIRS:
        base = scope.plugin_root / contracts_dir / d
        if base.is_dir():
            paths += [p for p in sorted(base.glob("*.json")) if not p.name.endswith(".schema.json")]
    return paths


def tables(scope: Scope, contracts_dir: str) -> tuple[dict[str, list[str]], list[Finding]]:
    """事実の表（`<contracts_dir>/facts/` と `<contracts_dir>/domains/`）を読み、表の名前 → `id` の並びと、形の違反を返す。

    表が無ければ空。2 つの保存先が同じ名前の表を持てば、後の保存先の表を違反にする（読み手は名前だけで引く）。
    `shared_names` が真の表は形だけを確かめ、返す `id` の対応に含めない（定数との検証をしない）。
    """
    paths = _table_paths(scope, contracts_dir)
    found: list[Finding] = []
    ids: dict[str, list[str]] = {}
    if not paths:
        return ids, found
    common = table_validator(scope.plugin_root / contracts_dir / FACTS_DIR / SCHEMA_FILE)
    seen: set[str] = set()
    for path in paths:
        if path.stem in seen:
            found.append(Finding("fact-table", scope.rel(path), 1, f"表の名前 {path.stem} が別の保存先の表と重なる"))
            continue
        seen.add(path.stem)
        rows, bad = _table_rows(path, scope.rel(path), common)
        if rows:
            ids[path.stem] = rows
        found += bad
    return ids, found


def _hit_kind(c: Const, idset: set[str], min_str: int) -> str | None:
    """定数 1 つが表の `id` を写すときの種別（`table-set`・`table-str`）。写さなければ `None`。"""
    if isinstance(c.value, frozenset):
        return "table-set" if len(c.value & idset) >= 2 else None
    if isinstance(c.value, str) and len(c.value) >= min_str and c.value in idset:
        return "table-str"
    return None


def table_hits(all_consts: list[Const], machine_ids: dict[str, list[str]], min_str: int) -> list[Finding]:
    """表の `id` を 2 つ以上含む集まり（`table-set`）と、表の `id` に等しい文字列（`table-str`）。"""
    found = []
    for name, ids in machine_ids.items():
        idset = set(ids)
        for c in all_consts:
            kind = _hit_kind(c, idset, min_str)
            if kind is not None:
                found.append(Finding("const-fact", c.rel, c.line, f"{kind}: {c.name} が表 {name} の id を写す"))
    return found


def _enums(node: Any, pointer: str) -> Iterator[tuple[str, frozenset[str]]]:
    """スキーマの中の `enum` の JSON Pointer と文字列の集まり（2 つ以上）。"""
    if isinstance(node, list):
        for i, child in enumerate(node):
            yield from _enums(child, f"{pointer}/{i}")
    if not isinstance(node, dict):
        return
    enum = node.get("enum")
    values = frozenset(v for v in enum if isinstance(v, str)) if isinstance(enum, list) else frozenset()
    if len(values) >= 2:
        yield pointer, values
    for key, child in node.items():
        yield from _enums(child, f"{pointer}/{key}")


def enum_sets(contracts_dir: Path, generated: list[str]) -> dict[str, frozenset[str]]:
    """手書きのスキーマの `enum` の集まり（`<スキーマ>#<JSON Pointer>` → 値）。`generated` の形のスキーマ（生成物）は除く。"""
    found: dict[str, frozenset[str]] = {}
    for path in sorted(contracts_dir.rglob("*.schema.json")):
        rel = path.relative_to(contracts_dir).as_posix()
        if not any(fnmatch(rel, pattern) for pattern in generated):
            found.update({f"{rel}#{ptr}": values for ptr, values in _enums(read_contract(path, "スキーマを読めない")[1], "")})
    return found


def _enum_dups(sets: list[Const], enums: Mapping[str, frozenset[str]]) -> list[Finding]:
    """スキーマの `enum` と同じ集まり（`enum-set`）か、その一部（`enum-subset`）の定数。定数 1 つに 1 件。"""
    found = []
    for c in sets:
        where = [k for k, values in enums.items() if c.value <= values]
        if where:
            kind = "enum-set" if any(c.value == enums[k] for k in where) else "enum-subset"
            found.append(Finding("const-fact", c.rel, c.line, f"{kind}: {c.name} がスキーマの enum を写す（{'・'.join(where)}）"))
    return found


def limit_numbers(path: Path) -> dict[tuple[str, float], list[str]]:
    """上限の設定の値のキーと数 → その数を持つ方針の名前。読めない・形が違えば `CheckError`。"""
    raw = read_contract(path, "上限の設定を読めない")[1]
    try:
        policies = limits.validate(raw)
    except limits.LimitsError as exc:
        raise CheckError(str(exc)) from exc
    found: dict[tuple[str, float], list[str]] = defaultdict(list)
    for name, policy in policies.items():
        for key in limits.VALUE_KEYS:
            if getattr(policy, key) is not None:
                found[key, getattr(policy, key)].append(name)
    return dict(found)


def limit_constants(conf: dict[str, Any]) -> frozenset[str]:
    """定数のまま置く値の名前（`facts.limit_constants`）。行の形が違うか理由が空なら `CheckError`。"""
    for row in conf["limit_constants"]:
        ok = set(row) == {"name", "kind", "reason"} and row["kind"] in ("external", "implementation") and row["reason"].strip()
        if not ok:
            raise CheckError(
                f"facts.limit_constants の行の形が違う（name・kind（external か implementation）・空でない reason を書く）: {row}"
            )
    return frozenset(row["name"] for row in conf["limit_constants"])


def _same_limits(c: Const, numbers: Mapping[tuple[str, float], list[str]], kinds: list[dict[str, list[str]]]) -> list[str]:
    """定数の名前の語に対応する値のキーに、同じ数を持つ方針（`<方針>.<キー>`）。"""
    keys = [key for row in kinds if any(word in c.name for word in row["words"]) for key in row["keys"]]
    return [f"{name}.{key}" for key in dict.fromkeys(keys) if (key, c.value) in numbers for name in numbers[key, c.value]]


def _limit_dups(
    nums: Mapping[str, Const], numbers: Mapping[tuple[str, float], list[str]], conf: dict[str, Any], rules_rel: str
) -> list[Finding]:
    """上限の設定の数と同じで、名前が上限・回数・待ちを表す定数（`limit-num`）と、当たらなくなった除外の行（`stale`）。

    `nums` は `<モジュール>.<定数>` → 数の定数。
    """
    allowed = limit_constants(conf)
    hits = {full: same for full, c in nums.items() if (same := _same_limits(c, numbers, conf["limit_kinds"]))}
    found = [
        Finding(
            "const-fact",
            nums[full].rel,
            nums[full].line,
            f"limit-num: {nums[full].name} が上限の設定の数を写す（{'・'.join(same[:3])} など {len(same)} 件。方針でなければ facts.limit_constants に理由を書く）",
        )
        for full, same in hits.items()
        if full not in allowed
    ]
    return found + [
        Finding("const-fact", rules_rel, 1, f"stale: facts.limit_constants の {full} は上限の設定の数に当たる定数でない（行を消す）")
        for full in sorted(allowed - set(hits))
    ]


def _contract_dups(scope: Scope, conf: dict[str, Any], by_source: Mapping[str, list[Const]]) -> list[Finding]:
    """スキーマの `enum` と上限の設定の写し。上限の設定を持たないプラグインには `_limit_dups` を当てない。"""
    contracts_dir = scope.plugin_root / conf["contracts_dir"]
    sets = [c for cs in by_source.values() for c in cs if isinstance(c.value, frozenset)]
    found = _enum_dups(sets, enum_sets(contracts_dir, conf["generated_schemas"]))
    path = contracts_dir / limits.NDF_SETTINGS
    if not path.is_file():
        return found
    nums = {f"{module}.{c.name}": c for module, cs in by_source.items() for c in cs if isinstance(c.value, (int, float))}
    rules_rel = scope.rel(scope.plugin_root / module_rel(f"{__package__}.rules", ".json"))
    return found + _limit_dups(nums, limit_numbers(path), conf, rules_rel)


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    conf = rules["facts"]
    by_source = {module_name(scope, src): consts(src.tree, src.rel) for src in scope.code()}
    all_consts = [c for cs in by_source.values() for c in cs]
    machine_ids, found = tables(scope, conf["contracts_dir"])
    found += groups(all_consts, conf["min_str"]) + table_hits(all_consts, machine_ids, conf["min_str"])
    return found + _contract_dups(scope, conf, by_source)
