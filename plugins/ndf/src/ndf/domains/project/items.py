"""項目の測定の結果の形（#3569）。

`{"status": "measured", "value": ...}`・`{"status": "question", "candidates": ..., "evidence": ...}`・
`{"status": "unknown", "reason": ...}` のどれかを返す。読んだ JSON の欠けたセクションを空として扱う `obj`・`seq` と、書いた値の指紋を作る
`value_digest`（設定の書き出しと解析の記録が共用する）も置く。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_EVIDENCE = 20
# 項目の測定の状態（`status` の値）
MEASURED, QUESTION, UNKNOWN = "measured", "question", "unknown"
STATUSES = (MEASURED, QUESTION, UNKNOWN)


def measured(value: Any) -> dict[str, Any]:
    return {"status": MEASURED, "value": value}


def question(candidates: Any, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": QUESTION, "candidates": candidates, "evidence": evidence[:MAX_EVIDENCE]}


def unknown(reason: str) -> dict[str, Any]:
    return {"status": UNKNOWN, "reason": reason}


def obj(value: Any) -> dict[str, Any]:
    """値が object ならそれ、ほかは空の object（読んだ JSON の欠けたセクションを空として扱う）。"""
    return value if isinstance(value, dict) else {}


def seq(value: Any) -> list[Any]:
    """値が配列ならそれ、ほかは空の配列。"""
    return value if isinstance(value, list) else []


def value_digest(value: Any) -> str:
    """値を正規化した JSON の SHA-256（書いた値の指紋）。"""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
