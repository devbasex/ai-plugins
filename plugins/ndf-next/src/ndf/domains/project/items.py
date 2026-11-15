"""項目の測定の結果の形（#3569）。

`{"status": "measured", "value": ...}`・`{"status": "question", "candidates": ..., "evidence": ...}`・
`{"status": "unknown", "reason": ...}` のどれかを返す。読んだ JSON の欠けた節を空として扱う `obj`・`seq` も置く。
"""

from __future__ import annotations

from typing import Any

MAX_EVIDENCE = 20


def measured(value: Any) -> dict[str, Any]:
    return {"status": "measured", "value": value}


def question(candidates: Any, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": "question", "candidates": candidates, "evidence": evidence[:MAX_EVIDENCE]}


def unknown(reason: str) -> dict[str, Any]:
    return {"status": "unknown", "reason": reason}


def obj(value: Any) -> dict[str, Any]:
    """値が object ならそれ、ほかは空の object（読んだ JSON の欠けた節を空として扱う）。"""
    return value if isinstance(value, dict) else {}


def seq(value: Any) -> list[Any]:
    """値が配列ならそれ、ほかは空の配列。"""
    return value if isinstance(value, list) else []
