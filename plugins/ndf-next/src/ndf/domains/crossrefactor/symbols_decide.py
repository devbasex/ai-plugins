"""課題が触る関数を差分から引き、提案をその関数へ絞る（#622）。

`--scope` はファイル単位の範囲で、そのファイルの関数のうち課題が触ったものだけが提案の対象になる。
触った行を含む関数・メソッド（`Class.method`）・クラスの名前を、範囲の中のファイルごとに返す。

- Python は構文木で関数の行の範囲を読む。構文の誤りのファイルは `SymbolsError` で止め、ファイル単位へ倒さない
- 構文木の読み手の無い言語のファイルは `whole_files` に入れ、提案をファイル単位で通す（関数の範囲を読めない）
- 関数の外の行（モジュールの頭の import など）だけを触ったファイルは空の集合を持つ。そのファイルへの提案は範囲外になる
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ndf.domains.crossrefactor.diff_decide import FileChange, in_scope

# 見送りの理由（提案が課題の触る関数の外を指す）
OUT_OF_SCOPE = "out_of_scope"
PYTHON_SUFFIX = ".py"


class SymbolsError(ValueError):
    """触る関数を読めない（構文の誤り）。`path` は読めなかったファイル。"""

    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"{path}: {detail}")
        self.path = path


@dataclass(frozen=True)
class Symbol:
    name: str
    start: int
    end: int


@dataclass(frozen=True)
class Touched:
    """触る関数。`symbols` はファイル → 名前の集合、`whole_files` は関数の範囲を読めずファイル単位で扱うファイル。"""

    symbols: Mapping[str, frozenset[str]] = field(default_factory=dict)
    whole_files: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {"symbols": {p: sorted(s) for p, s in sorted(self.symbols.items())}, "whole_files": list(self.whole_files)}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Touched:
        """`as_json` の形から組み直す（ファイルの並びは保つ）。"""
        return cls({p: frozenset(n) for p, n in data["symbols"].items()}, tuple(data["whole_files"]))


_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _start(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> int:
    return min([node.lineno, *(d.lineno for d in node.decorator_list)])


def _walk(body: Iterable[ast.stmt], prefix: str) -> list[Symbol]:
    out: list[Symbol] = []
    for node in body:
        if not isinstance(node, _DEFS):
            continue
        name = f"{prefix}{node.name}"
        out.append(Symbol(name, _start(node), node.end_lineno or node.lineno))
        if isinstance(node, ast.ClassDef):
            out.extend(_walk(node.body, f"{name}."))
    return out


def python_symbols(path: str, source: str) -> tuple[Symbol, ...]:
    """モジュールの最上位の関数・クラスと、クラスの中のメソッド（入れ子のクラスを含む）。関数の中の関数は外の関数に数える。"""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise SymbolsError(path, f"構文木として読めない（{exc.msg}、{exc.lineno} 行目）") from exc
    return tuple(_walk(tree.body, ""))


def symbols_at(symbols: tuple[Symbol, ...], lines: Iterable[int]) -> frozenset[str]:
    """行ごとに、その行を含む最も狭い関数・クラスの名前を集める。どれにも入らない行は数えない。"""
    names: set[str] = set()
    for line in lines:
        holders = [s for s in symbols if s.start <= line <= s.end]
        if holders:
            names.add(min(holders, key=lambda s: s.end - s.start).name)
    return frozenset(names)


def touched_symbols(changes: Mapping[str, FileChange], sources: Mapping[str, str], scope: tuple[str, ...]) -> Touched:
    """範囲の中で変わったファイルごとの触る関数。`sources` は変更の後の Python のファイルの中身（消したファイルは持たない）。"""
    symbols: dict[str, frozenset[str]] = {}
    whole: list[str] = []
    for path, change in sorted(changes.items()):
        if change.removed or not in_scope(path, scope):
            continue
        if not path.endswith(PYTHON_SUFFIX):
            whole.append(path)
            continue
        if path not in sources:
            raise SymbolsError(path, "変更の後の中身が渡されていない")
        symbols[path] = symbols_at(python_symbols(path, sources[path]), change.lines)
    return Touched(symbols, tuple(whole))


def explicit(pairs: Iterable[str]) -> Touched:
    """明示の `--symbols <ファイル>:<名前>` の並びから触る関数を組む。"""
    symbols: dict[str, set[str]] = {}
    for pair in pairs:
        path, sep, name = pair.partition(":")
        if not sep or not path or not name:
            raise ValueError(f"--symbols は <ファイル>:<名前> の形にする: {pair!r}")
        symbols.setdefault(path, set()).add(name)
    return Touched({p: frozenset(n) for p, n in symbols.items()})


def _name_matches(proposed: str, touched: frozenset[str]) -> bool:
    for name in touched:
        if proposed == name or name.startswith(proposed + "."):
            return True
        if "." not in proposed and name.rsplit(".", 1)[-1] == proposed:
            return True
    return False


def in_touched(proposal: Mapping[str, Any], touched: Touched) -> bool:
    """提案（`path` と `symbol`）が触る関数を指すか。クラスへの提案は、そのクラスのメソッドを触ったときも当たる。"""
    path = str(proposal["path"])
    if path in touched.whole_files:
        return True
    if path not in touched.symbols:
        return False
    return _name_matches(str(proposal["symbol"]), touched.symbols[path])


def split_by_touched(
    proposals: Iterable[dict[str, Any]], touched: Touched
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    """提案を (触る関数を指すもの, [(範囲外の提案, `OUT_OF_SCOPE`)]) に分ける。触る関数が 1 つも無ければすべて範囲外。"""
    kept: list[dict[str, Any]] = []
    skipped: list[tuple[dict[str, Any], str]] = []
    for p in proposals:
        if in_touched(p, touched):
            kept.append(p)
        else:
            skipped.append((p, OUT_OF_SCOPE))
    return kept, skipped
