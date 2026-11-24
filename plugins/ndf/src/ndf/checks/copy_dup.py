"""規則 `copy-dup`: コピーで生まれた重複を、ファイルをまたぐ関数の名前と文字列の一致で数える。

- 2 つ以上のファイルの最上位に同じ名前の関数がある（名前ごとに 1 件。`_` で始まる関数も数える）
- 4 つ以上のファイルに同じ文字列が出る（文字列ごとに 1 件。docstring を除き、f 文字列の文字の部分を含む）

ファイルの数・文字列の長さ・同名を認める関数の名前は `rules.json` の `copy_dup` が持つ。今の件数を基準にしたラチェット
（`ratchet`）が、この規則の合否を決める。
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from ndf.checks.model import Finding, Scope, string_constants

RULE = "copy-dup"
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
# 名前か文字列 → それを持つファイル → 最初の行
Places = Mapping[str, Mapping[str, int]]


def _places(found: Iterable[tuple[str, str, int]]) -> Places:
    """（名前か文字列・ファイル・行）の並びを、ファイルごとの最初の行にまとめる。"""
    places: dict[str, dict[str, int]] = defaultdict(dict)
    for key, rel, line in found:
        places[key][rel] = min(line, places[key].setdefault(rel, line))
    return places


def _findings(places: Places, min_files: int, what: str) -> list[Finding]:
    """`min_files` 以上のファイルが持つものを、辞書順の先頭のファイルの指摘にする。"""
    return [
        Finding(
            RULE,
            min(files),
            files[min(files)],
            f"{what} {key!r} が {len(files)} ファイルにある: {', '.join(sorted(files))}",
            f"{what} {key}",
        )
        for key, files in places.items()
        if len(files) >= min_files
    ]


def top_functions(trees: Mapping[str, ast.Module], conf: Mapping[str, Any]) -> list[Finding]:
    """2 つ以上のファイルに同じ名前の最上位の関数がある組。"""
    named = [(node.name, rel, node.lineno) for rel, tree in trees.items() for node in tree.body if isinstance(node, FUNCTIONS)]
    return _findings(_places(n for n in named if n[0] not in conf["allow_names"]), 2, "同じ名前の関数")


def repeated_strings(trees: Mapping[str, ast.Module], conf: Mapping[str, Any]) -> list[Finding]:
    """`min_files` 以上のファイルに出る、決めた長さの文字列。"""
    found = [(text, rel, line) for rel, tree in trees.items() for line, text in string_constants(tree)]
    counted = (row for row in found if conf["min_chars"] <= len(row[0]) <= conf["max_chars"])
    return _findings(_places(counted), conf["min_files"], "同じ文字列")


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    """製品のコードの、ファイルをまたぐ重複を並べる。"""
    trees = {src.rel: src.tree for src in scope.code()}
    return top_functions(trees, rules["copy_dup"]) + repeated_strings(trees, rules["copy_dup"])
