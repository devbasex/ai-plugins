"""値の形の判定と、値についての小さな部品。標準ライブラリだけを使い、hook のエントリポイントの import の閉包にも入る（contracts/hooks/imports.json の stdlib_only）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, Optional, TypeVar

_Key = TypeVar("_Key")
_Value = TypeVar("_Value")


def is_int(value: object) -> bool:
    """`value` が整数か（真偽値は整数に数えない）。"""
    return isinstance(value, int) and not isinstance(value, bool)


class LastOne(Generic[_Key, _Value]):
    """直前の 1 件だけ覚える計算のメモ。同じキー（同じオブジェクト）を続けて渡せば計算し直さない。"""

    def __init__(self, compute: Callable[[_Key], _Value]) -> None:
        self._compute = compute
        self._last: Optional[tuple[_Key, _Value]] = None

    def of(self, key: _Key) -> _Value:
        if self._last is None or self._last[0] is not key:
            self._last = (key, self._compute(key))
        return self._last[1]
