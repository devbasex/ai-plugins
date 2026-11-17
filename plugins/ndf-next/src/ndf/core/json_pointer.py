"""JSON Pointer（RFC 6901）の分解・書き出し・たどり方。

宣言の読み書き（`has`・`get`・`put`・`remove`）は object だけをたどる。配列の添字もたどるのは `resolve` だけで、
版数のファイルとセクションの値を引く側（`branch_strategy.get`・`release.land.store_version`）が使う。
"""

from __future__ import annotations

from typing import Any, Iterable


def tokens(pointer: str) -> list[str]:
    """JSON Pointer を参照の並びへ分ける（`~1` → `/`・`~0` → `~`）。"""
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer.split("/")[1:]]


def escape(key: Any) -> str:
    """JSON Pointer の参照 1 つを書き出す（`~` → `~0`・`/` → `~1`）。"""
    return str(key).replace("~", "~0").replace("/", "~1")


def join(parts: Iterable[Any]) -> str:
    """参照の並びを JSON Pointer へ書き出す（空の並びは空の文字列）。"""
    return "".join("/" + escape(p) for p in parts)


# `has` が「無い」を値の null と分けるための印
_MISSING = object()


def has(doc: Any, pointer: str) -> bool:
    """object だけをたどって、キーが在るか（値が null でも在る）。"""
    return get(doc, pointer, _MISSING) is not _MISSING


def get(doc: Any, pointer: str, default: Any = None) -> Any:
    """object だけをたどった値。途中が無い・object でなければ `default`。"""
    cur = doc
    for t in tokens(pointer):
        if not isinstance(cur, dict) or t not in cur:
            return default
        cur = cur[t]
    return cur


def put(doc: dict, pointer: str, value: Any) -> None:
    """JSON Pointer の位置へ値を置く（途中の object は作る）。"""
    toks = tokens(pointer)
    cur = doc
    for t in toks[:-1]:
        cur = cur.setdefault(t, {})
    cur[toks[-1]] = value


def remove(doc: dict, pointer: str) -> None:
    """object だけをたどって、キーを消す。途中が無い・object でなければ何もしない。"""
    toks = tokens(pointer)
    cur: Any = doc
    for t in toks[:-1]:
        if not isinstance(cur, dict) or t not in cur:
            return
        cur = cur[t]
    if isinstance(cur, dict):
        cur.pop(toks[-1], None)


def resolve(doc: Any, pointer: str) -> tuple[bool, Any]:
    """object のキーと配列の添字をたどる。`(見つかったか, 値)` を返す。"""
    cur: Any = doc
    for tok in tokens(pointer):
        if isinstance(cur, dict) and tok in cur:
            cur = cur[tok]
        elif isinstance(cur, list) and tok.isdigit() and int(tok) < len(cur):
            cur = cur[int(tok)]
        else:
            return False, None
    return True, cur
