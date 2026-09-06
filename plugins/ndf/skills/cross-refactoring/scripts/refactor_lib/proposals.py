"""複数の CLI が出した提案を 1 つの一覧へまとめる。

語彙の検証・重複排除・優先度付けと、前のラウンドとの重複率の測定を持つ。
**構造改善の提案とテスト整備の提案の両方**を扱い、採否の詰めは 1 つの関数
（`_select`）が持つ。違うのは正規化と優先度の付け方だけである。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

from . import info
from .gitfacts import _safe_int
from .rounds import TEST, item_key
from .vocabulary import (
    DEFAULT_SEVERITY_THRESHOLD,
    SEVERITY_ORDER,
    SMELLS,
    TECHNIQUES,
    TEST_CASES,
    TEST_LEVELS,
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


def _dedupe_key(item: dict[str, Any]) -> tuple[str, ...]:
    """重複排除の鍵。同じ箇所への同じ兆候の指摘を 1 件へまとめる。

    種類ごとの鍵は `rounds.item_key` が持つ。対象外の判定と同じ鍵を使わないと、
    見送った項目が別物として再び採用される。
    """
    return item_key(item)


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
    excluded_keys: Iterable[tuple[str, ...]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """提案をマージして `(採用, 見送り)` を返す。

    優先度は「**合意したランタイム数 → 重要度 → 推定差分行数の昇順**」。
    小さく合意の多いものから直す。合意が多い提案は誤検知の確率が低く、
    小さい提案は失敗したときの取り消し範囲も小さい。

    `excluded_keys` には過去に見送った項目の鍵を渡す。見送った項目を毎ラウンド
    再提案されると収束しないため、対象外として落とす。
    """
    merged: dict[tuple[str, ...], dict[str, Any]] = {}
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

    min_severity = SEVERITY_ORDER.get(
        threshold, SEVERITY_ORDER[DEFAULT_SEVERITY_THRESHOLD])

    def reject(item: dict[str, Any]) -> Optional[str]:
        if SEVERITY_ORDER[item["severity"]] < min_severity:
            return f"重要度 {item['severity']} がしきい値 {threshold} 未満"
        return None

    return _select(
        merged,
        excluded_keys=excluded_keys,
        reject=reject,
        sort_key=lambda i: (
            -len(i["proposed_by"]),
            -SEVERITY_ORDER[i["severity"]],
            i["estimated_diff_lines"],
            i["path"],
            i["symbol"],
        ),
        max_items=max_items,
    )


def _select(
    merged: dict[tuple[str, ...], dict[str, Any]],
    *,
    excluded_keys: Iterable[tuple[str, ...]],
    reject: Callable[[dict[str, Any]], Optional[str]],
    sort_key: Callable[[dict[str, Any]], Any],
    max_items: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """統合済みの提案から `(採用, 見送り)` を決める。**種類で分けない。**

    対象外・採否の理由・採用上限の扱いは、構造改善の提案とテスト整備の提案で
    同じである。片方だけ直されて基準が食い違わないよう 1 箇所に置く。
    """
    excluded = set(excluded_keys)
    adopted: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for key, item in merged.items():
        if key in excluded:
            item["defer_reason"] = "過去のラウンドで見送った項目のため対象外"
            deferred.append(item)
            continue
        reason = reject(item)
        if reason:
            item["defer_reason"] = reason
            deferred.append(item)
            continue
        adopted.append(item)

    adopted.sort(key=sort_key)
    if len(adopted) > max_items:
        for item in adopted[max_items:]:
            item["defer_reason"] = f"1 ラウンドの採用上限 {max_items} 件を超えた"
        deferred.extend(adopted[max_items:])
        adopted = adopted[:max_items]
    return adopted, deferred


def _normalize_test_proposal(
    raw: dict[str, Any], source: str
) -> Optional[dict[str, Any]]:
    """テスト整備の提案 1 件を正規化する。必須項目を欠くものは捨てる。

    **語彙外の値は降格する**（構造改善の提案と同じ扱い）。テスト項目には重要度が
    無いため、降格先は `unknown` の `case` / `level` そのもので、採否の段で対象外へ
    落とす。経路を自由文で書かせると、同じ経路が 3 者から別の言い回しで出て重複
    排除が効かない。
    """
    path = str(raw.get("path") or "").strip()
    target = str(raw.get("target") or "").strip()
    if not path or not target:
        info(f"⚠ {source}: path / target の無いテスト項目を無視しました: {raw!r:.120}")
        return None

    case = str(raw.get("case") or "").strip().lower()
    level = str(raw.get("level") or "").strip().lower()
    if case not in TEST_CASES:
        info(f"⚠ {source}: 語彙外の経路 `{case}` — unknown へ降格 ({target})")
        case = "unknown"
    if level not in TEST_LEVELS:
        info(f"⚠ {source}: 語彙外の階層 `{level}` — unknown へ降格 ({target})")
        level = "unknown"

    return {
        "kind": TEST,
        "path": path,
        "target": target,
        "case": case,
        "level": level,
        "rationale": str(raw.get("rationale") or "").strip(),
        "plan": str(raw.get("plan") or "").strip(),
        # テスト整備では見積を求めない。現状固定テストは分岐ごとに書くため、
        # 提案の時点で行数を当てられない。差分予算の検査は 0 のとき働かない。
        "estimated_diff_lines": 0,
        "test_gap": False,
        "proposed_by": [source],
    }


def _level_rank(level: str) -> int:
    """階層の低い順の順位。語彙外（`unknown`）は最後に置く。"""
    order = list(TEST_LEVELS)
    return order.index(level) if level in order else len(order)


def _merge_test_one(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    """同一の鍵を持つテスト項目を統合する。

    `rationale` と `plan` は最も具体的なもの（長い方）を採る。**階層は低い方を
    採る**（「上の階層へ持ち上げない」）。語彙外の階層は最後に置くため、片方が
    語彙の値を持っていればそちらへ寄る。
    """
    for source in incoming["proposed_by"]:
        if source not in existing["proposed_by"]:
            existing["proposed_by"].append(source)
    if len(incoming["rationale"]) > len(existing["rationale"]):
        existing["rationale"] = incoming["rationale"]
    if len(incoming["plan"]) > len(existing["plan"]):
        existing["plan"] = incoming["plan"]
    if _level_rank(incoming["level"]) < _level_rank(existing["level"]):
        existing["level"] = incoming["level"]


def merge_test_proposals(
    proposals: dict[str, list[dict[str, Any]]],
    max_items: int = 5,
    excluded_keys: Iterable[tuple[str, ...]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """テスト整備の提案をマージして `(採用, 見送り)` を返す。

    優先度は「**合意したランタイム数 → 入口 → 経路の種類**」。重要度と見積を
    持たないため、構造改善の 2 つ目以降の軸をここでは名前の順で埋める。

    採否の詰めは `merge_proposals` と同じ `_select` が行う。
    """
    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    for source, items in proposals.items():
        for raw in items:
            norm = _normalize_test_proposal(raw, source)
            if norm is None:
                continue
            key = _dedupe_key(norm)
            if key in merged:
                _merge_test_one(merged[key], norm)
            else:
                merged[key] = norm

    def reject(item: dict[str, Any]) -> Optional[str]:
        if item["case"] == "unknown" or item["level"] == "unknown":
            return (
                "語彙外の値を含むため対象外"
                "（`case` と `level` は列挙した識別子のいずれかで書く）"
            )
        return None

    return _select(
        merged,
        excluded_keys=excluded_keys,
        reject=reject,
        sort_key=lambda i: (-len(i["proposed_by"]), i["target"], i["case"]),
        max_items=max_items,
    )


def assign_apply_rounds(
    adopted: list[dict[str, Any]]
) -> list[list[dict[str, Any]]]:
    """採用した項目を、**書き換えるファイルが重ならない群**（適用ラウンド）へ分ける。

    群の中の項目は**互いに独立している**（触るファイルが重ならない）ため、まとめて
    1 コミットへ入れられる。群と群は同じファイルを直列に書き換えるため、**順序に
    依存する**。後続の群は先行の群を適用した後の作業ツリーを読む。

    分ける基準は**書き換えるファイルの一致**だけである。行の範囲は見ない。提案の
    時点で行の範囲は分からず、見積を求めると提案の負荷が上がる。**見積の精度に
    依存する分け方は、外れたときに取り消しが分離できない状態へ戻る**（決定 1）。

    **群の数に上限は置かない。** 重なり方が決める。1 ラウンドの採用件数
    （`--max-items-per-round`）が実質の上限になる。

    `adopted` は `merge_proposals` が採否の優先度（合意した数 → 重要度 →
    推定差分行数）で並べた順で渡す。**その順を崩さない**ため、群の順序も
    同じ優先度に従う。
    """
    groups: list[list[dict[str, Any]]] = []
    taken: list[set[str]] = []          # 群ごとの「その群が触るファイル」
    for item in adopted:
        path = item["path"]
        for paths, group in zip(taken, groups):
            if path in paths:
                continue
            paths.add(path)
            group.append(item)
            break
        else:
            groups.append([item])
            taken.append({path})
    return groups


def duplicate_rate(
    current: Iterable[tuple[str, str, str]], previous: Iterable[tuple[str, str, str]]
) -> float:
    """前ラウンドの提案とどれだけ重なっているか。前ラウンドが空なら 0 を返す。"""
    prev = set(previous)
    cur = set(current)
    if not prev or not cur:
        return 0.0
    return len(cur & prev) / len(cur)
