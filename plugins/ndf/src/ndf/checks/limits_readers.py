"""上限の設定の読み手の指定の規則（`limit-readers`）: 方針の `read_by` と、実際に方針を引くモジュールを突き合わせる。

方針を引く関数（読み口）と、名前を実行時に組み立てる読み手は `rules.json` の `limits` が持つ。

- `readers`: 読み口の関数の完全な名前 → 方針の名前の引数（名前と位置）。名前を引数で受けて読み口へ渡す関数・メソッド・クラスの
  欄・引数で受けた読み口（`<関数>.<引数>`）も読み口として並べ、呼び手を読み手に数える。`template` があれば、引数の値を `{}` に入れた名前を引く
- `tables`・`whole`: 方針の表を丸ごと返す名前と、表を受け取るだけで方針を引かないモジュール
- `dynamic`: 名前を実行時に組み立てる読み手。関数 `at` の中の式 `expr` が、`vocabulary`（コントラクトのファイルと JSON Pointer。
  `*` は辞書か並びのすべて）の値を `template` の `{}` に、`*` が当たったキー（左から 1 番目が `{1}`）を `{1}` に入れた名前を引く。
  `expr` は読み口の引数の式で、引数が関数の中の名前なら、その名前に入る式（`for` の変数なら並びの式）。式の綴りか組み立ての形が
  実際のコードと違えば、その読み口は解けない。表を丸ごと参照する読みには当てない（1 行ずつ読む読み口を使う）
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Optional

from ndf import contracts
from ndf.checks import imports
from ndf.checks.model import CheckError, Finding, Scope
from ndf.checks.spec_model import CONTRACTS
from ndf.contracts import limits

RULE = "limit-readers"
HOLE = "{}"
NO_NAMES: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Reader:
    """読み口 1 つ。方針の名前の引数の名前と位置（メソッドは `self` を数えない）。"""

    arg: str
    index: int
    template: str = HOLE


@dataclass(frozen=True)
class Dynamic:
    """動的な読み口 1 つ。関数 `at` の中の式 `expr` が、`shape` の形で `names` のどれかを組み立てる。"""

    at: str
    expr: str
    shape: str
    names: frozenset[str]


@dataclass(frozen=True)
class Settings:
    """規則のデータ（`rules.json` の `limits`）。"""

    readers: Mapping[str, Reader]
    tables: frozenset[str]
    whole: frozenset[str]
    dynamic: tuple[Dynamic, ...]

    @cached_property
    def methods(self) -> frozenset[str]:
        """読み口に並べたメソッドの名前。"""
        return frozenset(full.rpartition(".")[2] for full in self.readers if _is_class(full.rpartition(".")[0]))


@dataclass(frozen=True)
class Read:
    """方針を引く箇所 1 つ。`names` が None なら名前を解けない。`dynamic` なら動的な読み口の語彙から組み立てた名前。"""

    module: str
    rel: str
    line: int
    names: Optional[frozenset[str]]
    dynamic: bool = False


def _pointed(data: Any, steps: list[str], keys: tuple[str, ...] = ()) -> Iterator[tuple[Any, tuple[str, ...]]]:
    """JSON Pointer の `steps` が指す値と、`*` が当たったキーの組。"""
    if not steps:
        yield data, keys
        return
    step, rest = steps[0], steps[1:]
    if step != "*":
        yield from _pointed(data[int(step)] if isinstance(data, list) else data[step], rest, keys)
        return
    items = data.items() if isinstance(data, dict) else enumerate(data)
    for key, value in items:
        yield from _pointed(value, rest, (*keys, str(key)))


def _filled(template: str, value: str, keys: tuple[str, ...]) -> str:
    name = template.replace("{}", value)
    for i, key in enumerate(keys, 1):
        name = name.replace(f"{{{i}}}", key)
    return name


def _contract(path: Path, unreadable: str) -> tuple[str, Any]:
    """コントラクトのファイルの本文と値。実行時の読み手と同じく、同じキーが 2 つあれば断る。"""
    try:
        return contracts.read_text_json(path)
    except (contracts.ContractError, UnicodeDecodeError) as exc:
        raise CheckError(f"{unreadable}: {exc}") from exc


def dynamic_names(row: Mapping[str, str], data: Any) -> frozenset[str]:
    """`dynamic` の 1 行が組み立てうる方針の名前。語彙の値のうち文字列でないもの（null など）は名前を作らない。"""
    try:
        values = list(_pointed(data, row["vocabulary"].partition("#")[2].split("/")[1:]))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise CheckError(f"動的な読み口の語彙を引けない: {row['vocabulary']}: {exc}") from exc
    return frozenset(_filled(row["template"], value, keys) for value, keys in values if isinstance(value, str))


def _dynamic(row: Mapping[str, str], data: Any) -> Dynamic:
    expr = ast.unparse(ast.parse(row["expr"], mode="eval"))
    shape = re.sub(r"\{\d+\}", HOLE, row["template"])
    return Dynamic(row["at"], expr, shape, dynamic_names(row, data))


def settings(rules: dict[str, Any], contracts_dir: Path) -> Settings:
    """`rules.json` の `limits` を読み、動的な読み口の名前をコントラクトのファイルの語彙から組み立てる（ファイルは 1 回ずつ読む）。"""
    conf = rules["limits"]
    rels = {row["vocabulary"].partition("#")[0] for row in conf["dynamic"]}
    data = {rel: _contract(contracts_dir / rel, "動的な読み口の語彙を読めない")[1] for rel in sorted(rels)}
    dynamic = tuple(_dynamic(row, data[row["vocabulary"].partition("#")[0]]) for row in conf["dynamic"])
    readers = {name: Reader(**row) for name, row in conf["readers"].items()}
    whole = frozenset(row["module"] for row in conf["whole"])
    return Settings(readers, frozenset(conf["tables"]), whole, dynamic)


class _Module:
    """モジュール 1 つの、名前を解くための表。"""

    def __init__(self, rel: str, tree: ast.Module) -> None:
        self.rel, self.tree = rel, tree
        parts = Path(rel).with_suffix("").parts[1:]
        self.is_package = parts[-1] == "__init__"
        self.name = ".".join(parts[:-1] if self.is_package else parts)
        self.imported = self.bindings(tree)
        self.consts: dict[str, ast.expr] = {}
        self.defs: dict[str, ast.AST] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.defs[node.name] = node
            elif isinstance(node, ast.Assign):
                self.consts.update((t.id, node.value) for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name):
                self.consts[node.target.id] = node.value

    def bindings(self, scope: ast.AST) -> dict[str, str]:
        """囲み `scope` の import が束ねる名前。"""
        return imports.bindings(scope, self.name, self.is_package)


@dataclass(frozen=True)
class _Scope:
    """関数かクラスの 1 つの囲み。`params` は関数の引数、`imported` はこの囲みの import が束ねる名前。"""

    node: ast.AST
    name: str
    params: frozenset[str]
    is_class: bool = False
    imported: Mapping[str, str] = field(default_factory=dict)

    @cached_property
    def local(self) -> Mapping[str, list[tuple[ast.expr, bool]]]:
        """この囲みで束ねる名前 → 入る式と、その式の要素 1 つずつ（`for` の変数）かどうか（1 回だけ歩く）。"""
        found: defaultdict[str, list[tuple[ast.expr, bool]]] = defaultdict(list)
        for node in _within(self.node):
            for target, value, many in _assigned(node):
                if isinstance(target, ast.Name):
                    found[target.id].append((value, many))
        return found

    def binds(self, name: str) -> bool:
        return name in self.params or name in self.imported or name in self.local


def _params(args: ast.arguments) -> frozenset[str]:
    rest = [a for a in (args.vararg, args.kwarg) if a is not None]
    return frozenset(a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, *rest])


def _field(node: Optional[ast.AST], name: str, part: str) -> Optional[ast.expr]:
    """クラスの欄 `name` の注釈（`annotation`）か既定値（`value`）。"""
    body = node.body if isinstance(node, ast.ClassDef) else []
    fields = [n for n in body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == name]
    return getattr(fields[0], part) if fields else None


def _annotation(node: ast.AST, arg: str) -> Optional[ast.expr]:
    """関数の引数 `arg` の注釈。"""
    args = node.args if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
    named = [*args.posonlyargs, *args.args, *args.kwonlyargs] if args is not None else []
    return next((a.annotation for a in named if a.arg == arg), None)


def _default(node: ast.AST, arg: str) -> Optional[ast.expr]:
    """関数の引数かクラスの欄 `arg` の既定値。"""
    if isinstance(node, ast.ClassDef):
        return _field(node, arg, "value")
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    positional = [*node.args.posonlyargs, *node.args.args]
    defaults = dict(zip(reversed([a.arg for a in positional]), reversed(node.args.defaults)))
    defaults.update({a.arg: d for a, d in zip(node.args.kwonlyargs, node.args.kw_defaults) if d is not None})
    return defaults.get(arg)


def _within(scope: ast.AST) -> Iterator[ast.AST]:
    """囲みの中の節点。内側の関数・クラス・lambda の中へは降りない。"""
    for node in ast.iter_child_nodes(scope):
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield from _within(node)


def _assigned(node: ast.AST) -> Iterator[tuple[ast.expr, ast.expr, bool]]:
    """節点が束ねる先と、入る式と、その式の要素 1 つずつかどうか。"""
    if isinstance(node, ast.Assign):
        yield from ((t, node.value, False) for t in node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
        yield node.target, node.value, False
    elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
        yield node.target, node.iter, True


class _Walker(ast.NodeVisitor):
    """モジュール 1 つを歩き、方針を引く箇所を `reads` へ集める。"""

    def __init__(self, mod: _Module, modules: Mapping[str, _Module], conf: Settings) -> None:
        self.mod, self.modules, self.conf = mod, modules, conf
        self.scopes: list[_Scope] = []
        self.called: set[int] = set()
        self.handed: set[int] = set()
        self.resolving: set[str] = set()
        self.reads: list[Read] = []

    # ---------------------------------------------------------------- 名前を解く

    def _canonical(self, name: str) -> str:
        """別のモジュールが import した名前（再公開）を、元の完全な名前へ直す。"""
        owner, _, attr = name.rpartition(".")
        if owner in self.modules and name not in self.modules and attr in self.modules[owner].imported:
            return self.modules[owner].imported[attr]
        return name

    def _binder(self, name: str) -> Optional[_Scope]:
        """名前を束ねる、いちばん内側の囲み（クラスの囲みは、その直下の式からだけ引ける）。モジュールの名前は None。"""
        visible = (s for i, s in enumerate(reversed(self.scopes)) if i == 0 or not s.is_class)
        return next((s for s in visible if s.binds(name)), None)

    def _param_scope(self, name: str) -> Optional[_Scope]:
        """名前を引数に持つ囲み。それより内側の囲みが同じ名前を束ねていれば None。"""
        found = self._binder(name)
        return found if found is not None and name in found.params else None

    def _imported(self, name: str) -> Optional[str]:
        """名前が指す import の先の完全な名前（内側の囲みから解く）。"""
        found = self._binder(name)
        return (found.imported if found is not None else self.mod.imported).get(name)

    def _method(self, name: str) -> Optional[str]:
        owner = next((s for s in reversed(self.scopes) if s.is_class), None)
        return f"{owner.name}.{name}" if owner is not None else None

    def full_name(self, node: ast.expr) -> Optional[str]:
        """式が指す完全な名前。引数は `<関数>.<引数>`、`self` の属性は `<クラス>.<属性>`。解けなければ None。"""
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                return self._method(node.attr)
            base = self.full_name(node.value)
            return self._canonical(f"{base}.{node.attr}") if base is not None else None
        if not isinstance(node, ast.Name):
            return None
        scope = self._param_scope(node.id)
        if scope is not None:
            return f"{scope.name}.{node.id}"
        imported = self._imported(node.id)
        if imported is not None:
            return self._canonical(imported)
        at_module = self._binder(node.id) is None and (node.id in self.mod.defs or node.id in self.mod.consts)
        return f"{self.mod.name}.{node.id}" if at_module else None

    def _constant(self, node: ast.expr) -> Optional[ast.expr]:
        """名前か属性が指す、モジュールの最上位の代入の右辺（import の先は 1 階層だけ）。"""
        name = self.full_name(node)
        owner, _, attr = (name if name is not None else "").rpartition(".")
        found = self.modules[owner].consts.get(attr) if owner in self.modules else None
        return self._constant(found) if owner == self.mod.name and isinstance(found, (ast.Name, ast.Attribute)) else found

    def names_of(self, node: ast.expr) -> Optional[frozenset[str]]:
        """式がとりうる方針の名前。解けなければ None。"""
        if isinstance(node, ast.IfExp):
            return _union([self.names_of(node.body), self.names_of(node.orelse)])
        if isinstance(node, ast.Subscript):
            return self._elements(node.value)
        local = self._local(node.id) if isinstance(node, ast.Name) else []
        if isinstance(node, ast.Name) and local:
            return self._names_of_local(node.id, local)
        return _literal(self._constant(node) if isinstance(node, (ast.Name, ast.Attribute)) else node)

    def _local(self, name: str) -> list[tuple[ast.expr, bool]]:
        """囲む関数の中で `name` に入る式。関数の中で束ねていない名前と引数は空。"""
        found = self._binder(name)
        if found is None or name in found.params or name in found.imported:
            return []
        return found.local[name]

    def _names_of_local(self, name: str, local: list[tuple[ast.expr, bool]]) -> Optional[frozenset[str]]:
        """関数の中の名前に入る式の和。自分の値から作り直す名前（`x = x if x else "a.b"`）は解けない。"""
        if name in self.resolving:
            return None
        self.resolving.add(name)
        found = _union([self._elements(e) if many else self.names_of(e) for e, many in local])
        self.resolving.discard(name)
        return found

    def _elements(self, node: ast.expr) -> Optional[frozenset[str]]:
        """並びの要素か辞書の値がとりうる方針の名前。別のモジュールの並びは、文字列の要素だけを解く。"""
        seq = self._constant(node) if isinstance(node, (ast.Name, ast.Attribute)) else node
        if not isinstance(seq, (ast.Dict, ast.Tuple, ast.List)):
            return None
        own = seq is node or seq in self.mod.consts.values()
        values = seq.values if isinstance(seq, ast.Dict) else seq.elts
        return _union([self.names_of(v) if own else _literal(v) for v in values])

    # ---------------------------------------------------------------- 受け手の型を解く

    def _annotated(self, mod: _Module, node: Optional[ast.expr]) -> Optional[str]:
        """注釈の式が指すクラスの完全な名前（`Optional[X]`・`X | None`・文字列の注釈は X）。"""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return self._annotated(mod, ast.parse(node.value, mode="eval").body)
        if isinstance(node, ast.Subscript) and getattr(node.value, "id", getattr(node.value, "attr", "")) == "Optional":
            return self._annotated(mod, node.slice)
        if isinstance(node, ast.BinOp):
            return self._annotated(mod, node.right if isinstance(node.left, ast.Constant) and node.left.value is None else node.left)
        if isinstance(node, ast.Attribute):
            base = self._annotated(mod, node.value)
            return self._canonical(f"{base}.{node.attr}") if base is not None else None
        if not isinstance(node, ast.Name):
            return None
        return self._canonical(mod.imported[node.id]) if node.id in mod.imported else f"{mod.name}.{node.id}"

    def _type_of(self, node: ast.expr) -> Optional[str]:
        """式の値のクラスの完全な名前。引数と欄の注釈・クラスの呼び出し・関数の戻り値の注釈から解く。"""
        if isinstance(node, ast.Call):
            return self._returned(node.func)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            owner = next((s.node for s in reversed(self.scopes) if s.is_class), None)
            return self._annotated(self.mod, _field(owner, node.attr, "annotation"))
        if not isinstance(node, ast.Name):
            return None
        scope = self._param_scope(node.id)
        if scope is not None:
            return self._annotated(self.mod, _annotation(scope.node, node.id))
        bound = [e for e, many in self._local(node.id) if not many]
        return self._returned(bound[0].func) if len(bound) == 1 and isinstance(bound[0], ast.Call) else None

    def _returned(self, func: ast.expr) -> Optional[str]:
        """呼び先がクラスならその名前、関数なら戻り値の注釈のクラス。"""
        name = self.full_name(func)
        owner, _, attr = (name if name is not None else "").rpartition(".")
        mod = self.modules[owner] if owner in self.modules else None
        found = mod.defs[attr] if mod is not None and attr in mod.defs else None
        if mod is None or isinstance(found, ast.ClassDef):
            return name if mod is not None else None
        return self._annotated(mod, found.returns) if isinstance(found, (ast.FunctionDef, ast.AsyncFunctionDef)) else None

    # ---------------------------------------------------------------- 読み口の呼び出し

    def _reader_of(self, call: ast.Call) -> Optional[Reader]:
        name = self.full_name(call.func)
        # 受け手の型は、読み口に並べたメソッドの名前の呼び出しだけで解く
        if name not in self.conf.readers and isinstance(call.func, ast.Attribute) and call.func.attr in self.conf.methods:
            owner = self._type_of(call.func.value)
            name = f"{owner}.{call.func.attr}" if owner is not None else None
        return self.conf.readers[name] if name is not None and name in self.conf.readers else None

    def _assembled(self, node: ast.expr) -> tuple[str, list[ast.expr]]:
        """名前を組み立てる式の形（解けない部分は `{}`）と、解けない部分の式。"""
        if isinstance(node, ast.JoinedStr):
            parts = [self._assembled(v.value if isinstance(v, ast.FormattedValue) else v) for v in node.values]
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            parts = [self._assembled(node.left), self._assembled(node.right)]
        else:
            known = self.names_of(node)
            parts = [(next(iter(known)), [])] if known is not None and len(known) == 1 else [(HOLE, [node])]
        return "".join(shape for shape, _ in parts), [hole for _, holes in parts for hole in holes]

    def _handed_by_caller(self, node: ast.expr) -> Optional[Reader]:
        """式が、読み口として並べた関数の引数かクラスの欄そのものなら、その読み口。関数の中で束ね直した引数は当たらない。"""
        if isinstance(node, ast.Name):
            scope = self._param_scope(node.id)
            reader = self.conf.readers.get(scope.name) if scope is not None else None
            rebound = scope is not None and node.id in scope.local
            return reader if reader is not None and reader.arg == node.id and not rebound else None
        if not isinstance(node, ast.Attribute):
            return None
        fields = [r for full, r in self.conf.readers.items() if _is_class(full) and full.rpartition(".")[0] == self.mod.name]
        return next((r for r in fields if r.arg == node.attr), None)

    def _passed_through(self, node: ast.expr) -> bool:
        """式が呼び手の渡した名前をそのまま（読み口に `template` があればその形に）引く。呼び手を読み手に数えるので、ここでは数えない。"""
        shape, holes = self._assembled(node)
        reader = self._handed_by_caller(holes[0]) if len(holes) == 1 else None
        return reader is not None and shape == reader.template

    def _dynamic_names(self, node: ast.expr) -> Optional[frozenset[str]]:
        """動的な読み口の指定が、この関数のこの式に当たるときの名前。当たる指定が無ければ None。"""
        local = self._local(node.id) if isinstance(node, ast.Name) else []
        expr = local[0][0] if len(local) == 1 else node
        at = next((s.name for s in reversed(self.scopes) if not s.is_class), self.mod.name)
        rows = [d for d in self.conf.dynamic if d.at == at and self._is_assembly(expr, d)]
        return frozenset().union(*(d.names for d in rows)) if rows else None

    def _is_assembly(self, node: ast.expr, row: Dynamic) -> bool:
        return ast.unparse(node) == row.expr and self._assembled(node)[0] == row.shape

    def _record_argument(self, line: int, node: ast.expr, reader: Reader) -> None:
        """方針の名前の引数 1 つの読みを記録する（条件式は枝ごとに解く）。"""
        if isinstance(node, ast.IfExp):
            self._record_argument(line, node.body, reader)
            self._record_argument(line, node.orelse, reader)
            return
        if self._passed_through(node):
            return
        names = self.names_of(node)
        found = names if names is not None else self._dynamic_names(node)
        filled = None if found is None else frozenset(reader.template.replace(HOLE, n) for n in found)
        self._record(line, filled, dynamic=names is None)

    def _record(self, line: int, names: Optional[frozenset[str]], dynamic: bool = False) -> None:
        self.reads.append(Read(self.mod.name, self.mod.rel, line, names, dynamic))

    def _starred(self, node: ast.Starred, offset: int) -> Optional[ast.expr]:
        """星付きの引数の `offset` 番目の要素。"""
        seq = self._constant(node.value)
        return seq.elts[offset] if isinstance(seq, (ast.Tuple, ast.List)) and offset < len(seq.elts) else None

    def visit_Call(self, node: ast.Call) -> None:
        reader = self._reader_of(node)
        if reader is not None:
            self.called.add(id(node.func))
            given = _argument(node, reader)
            arg = self._starred(given, reader.index - node.args.index(given)) if isinstance(given, ast.Starred) else given
            if arg is not None:
                self._record_argument(node.lineno, arg, reader)
            elif given is not None or not self._has_default(node.func, reader):
                self._record(node.lineno, None)
        callee = self.full_name(node.func)
        if callee is not None and any(full.startswith(f"{callee}.") for full in self.conf.readers):
            self.handed.update(id(a) for a in [*node.args, *(k.value for k in node.keywords)])
        self.generic_visit(node)

    def _has_default(self, func: ast.expr, reader: Reader) -> bool:
        """呼び先が方針の名前の引数に既定値を持つ（既定値は定義したモジュールの読みに数える）。"""
        name = self.full_name(func)
        owner, _, attr = (name if name is not None else "").rpartition(".")
        if owner not in self.modules:  # メソッドは定義の側で既定値を数える
            return True
        defs = self.modules[owner].defs
        return attr in defs and _default(defs[attr], reader.arg) is not None

    # ---------------------------------------------------------------- 読み口と表の参照

    def _reference(self, node: ast.expr) -> None:
        name = self.full_name(node)
        owner = name.rpartition(".")[0] if name is not None else ""
        if name is None or owner == self.mod.name or self.mod.name in self.conf.whole:
            return
        is_reader = name in self.conf.readers and not _is_class(name) and id(node) not in self.called | self.handed
        if name in self.conf.tables or is_reader:
            self._record(node.lineno, None)

    def visit_Name(self, node: ast.Name) -> None:
        self._reference(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._reference(node)
        self.generic_visit(node)

    # ---------------------------------------------------------------- 囲み

    def _enter(self, node: ast.AST, name: str, params: frozenset[str], is_class: bool = False) -> None:
        owner = self.scopes[-1].name if self.scopes else self.mod.name
        full = f"{owner}.{name}"
        reader = self.conf.readers.get(full)
        default = _default(node, reader.arg) if reader is not None else None
        if reader is not None and default is not None:
            self._record_argument(default.lineno, default, reader)
        self.scopes.append(_Scope(node, full, params, is_class, self.mod.bindings(node)))
        self.generic_visit(node)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node, node.name, _params(node.args))

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter(node, node.name, _params(node.args))

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._enter(node, "<lambda>", _params(node.args))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter(node, node.name, NO_NAMES, True)


def _literal(node: Optional[ast.expr]) -> Optional[frozenset[str]]:
    return frozenset({node.value}) if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _union(parts: list[Optional[frozenset[str]]]) -> Optional[frozenset[str]]:
    """名前の集まりの和。1 つでも解けなければ None。"""
    known = [p for p in parts if p is not None]
    return frozenset().union(*known) if len(known) == len(parts) else None


def _is_class(full: str) -> bool:
    return full.rpartition(".")[2].lstrip("_")[:1].isupper()


def _argument(call: ast.Call, reader: Reader) -> Optional[ast.expr]:
    """呼び出しの、方針の名前の引数（名前で渡したもの・位置で渡したもの・星付き）。無ければ None。"""
    named = [k.value for k in call.keywords if k.arg == reader.arg]
    if named:
        return named[0]
    for i, arg in enumerate(call.args[: reader.index + 1]):
        if isinstance(arg, ast.Starred) or i == reader.index:
            return arg
    return None


def reads(trees: Mapping[str, ast.Module], conf: Settings) -> list[Read]:
    """方針を引く箇所のすべて。動的な読み口の指定に当たる箇所は、指定から組み立てた名前を引く。"""
    modules = {m.name: m for m in (_Module(rel, tree) for rel, tree in trees.items())}
    found: list[Read] = []
    for mod in modules.values():
        walker = _Walker(mod, modules, conf)
        walker.visit(mod.tree)
        found += walker.reads
    return found


def actual(trees: Mapping[str, ast.Module], conf: Settings) -> dict[str, frozenset[str]]:
    """方針の名前 → 引いているモジュールの名前。`trees` のキーはプラグインの根からの相対パス（`src/` の下の `.py`）。"""
    return {name: frozenset(mods) for name, mods in _by_policy(reads(trees, conf)).items()}


def _by_policy(found: list[Read]) -> defaultdict[str, set[str]]:
    """方針の名前 → 引いているモジュールの名前。"""
    out: defaultdict[str, set[str]] = defaultdict(set)
    for read in found:
        for name in read.names if read.names is not None else NO_NAMES:
            out[name].add(read.module)
    return out


def declared(raw: object) -> dict[str, frozenset[str]]:
    """上限の設定の中身から、方針ごとの指定された読み手のモジュールの名前。"""
    return {name: frozenset(policy.read_by) for name, policy in limits.validate(raw).items()}


def _line_of(text: str, name: str) -> int:
    """上限の設定の本文の、方針 `name` の行。"""
    return next((i for i, line in enumerate(text.splitlines(), 1) if f'"{name}":' in line), 1)


def _unread(scope: Scope, text: str, want: Mapping[str, frozenset[str]], found: list[Read]) -> list[Finding]:
    """指定した読み手が引いていない方針（`unread`）。"""
    got = _by_policy(found)
    path = scope.plugin_root / CONTRACTS / limits.NDF_SETTINGS
    missing = [(name, mod) for name, mods in want.items() for mod in sorted(mods) if mod not in got[name]]
    return [
        Finding(RULE, scope.rel(path), _line_of(text, name), f"unread: 方針 {name} の読み手 {mod} がこの方針を引いていない")
        for name, mod in missing
    ]


def _extra(scope: Scope, want: Mapping[str, frozenset[str]], found: list[Read]) -> list[Finding]:
    """指定の無い方針を引く箇所（`undeclared`。方針とモジュールの組ごとに最初の 1 件）と、解けない読み口（`unresolved`）。"""
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for read in sorted(found, key=lambda r: (r.rel, r.line)):
        path = scope.rel(scope.plugin_root / read.rel)
        if read.names is None:
            why = f"unresolved: {read.module} の読み口の方針の名前を解けない（rules.json の limits の readers か dynamic に指定する）"
            out.append(Finding(RULE, path, read.line, why))
            continue
        # 動的な読み口の語彙のうち、上限の設定に無い名前は引かれない（読み手は無いときの既定へ落とす）
        for name in sorted(read.names):
            declared_here = read.module in want[name] if name in want else read.dynamic
            if not declared_here and (name, read.module) not in seen:
                out.append(Finding(RULE, path, read.line, f"undeclared: 方針 {name} を引く {read.module} が方針の read_by に無い"))
            seen.add((name, read.module))
    return out


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    """指定と実際の食い違い（`unread`・`undeclared`・`unresolved`）。上限の設定を持たないプラグインには当てない。"""
    contracts_dir = scope.plugin_root / CONTRACTS
    if not (contracts_dir / limits.NDF_SETTINGS).is_file():
        return []
    # 指定と行番号の両方に使う本文を 1 回だけ読む
    text, raw = _contract(contracts_dir / limits.NDF_SETTINGS, "上限の設定を読めない")
    try:
        want = declared(raw)
    except limits.LimitsError as exc:
        raise CheckError(str(exc)) from exc
    conf = settings(rules, contracts_dir)
    trees = {scope.plugin_rel(src): src.tree for src in scope.code()}
    found = reads(trees, conf)
    return _unread(scope, text, want, found) + _extra(scope, want, found)
