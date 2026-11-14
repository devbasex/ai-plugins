"""規則 `child-deps`: 子の表のコピー（`children.json`）と、import の向き・順番・レーン・表の書き手を照らす。

#3347 の決定 97（先に要るものを import の持ち主から引く）・不変条件 IG1・IG2 と、#3378 の 3378-6（1 つの表へ複数の子が
並行で書く）を CI で縛る。

- 持ち主: `src/` のモジュールの持ち主は、`packages` が最も長く一致する子である。持ち主の無いモジュール（パッケージの
  `__init__` を除く）は指摘する。表を直す設計 PR を先に通してから、モジュールを足す
- IG1: 子 A のモジュールが読む別の子 B のモジュールは、A の先に要るものの推移の閉包に B が入る。A に追補 `A+` が
  あれば、`A+` の閉包も A のモジュールが読んでよい範囲に入る（追補は元の子のモジュールを直す）
- IG2: 閉じていない子は、同じレーン・同じ順番に `max_per_slot` 本まで。どの子の順番も、先に要る子の順番より大きい
- 表の書き手: 同じ表（`tables`）へ書く閉じていない子どうしは、どちらかが他方の閉包に入る。並行で書かず、後に流れる子が
  先の子のマージを取り込んでから衝突を解く

入れ替え（S2）がこのモジュールと `children.json` を消す。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Optional

from ndf.checks import imports
from ndf.checks.model import CheckError, Finding, Scope, Source, module_rel, read_json_text, source_imports

RULE = "child-deps"
# 子の表のコピーの置き場（プラグインの根からの相対パス）
CHILDREN_REL = module_rel(__name__.rpartition(".")[0], "/children.json")
# 追補の単位の記号は、元の子の記号の後ろにこの文字を付ける（#3395 の決定 110）
ADDENDUM_MARK = "+"


@dataclass(frozen=True)
class Child:
    name: str
    packages: tuple[str, ...]
    needs: tuple[str, ...]
    order: int
    lane: str
    closed: bool


@dataclass(frozen=True)
class Children:
    """子の表。`lines` は子の記号 → `children.json` の行（指摘の行に使う）。"""

    rows: dict[str, Child]
    tables: dict[str, tuple[str, ...]]
    rel: str
    lines: dict[str, int]


def _strs(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v for v in value)


def _child(name: str, row: Any) -> Child:
    ok = (
        isinstance(row, dict)
        and _strs(row.get("packages"))
        and _strs(row.get("needs"))
        and type(row.get("order")) is int
        and isinstance(row.get("lane"), str)
        and bool(row["lane"])
        and isinstance(row.get("closed"), bool)
    )
    if not ok:
        raise CheckError(f"children.json の子 {name} の形が違う（packages・needs・order・lane・closed）")
    return Child(name, tuple(row["packages"]), tuple(row["needs"]), row["order"], row["lane"], row["closed"])


def _validate(rows: dict[str, Child], tables: dict[str, tuple[str, ...]]) -> None:
    """記号の参照と、パッケージの持ち主の重なりを確かめる。"""
    named = [n for c in rows.values() for n in c.needs] + [w for ws in tables.values() for w in ws]
    unknown = sorted(set(named) - set(rows))
    if unknown:
        raise CheckError(f"children.json が知らない子を指す: {unknown}")
    packages = [p for c in rows.values() for p in c.packages]
    shared = sorted({p for p in packages if packages.count(p) > 1})
    if shared:
        raise CheckError(f"children.json で 2 つの子が同じパッケージを持つ: {shared}")


def load_children(path: Path, rel: str) -> Children:
    """`children.json` を読む。無い・形が違う・知らない子を指すときは `CheckError`。`rel` は指摘に書くパス。"""
    text, data = read_json_text(path, "children.json を読めない")
    if not isinstance(data, dict) or not isinstance(data.get("children"), dict) or not isinstance(data.get("tables"), dict):
        raise CheckError(f"children.json に children と tables の表が無い: {path}")
    rows = {name: _child(name, row) for name, row in data["children"].items()}
    if not all(_strs(ws) for ws in data["tables"].values()):
        raise CheckError(f"children.json の tables の書き手が子の記号の並びでない: {path}")
    tables = {table: tuple(ws) for table, ws in data["tables"].items()}
    _validate(rows, tables)
    text_lines = text.splitlines()
    lines = {name: next((no for no, line in enumerate(text_lines, 1) if line.lstrip().startswith(f'"{name}"')), 1) for name in rows}
    return Children(rows, tables, rel, lines)


def owner(module: str, children: Children) -> Optional[str]:
    """モジュール（`ndf.<…>`）の持ち主の子。`packages` が最も長く一致する子で、無ければ None。"""
    best = max(
        ((len(p), c.name) for c in children.rows.values() for p in c.packages if imports.is_under(module, p)),
        default=None,
    )
    return best[1] if best else None


def closure(child: str, children: Children) -> frozenset[str]:
    """子の `needs` の推移の閉包（自分を含まない）。輪があれば `CheckError`。"""
    done: set[str] = set()
    path: list[str] = []

    def visit(name: str) -> None:
        if name in path:
            raise CheckError(f"children.json の needs に輪がある: {' → '.join([*path[path.index(name) :], name])}")
        if name in done:
            return
        path.append(name)
        for need in children.rows[name].needs:
            visit(need)
        path.pop()
        done.add(name)

    visit(child)
    return frozenset(done - {child})


def _reach(child: str, children: Children) -> frozenset[str]:
    """子のモジュールが読んでよい子: 自分・追補・それぞれの閉包。"""
    names = [n for n in (child, child + ADDENDUM_MARK) if n in children.rows]
    return frozenset(names).union(*(closure(n, children) for n in names))


def import_findings(scope: Scope, children: Children) -> list[Finding]:
    """持ち主の無いモジュールと、IG1（閉包の外の子のモジュールを読む import）。"""
    by_module = scope.modules()
    known = set(by_module)
    reach: dict[str, frozenset[str]] = {}
    found = []
    for module, src in by_module.items():
        mine = owner(module, children)
        if mine is None:
            if src.path.name != "__init__.py":
                found.append(
                    Finding(RULE, src.rel, 1, f"持ち主の子が children.json に無いモジュール: {module}。表を直す設計 PR を先に通す")
                )
            continue
        allowed = reach.setdefault(mine, _reach(mine, children))
        found += [
            Finding(RULE, src.rel, line, f"{mine} の先に要るものに無い {theirs} の {target} を読む（IG1）")
            for line, target, theirs in _owned_targets(src, module, known, children)
            if theirs not in allowed
        ]
    return found


def _owned_targets(src: Source, module: str, known: set[str], children: Children) -> list[tuple[int, str, str]]:
    """モジュールの import が読む、持ち主のあるモジュールと持ち主（行・モジュール・持ち主）。"""
    imps = source_imports(src, module)
    pairs = [(imp.line, target, owner(target, children)) for imp in imps for target in imports.targets(imp, known)]
    return [(line, target, theirs) for line, target, theirs in pairs if theirs is not None]


def _at(children: Children, name: str, detail: str) -> Finding:
    return Finding(RULE, children.rel, children.lines[name], detail)


def plan_findings(children: Children, max_per_slot: int) -> list[Finding]:
    """IG2（順番とレーン）と、同じ表へ並行で書く子。指摘は 順番 → 枠 → 表 の順に並べる。"""
    return _order_findings(children) + _slot_findings(children, max_per_slot) + _table_findings(children)


def _order_findings(children: Children) -> list[Finding]:
    """IG2: 順番が先に要る子の順番より大きくない子。"""
    rows = children.rows
    found = []
    for c in rows.values():
        late = [n for n in c.needs if rows[n].order >= c.order]
        if late:
            found.append(_at(children, c.name, f"{c.name} の順番 {c.order} が先に要る子（{'・'.join(late)}）の順番より大きくない（IG2）"))
    return found


def _slot_findings(children: Children, max_per_slot: int) -> list[Finding]:
    """IG2: 同じレーン・同じ順番に閉じていない子が `max_per_slot` 本を超える枠。"""
    slots: dict[tuple[str, int], list[str]] = defaultdict(list)
    for c in children.rows.values():
        if not c.closed:
            slots[(c.lane, c.order)].append(c.name)
    found = []
    for (lane, order), names in sorted(slots.items()):
        if len(names) > max_per_slot:
            detail = f"レーン {lane} の順番 {order} に閉じていない子が {len(names)} 本ある（上限 {max_per_slot}。IG2）: {'・'.join(names)}"
            found.append(_at(children, names[0], detail))
    return found


def _table_findings(children: Children) -> list[Finding]:
    """同じ表へ書く閉じていない子どうしで、どちらも他方の閉包に入らない組。"""
    found = []
    for table, writers in sorted(children.tables.items()):
        live = [w for w in writers if not children.rows[w].closed]
        for a, b in combinations(live, 2):
            if a not in closure(b, children) and b not in closure(a, children):
                detail = f"表 {table} へ {a} と {b} が並行で書く。先に要るもので順序を付け、後の子が衝突を解く"
                found.append(_at(children, b, detail))
    return found


def check(scope: Scope, children: Children, max_per_slot: int) -> list[Finding]:
    """見る範囲（検査が作った構文木の集まり）に、持ち主・IG1・IG2・表の書き手を当てる。"""
    return import_findings(scope, children) + plan_findings(children, max_per_slot)


def apply(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    """見る範囲のプラグインの `children.json` を読み、規則 `child-deps` を当てる（検査のエントリポイントが呼ぶ）。"""
    path = scope.plugin_root / CHILDREN_REL
    return check(scope, load_children(path, scope.rel(path)), rules["child_deps"]["max_per_slot"])
