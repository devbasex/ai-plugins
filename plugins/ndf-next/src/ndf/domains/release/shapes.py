"""GitHub の応答やプランなど、形の決まらない JSON の値を読む小さな補助（リリースの領域で共有する）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def list_of(raw: Mapping[str, Any], key: str) -> list[Any]:
    """`raw[key]` が配列ならその配列そのもの、ほか（鍵が無いを含む）は空の配列。要素は変換しない。"""
    got = raw.get(key)
    return got if isinstance(got, list) else []
