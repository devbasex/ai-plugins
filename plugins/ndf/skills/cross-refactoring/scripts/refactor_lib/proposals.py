"""複数の CLI が出した提案を 1 つの一覧へまとめる。

語彙の検証・重複排除・優先度付けと、前のラウンドとの重複率の測定を持つ。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from . import info
from .gitfacts import _safe_int
from .vocabulary import (
    DEFAULT_SEVERITY_THRESHOLD,
    SEVERITY_ORDER,
    SMELLS,
    TECHNIQUES,
)


def _normalize_proposal(raw: dict[str, Any], source: str) -> Optional[dict[str, Any]]:
    """1 件の提案を正規化する。必須項目を欠くものは捨てる。

    語彙外の `smell` / `technique` は `unknown` として警告し、**最低の重要度へ
    降格**させる。しきい値で自動的に落ちるため、語彙を守らない提案が
    重複排除をすり抜けて残ることがない。
    """
    path = str(raw.get("path") or "").strip()
    symbol = str(raw.get("symbol") or "").strip()
    if not path or not symbol:
        info(f"⚠ {source}: path / symbol の無い提案を無視しました: {raw!r:.120}")
        return None

    smell = str(raw.get("smell") or "").strip()
    technique = str(raw.get("technique") or "").strip()
    severity = str(raw.get("severity") or "").strip().lower()
    degraded = False
    if smell not in SMELLS:
        info(f"⚠ {source}: 語彙外の兆候 `{smell}` — unknown へ降格 ({path}#{symbol})")
        smell = "unknown"
        degraded = True
    if technique not in TECHNIQUES:
        info(f"⚠ {source}: 語彙外の手法 `{technique}` — unknown へ降格 ({path}#{symbol})")
        technique = "unknown"
        degraded = True
    if severity not in SEVERITY_ORDER:
        info(f"⚠ {source}: 語彙外の重要度 `{severity}` — unknown へ降格 ({path}#{symbol})")
        severity = "unknown"
        degraded = True
    if degraded:
        severity = "unknown"

    estimated = _safe_int(raw.get("estimated_diff_lines"))

    return {
        "path": path,
        "symbol": symbol,
        "smell": smell,
        "technique": technique,
        "severity": severity,
        "rationale": str(raw.get("rationale") or "").strip(),
        "plan": str(raw.get("plan") or "").strip(),
        "test_gap": bool(raw.get("test_gap")),
        "estimated_diff_lines": max(estimated, 0),
        "proposed_by": [source],
    }


def _dedupe_key(item: dict[str, Any]) -> tuple[str, str, str]:
    """重複排除の鍵。同じ箇所への同じ兆候の指摘を 1 件へまとめる。"""
    return (item["path"], item["symbol"], item["smell"])


def _merge_one(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    """同一の鍵を持つ提案を統合する。

    `rationale` と `plan` は**最も具体的なもの**（長い方）を採る。重要度は高い方、
    推定差分行数は大きい方を採り、見積りを楽観側へ倒さない。
    """
    for source in incoming["proposed_by"]:
        if source not in existing["proposed_by"]:
            existing["proposed_by"].append(source)
    if len(incoming["rationale"]) > len(existing["rationale"]):
        existing["rationale"] = incoming["rationale"]
    if len(incoming["plan"]) > len(existing["plan"]):
        existing["plan"] = incoming["plan"]
    if SEVERITY_ORDER[incoming["severity"]] > SEVERITY_ORDER[existing["severity"]]:
        existing["severity"] = incoming["severity"]
        existing["technique"] = incoming["technique"]
    existing["test_gap"] = existing["test_gap"] or incoming["test_gap"]
    existing["estimated_diff_lines"] = max(
        existing["estimated_diff_lines"], incoming["estimated_diff_lines"]
    )


def merge_proposals(
    proposals: dict[str, list[dict[str, Any]]],
    threshold: str = DEFAULT_SEVERITY_THRESHOLD,
    max_items: int = 5,
    excluded_keys: Iterable[tuple[str, str, str]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """提案をマージして `(採用, 見送り)` を返す。

    優先度は「**合意したランタイム数 → 重要度 → 推定差分行数の昇順**」。
    小さく合意の多いものから直す。合意が多い提案は誤検知の確率が低く、
    小さい提案は失敗したときの取り消し範囲も小さい。

    `excluded_keys` には過去に見送った項目の鍵を渡す。見送った項目を毎ラウンド
    再提案されると収束しないため、対象外として落とす。
    """
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for source, items in proposals.items():
        for raw in items:
            norm = _normalize_proposal(raw, source)
            if norm is None:
                continue
            key = _dedupe_key(norm)
            if key in merged:
                _merge_one(merged[key], norm)
            else:
                merged[key] = norm

    excluded = set(excluded_keys)
    adopted: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    min_severity = SEVERITY_ORDER.get(threshold, SEVERITY_ORDER[DEFAULT_SEVERITY_THRESHOLD])

    for key, item in merged.items():
        if key in excluded:
            item["defer_reason"] = "過去のラウンドで見送った項目のため対象外"
            deferred.append(item)
        elif SEVERITY_ORDER[item["severity"]] < min_severity:
            item["defer_reason"] = f"重要度 {item['severity']} がしきい値 {threshold} 未満"
            deferred.append(item)
        else:
            adopted.append(item)

    adopted.sort(
        key=lambda i: (
            -len(i["proposed_by"]),
            -SEVERITY_ORDER[i["severity"]],
            i["estimated_diff_lines"],
            i["path"],
            i["symbol"],
        )
    )
    if len(adopted) > max_items:
        for item in adopted[max_items:]:
            item["defer_reason"] = f"1 ラウンドの採用上限 {max_items} 件を超えた"
        deferred.extend(adopted[max_items:])
        adopted = adopted[:max_items]
    return adopted, deferred


def duplicate_rate(
    current: Iterable[tuple[str, str, str]], previous: Iterable[tuple[str, str, str]]
) -> float:
    """前ラウンドの提案とどれだけ重なっているか。前ラウンドが空なら 0 を返す。"""
    prev = set(previous)
    cur = set(current)
    if not prev or not cur:
        return 0.0
    return len(cur & prev) / len(cur)
