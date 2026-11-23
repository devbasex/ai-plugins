"""上限の設定の読み手の指定の規則（`limit-readers`）: 方針の `read_by` と、実際に方針を引くモジュールを突き合わせる。

方針を引く関数（読み口）と、名前を実行時に組み立てる読み手は `rules.json` の `limits` が持つ。

- `readers`: 読み口の関数の完全な名前 → 方針の名前の引数（名前と位置）。名前を引数で受けて読み口へ渡す関数・メソッド・クラスの
  欄・引数で受けた読み口（`<関数>.<引数>`）も読み口として並べ、呼び手を読み手に数える。`template` があれば、引数の値を `{}` に入れた名前を引く
- `tables`・`whole`: 方針の表を丸ごと返す名前と、表を受け取るだけで方針を引かないモジュール
- `dynamic`: 名前を実行時に組み立てる読み手。`vocabulary`（コントラクトのファイルと JSON Pointer。`*` は辞書か並びのすべて）の値を
  `template` の `{}` に、`*` が当たったキー（左から 1 番目が `{1}`）を `{1}` に入れた名前を引く
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ndf.checks import imports
from ndf.checks.model import CheckError, Finding, Scope, read_json
from ndf.checks.spec_model import CONTRACTS
from ndf.contracts import limits

RULE = "limit-readers"
NO_NAMES: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Reader:
    """読み口 1 つ。方針の名前の引数の名前と位置（メソッドは `self` を数えない）。"""

    arg: str
    index: int
    template: str = "{}"


@dataclass(frozen=True)
class Settings:
    """規則のデータ（`rules.json` の `limits`）。`dynamic` はモジュール → 組み立てうる方針の名前。"""

    readers: Mapping[str, Reader]
    tables: frozenset[str]
    whole: frozenset[str]
    dynamic: Mapping[str, frozenset[str]]


@dataclass(frozen=True)
class Read:
    """方針を引く箇所 1 つ。`names` が None なら名前を解けない。"""

    module: str
    rel: str
    line: int
    names: Optional[frozenset[str]]


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


def dynamic_names(row: Mapping[str, str], contracts_dir: Path) -> frozenset[str]:
    """`dynamic` の 1 行が組み立てうる方針の名前。語彙の値のうち文字列でないもの（null など）は名前を作らない。"""
    rel, _, pointer = row["vocabulary"].partition("#")
    data = read_json(contracts_dir / rel, "動的な読み口の語彙を読めない")
    try:
        values = list(_pointed(data, pointer.split("/")[1:]))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise CheckError(f"動的な読み口の語彙を引けない: {row['vocabulary']}: {exc}") from exc
    return frozenset(_filled(row["template"], value, keys) for value, keys in values if isinstance(value, str))


def settings(rules: dict[str, Any], contracts_dir: Path) -> Settings:
    """`rules.json` の `limits` を読み、動的な読み口の名前をコントラクトのファイルの語彙から組み立てる。"""
    conf = rules["limits"]
    dynamic: dict[str, frozenset[str]] = defaultdict(frozenset)
    for row in conf["dynamic"]:
        dynamic[row["module"]] |= dynamic_names(row, contracts_dir)
    readers = {name: Reader(**row) for name, row in conf["readers"].items()}
    whole = frozenset(row["module"] for row in conf["whole"])
    return Settings(readers, frozenset(conf["tables"]), whole, dict(dynamic))


class _Module:
    """モジュール 1 つの、名前を解くための表。"""

    def __init__(self, rel: str, tree: ast.Module) -> None:
        self.rel, self.tree = rel, tree
        parts = Path(rel).with_suffix("").parts[1:]
        is_package = parts[-1] == "__init__"
        self.name = ".".join(parts[:-1] if is_package else parts)
        self.imported = imports.bindings(tree, self.name, is_package)
        self.consts: dict[str, ast.expr] = {}
        self.defs: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.defs.add(node.name)
            elif isinstance(node, ast.Assign):
                self.consts.update((t.id, node.value) for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name):
                self.consts[node.target.id] = node.value


@dataclass(frozen=True)
class _Scope:
    """関数かクラスの 1 つの囲み。`params` は関数の引数。"""

    node: ast.AST
    name: str
    params: frozenset[str]
    is_class: bool = False


def _params(args: ast.arguments) -> frozenset[str]:
    rest = [a for a in (args.vararg, args.kwarg) if a is not None]
    return frozenset(a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, *rest])


def _default(node: ast.AST, arg: str) -> Optional[ast.expr]:
    """関数の引数かクラスの欄 `arg` の既定値。"""
    if isinstance(node, ast.ClassDef):
        fields = [n for n in node.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == arg]
        return fields[0].value if fields else None
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    positional = [*node.args.posonlyargs, *node.args.args]
    defaults = dict(zip(reversed([a.arg for a in positional]), reversed(node.args.defaults)))
    defaults.update({a.arg: d for a, d in zip(node.args.kwonlyargs, node.args.kw_defaults) if d is not None})
    return defaults.get(arg)


def _bound_to(node: ast.AST, name: str) -> Iterator[tuple[ast.expr, bool]]:
    """関数の中で `name` に入る式と、その式の要素 1 つずつ（`for` の変数）かどうか。"""
    for n in ast.walk(node):
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets):
            yield n.value, False
        elif isinstance(n, (ast.For, ast.comprehension)) and isinstance(n.target, ast.Name) and n.target.id == name:
            yield n.iter, True


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

    def _param_scope(self, name: str) -> Optional[_Scope]:
        return next((s for s in reversed(self.scopes) if name in s.params), None)

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
        if node.id in self.mod.imported:
            return self._canonical(self.mod.imported[node.id])
        return f"{self.mod.name}.{node.id}" if node.id in self.mod.defs or node.id in self.mod.consts else None

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
        functions = [s for s in self.scopes if not s.is_class]
        if not functions or self._param_scope(name) is not None:
            return []
        return list(_bound_to(functions[0].node, name))

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

    # ---------------------------------------------------------------- 読み口の呼び出し

    def _reader_of(self, call: ast.Call) -> Optional[Reader]:
        name = self.full_name(call.func)
        if name is not None and name in self.conf.readers:
            return self.conf.readers[name]
        # 受け手の型を決められないメソッドの呼び出しは、方針の名前を渡したものだけをメソッドの名前で数える
        known = name is not None and name.rpartition(".")[0] in self.modules
        attr = call.func.attr if isinstance(call.func, ast.Attribute) and not known else None
        methods = [r for full, r in self.conf.readers.items() if _is_method(full) and full.rpartition(".")[2] == attr]
        return methods[0] if methods and _argument(call, methods[0]) is not None else None

    def _passed_through(self, node: ast.expr) -> bool:
        """式が、読み口として並べた関数の引数かクラスの欄から来る（呼び手を読み手に数えるので、ここでは数えない）。"""
        for n in ast.walk(node):
            if isinstance(n, ast.Name):
                scope = self._param_scope(n.id)
                reader = self.conf.readers.get(scope.name) if scope is not None else None
                if reader is not None and reader.arg == n.id:
                    return True
            if isinstance(n, ast.Attribute) and any(
                r.arg == n.attr and full.rpartition(".")[0] == self.mod.name and _is_class(full) for full, r in self.conf.readers.items()
            ):
                return True
        return False

    def _names(self, node: ast.expr, reader: Reader) -> Optional[frozenset[str]]:
        if self._passed_through(node):
            return NO_NAMES
        names = self.names_of(node)
        return None if names is None else frozenset(reader.template.replace("{}", n) for n in names)

    def _record(self, line: int, names: Optional[frozenset[str]]) -> None:
        self.reads.append(Read(self.mod.name, self.mod.rel, line, names))

    def _starred(self, node: ast.Starred, offset: int) -> Optional[ast.expr]:
        """星付きの引数の `offset` 番目の要素。"""
        seq = self._constant(node.value)
        return seq.elts[offset] if isinstance(seq, (ast.Tuple, ast.List)) and offset < len(seq.elts) else None

    def visit_Call(self, node: ast.Call) -> None:
        reader = self._reader_of(node)
        if reader is not None:
            self.called.add(id(node.func))
            arg = _argument(node, reader)
            if isinstance(arg, ast.Starred):
                arg = self._starred(arg, reader.index - node.args.index(arg))
                self._record(node.lineno, None if arg is None else self._names(arg, reader))
            elif arg is not None:
                self._record(node.lineno, self._names(arg, reader))
            elif not self._has_default(node.func, reader):
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
        defs = [n for n in self.modules[owner].tree.body if getattr(n, "name", None) == attr]
        return any(_default(n, reader.arg) is not None for n in defs)

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
            self._record(default.lineno, self._names(default, reader))
        self.scopes.append(_Scope(node, full, params, is_class))
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


def _is_method(full: str) -> bool:
    return _is_class(full.rpartition(".")[0])


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
    """方針を引く箇所のすべて。動的な読み口を指定したモジュールの解けない箇所は、指定から組み立てた名前を引く。"""
    modules = {m.name: m for m in (_Module(rel, tree) for rel, tree in trees.items())}
    found = []
    for mod in modules.values():
        walker = _Walker(mod, modules, conf)
        walker.visit(mod.tree)
        found += walker.reads
    return [Read(r.module, r.rel, r.line, conf.dynamic.get(r.module) if r.names is None else r.names) for r in found]


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


def declared(contracts_dir: Path) -> dict[str, frozenset[str]]:
    """上限の設定の方針ごとの、指定された読み手のモジュールの名前。"""
    raw = read_json(contracts_dir / limits.NDF_SETTINGS, "上限の設定を読めない")
    return {name: frozenset(policy.read_by) for name, policy in limits.validate(raw).items()}


def _line_of(text: str, name: str) -> int:
    """上限の設定の本文の、方針 `name` の行。"""
    return next((i for i, line in enumerate(text.splitlines(), 1) if f'"{name}":' in line), 1)


def _unread(scope: Scope, want: Mapping[str, frozenset[str]], found: list[Read]) -> list[Finding]:
    """指定した読み手が引いていない方針（`unread`）。"""
    got = _by_policy(found)
    path = scope.plugin_root / CONTRACTS / limits.NDF_SETTINGS
    text = path.read_text(encoding="utf-8")
    missing = [(name, mod) for name, mods in want.items() for mod in sorted(mods) if mod not in got[name]]
    return [
        Finding(RULE, scope.rel(path), _line_of(text, name), f"unread: 方針 {name} の読み手 {mod} がこの方針を引いていない")
        for name, mod in missing
    ]


def _extra(scope: Scope, want: Mapping[str, frozenset[str]], found: list[Read], conf: Settings) -> list[Finding]:
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
        dynamic = conf.dynamic[read.module] if read.module in conf.dynamic else NO_NAMES
        for name in sorted(read.names):
            declared_here = read.module in want[name] if name in want else name in dynamic
            if not declared_here and (name, read.module) not in seen:
                out.append(Finding(RULE, path, read.line, f"undeclared: 方針 {name} を引く {read.module} が方針の read_by に無い"))
            seen.add((name, read.module))
    return out


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    """指定と実際の食い違い（`unread`・`undeclared`・`unresolved`）。上限の設定を持たないプラグインには当てない。"""
    contracts_dir = scope.plugin_root / CONTRACTS
    if not (contracts_dir / limits.NDF_SETTINGS).is_file():
        return []
    try:
        want = declared(contracts_dir)
    except limits.LimitsError as exc:
        raise CheckError(str(exc)) from exc
    conf = settings(rules, contracts_dir)
    trees = {scope.plugin_rel(src): src.tree for src in scope.code()}
    found = reads(trees, conf)
    return _unread(scope, want, found) + _extra(scope, want, found, conf)
