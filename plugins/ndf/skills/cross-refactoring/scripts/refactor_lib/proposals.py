"""複数の CLI が出した提案を、改修計画へ渡す候補へまとめる（#933）。

語彙の検証・鍵が同じ提案の機械的な統合・しきい値・候補の切り出し（`path` + `symbol`
の組を上位 30 組、組の中は上位 3 件）を持つ。**意味の上で同じ提案かの判断はここで
しない**（改修計画の中で Jev か実装担当が行う）。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from . import info
from .gitfacts import safe_int
from .items import group_key, item_key
from .vocabulary import (
    CANDIDATE_GROUPS,
    CANDIDATES_PER_GROUP,
    DEFAULT_SEVERITY_THRESHOLD,
    DEFER_RANK,
    DEFER_THRESHOLD,
    DEFER_VOCABULARY,
    SEVERITY_ORDER,
    SMELLS,
    TECHNIQUES,
)


def _degrade_if_unknown(
    value: str,
    allowed: Iterable[str],
    source: str,
    label: str,
    location: str,
) -> tuple[str, bool]:
    """語彙集合に含まれない値を `unknown` へ降格する。

    降格したときは警告を出し `(unknown, True)` を返す。含まれていれば値をそのまま
    `(value, False)` で返す。`smell` / `technique` / `severity` の同じ降格ルールと、
    テスト提案の `case` / `level` の降格を 1 箇所に集め、警告文や降格処理の変更が
    散らばらないようにする。`location` は警告に添える位置表記で、構造改善側は
    `path#symbol`、テスト側は `target` を渡す。
    """
    if value not in allowed:
        info(f"⚠ {source}: 語彙外の{label} `{value}` — unknown へ降格 ({location})")
        return "unknown", True
    return value, False


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
    smell, smell_degraded = _degrade_if_unknown(
        smell, SMELLS, source, "兆候", f"{path}#{symbol}")
    technique, technique_degraded = _degrade_if_unknown(
        technique, TECHNIQUES, source, "手法", f"{path}#{symbol}")
    severity, severity_degraded = _degrade_if_unknown(
        severity, SEVERITY_ORDER, source, "重要度", f"{path}#{symbol}")
    degraded = smell_degraded or technique_degraded or severity_degraded
    if degraded:
        severity = "unknown"

    estimated = safe_int(raw.get("estimated_diff_lines"))

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
        # 語彙外を含んだ提案。見送りの理由を `vocabulary` と `threshold` で分けるため
        # に残す（AC9）。
        "degraded": degraded,
    }


def _merge_common_attributes(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    """共通の提案属性（proposed_by, rationale, plan）をマージする。"""
    for source in incoming["proposed_by"]:
        if source not in existing["proposed_by"]:
            existing["proposed_by"].append(source)
    if len(incoming["rationale"]) > len(existing["rationale"]):
        existing["rationale"] = incoming["rationale"]
    if len(incoming["plan"]) > len(existing["plan"]):
        existing["plan"] = incoming["plan"]


def _merge_one(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    """同一の鍵を持つ提案を統合する。

    `rationale` と `plan` は**最も具体的なもの**（長い方）を採る。重要度は高い方、
    推定差分行数は大きい方を採り、見積りを楽観側へ倒さない。
    """
    _merge_common_attributes(existing, incoming)
    if SEVERITY_ORDER[incoming["severity"]] > SEVERITY_ORDER[existing["severity"]]:
        existing["severity"] = incoming["severity"]
        existing["technique"] = incoming["technique"]
    existing["test_gap"] = existing["test_gap"] or incoming["test_gap"]
    existing["degraded"] = existing["degraded"] and incoming["degraded"]
    existing["estimated_diff_lines"] = max(
        existing["estimated_diff_lines"], incoming["estimated_diff_lines"]
    )


def _merge_by_key(
    proposals: dict[str, list[dict[str, Any]]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """提案を正規化し、鍵（`path` + `symbol` + `smell`）ごとに統合する。"""
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for source, items in proposals.items():
        for raw in items:
            norm = _normalize_proposal(raw, source)
            if norm is None:
                continue
            key = item_key(norm)
            if key in merged:
                _merge_one(merged[key], norm)
            else:
                merged[key] = norm
    return merged


def order_key(item: dict[str, Any]) -> tuple:
    """候補の並び。(賛同した者の数, 重要度) の降順、同じなら `path` / `symbol` / `smell`。"""
    return (
        -len(item["proposed_by"]),
        -SEVERITY_ORDER[item["severity"]],
        item["path"], item["symbol"], item["smell"],
    )


def build_candidates(
    proposals: dict[str, list[dict[str, Any]]],
    threshold: str = DEFAULT_SEVERITY_THRESHOLD,
    groups: int = CANDIDATE_GROUPS,
    per_group: int = CANDIDATES_PER_GROUP,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    """提案から `(候補, [(見送る提案, 理由)])` を返す（決定 17）。

    1. 鍵が同じ提案を 1 件へまとめる（賛同した者を足し合わせる）
    2. 語彙外を含む提案は `vocabulary`、しきい値未満は `threshold` で見送る
    3. 残りを (賛同した者の数, 重要度) の降順に並べ、`path` + `symbol` の組の単位で
       上位 `groups` 組を取る。取った組の中は上位 `per_group` 件まで渡す
    4. 外れた組の提案と、組の中の `per_group` 件目より後は `rank` で見送る

    **件数ではなく組で切る。** 件数で切ると、意味の上で同じ提案が枠を占め、改修計画の中で
    統合された後に空いた枠を埋められない。「同じ変更か」は同じ組の中でしか問わない
    ため、組で切れば統合で組の数は減らない。
    """
    merged = _merge_by_key(proposals)
    min_severity = SEVERITY_ORDER.get(threshold, SEVERITY_ORDER[DEFAULT_SEVERITY_THRESHOLD])
    deferred: list[tuple[dict[str, Any], str]] = []
    kept: list[dict[str, Any]] = []
    for item in merged.values():
        if item["degraded"]:
            deferred.append((item, DEFER_VOCABULARY))
        elif SEVERITY_ORDER[item["severity"]] < min_severity:
            deferred.append((item, DEFER_THRESHOLD))
        else:
            kept.append(item)
    kept.sort(key=order_key)

    group_order: list[tuple[str, str]] = []
    members: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in kept:
        key = group_key(item)
        if key not in members:
            group_order.append(key)
            members[key] = []
        members[key].append(item)

    candidates: list[dict[str, Any]] = []
    for position, key in enumerate(group_order):
        for index, item in enumerate(members[key]):
            if position < groups and index < per_group:
                candidates.append(item)
            else:
                deferred.append((item, DEFER_RANK))
    for n, item in enumerate(candidates, start=1):
        item["id"] = f"C-{n:03d}"
        item.pop("degraded", None)
    return candidates, deferred
