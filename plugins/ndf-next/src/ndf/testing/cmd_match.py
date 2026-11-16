"""コマンドの文字列を語の並びで比べる（#2130）。

`shlex.quote` は全角の文字や空白を含むパスだけを引用で包むため、組み立てたコマンドの文字列を期待の文字列と
そのまま比べると、置き場のパスの形で結果が変わる。語の並びへ分けてから比べる。
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence


def same_cmd(actual: str, expected: Sequence[str]) -> bool:
    """`actual`（シェルのコマンドの文字列）を分けた語の並びが `expected` と同じか。"""
    try:
        return shlex.split(actual) == list(expected)
    except ValueError:  # noqa: NDF401 利用者の入力: 引用の閉じないコマンドの文字列は期待と一致しないとして扱う
        return False
