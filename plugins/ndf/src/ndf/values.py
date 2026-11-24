"""値の形の判定。標準ライブラリだけを使い、hook のエントリポイントの import の閉包にも入る（contracts/hooks/imports.json の stdlib_only）。"""

from __future__ import annotations


def is_int(value: object) -> bool:
    """`value` が整数か（真偽値は整数に数えない）。"""
    return isinstance(value, int) and not isinstance(value, bool)
