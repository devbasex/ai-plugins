"""JSON の値を鍵の並び（JSON Pointer を `/` で分けて戻した鍵）でたどる共通部品。

`scripts/check-ndf-choices.py` と `scripts/check-skill-repo-assumptions.py` が同じたどり方を使う。標準ライブラリだけで書く。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def walk(doc: Any, keys: Iterable[str]) -> Any:
    """`doc` を `keys` の順にたどった先の値。配列は 10 進の添字で引く。先が無ければ `LookupError`。"""
    node = doc
    for key in keys:
        if isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        elif isinstance(node, dict) and key in node:
            node = node[key]
        else:
            raise LookupError(key)
    return node
