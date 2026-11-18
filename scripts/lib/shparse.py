"""シェルの構文の解析の包み（#1142 の決定 19・種類 8）。tree-sitter-bash を呼ぶのはこのモジュールだけである。

11.0.0 の入れ替え（#3304）で 10 系のプラグインから根へ移した。根の検査（`check-skill-shell-vars.py`）が使う
構文木の組み立て（`parse_bash`）と字面の取り出し（`node_text`）だけを持つ。

使う側は `ndf_wrappers.require("shparse")` を先に呼ぶ。
"""

from __future__ import annotations

from functools import lru_cache

import tree_sitter as ts
import tree_sitter_bash as tsb

Node = ts.Node


@lru_cache(maxsize=1)
def _bash_parser() -> ts.Parser:
    return ts.Parser(ts.Language(tsb.language()))


def parse_bash(cmd: str) -> Node:
    """コマンドの文字列の構文木の根（`program`）。"""
    return _bash_parser().parse(cmd.encode("utf-8")).root_node


def node_text(n: Node) -> str:
    return n.text.decode("utf-8", errors="replace") if n.text is not None else ""
