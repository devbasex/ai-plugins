"""実装の予算から、採る改善項目を決める（#933 の「時間の決め方」、#2457）。

**予算は選定にだけ使う**（#2457 の原則 1・不変条件 I3）。見積りはテストの追加と実装の和だけで数え、テストの実行・
CI の待ち・経過時間・実装時間の実績を入れない（I1）。予算から締め切りを導かない。

**純粋な処理だけを置く。** 値の単位は、断りの無い限り分である。
"""

from __future__ import annotations

import math
from typing import Any, Optional

import limits as limit_decl

from .allocation import lookup
from .vocabulary import SEVERITY_ORDER

# 等級の順位。Jev か実装担当が付ける（#933 決定 11）。付いていない候補は最も低い 0 とみなす。
TIER_ORDER = {"high": 3, "medium": 2, "low": 1}
# 見積りに数える部分（#2457 決定 1）。検証（`verify`）は数えない。
ESTIMATE_PARTS = ("test", "implement")


def extension_factor() -> float:
    """予算を延ばしてよい倍率（`limits.json` の `cross-refactoring.budget-extension`）。"""
    return float(limit_decl.value("cross-refactoring.budget-extension", "factor"))


def safety_net_factor() -> float:
    """安全網の上限の倍率（`limits.json` の `cross-refactoring.safety-net`）。"""
    return float(limit_decl.value("cross-refactoring.safety-net", "factor"))


def item_estimate(table: dict[str, Any], technique: str, has_tests: bool) -> dict[str, float]:
    """項目 1 件の見積り（分）。テストを足さない項目はテストの分を 0 にする。"""
    return {
        "test": float(table["test"]) if has_tests else 0.0,
        "implement": lookup(table, f"structure/{technique}"),
    }


def estimate_total(estimate: dict[str, Any]) -> float:
    """見積りの合計（分）= テストの追加 + 実装。検証の見積りが残っていても数えない（決定 1）。"""
    return sum(float(estimate.get(name) or 0.0) for name in ESTIMATE_PARTS)


def rank_key(candidate: dict[str, Any]) -> tuple:
    """`sorted(..., key=rank_key)` の先頭が 1 位になる鍵。

    順位は（等級, 賛同した者の数, 重要度）の降順で、同じなら見積りの合計の昇順である。
    降順の 3 つは符号を反転して昇順の並べ替えに載せる。
    """
    return (
        -TIER_ORDER.get(str(candidate.get("tier") or ""), 0),
        -len(candidate.get("proposed_by") or []),
        -SEVERITY_ORDER.get(str(candidate.get("severity") or ""), 0),
        estimate_total(candidate.get("estimate") or {}),
    )


def select(ranked: list[dict[str, Any]], budget_minutes: float) -> tuple[list[dict], list[dict]]:
    """順位の順にたどり、入る項目は入れ、入らない項目は飛ばして次を見る（#933 決定 10）。

    入らなくなった時点で打ち切ると、大きな項目 1 件の後ろの小さな項目が入らず予算が
    余る。飛ばしても、順位の高い項目が先に着手される順序は変わらない。
    戻り値は（採用, 予算で見送り）で、どちらも順位の順を保つ。
    """
    selected: list[dict] = []
    skipped: list[dict] = []
    left = float(budget_minutes)
    for candidate in ranked:
        cost = estimate_total(candidate.get("estimate") or {})
        # 分の小数の足し引きで丁度の枠が誤差で外れないよう、わずかな幅を許す。
        if cost <= left + 1e-9:
            selected.append(candidate)
            left -= cost
        else:
            skipped.append(candidate)
    return selected, skipped


def extension(ranked: list[dict[str, Any]], budget_minutes: int, factor: Optional[float] = None) -> Optional[dict[str, Any]]:
    """予算の延長（#2457 決定 2・不変条件 I2）。延ばさないなら `None`。

    グレード `high` の候補の見積りの和 S が予算 B を超え、`factor`·B 以下のときだけ、実効の予算を `ceil(S)` 分にする。
    """
    b = float(budget_minutes)
    f = extension_factor() if factor is None else float(factor)
    s = round(sum(estimate_total(c.get("estimate") or {}) for c in ranked if c.get("tier") == "high"), 6)
    if s <= b + 1e-9 or s > f * b + 1e-9:
        return None
    to = int(math.ceil(s))
    return {
        "from": int(budget_minutes),
        "to": to,
        "reason": f"high の候補の見積りの和 {s:.1f} 分が予算 {int(budget_minutes)} 分を超えた",
        "high_minutes": s,
    }


def effective_minutes(budget: dict[str, Any]) -> float:
    """実効の予算（分）。延ばしていなければ実装の予算と同じ。"""
    ext = budget.get("extension") or None
    return ext["to"] if ext else budget["minutes"]


def adopted_minutes(items: list[dict[str, Any]]) -> float:
    """採っていて取り消していない項目（採り直しで採った項目を含む）の見積りの和（分。I1）。"""
    return sum(estimate_total(i.get("estimate") or {}) for i in items if i.get("status") not in ("reverted", "deferred"))


def readopt_left(budget: dict[str, Any], items: list[dict[str, Any]]) -> float:
    """採り直しに使える残り（分）= 実効の予算 − 採っていて取り消していない項目の見積りの和（I1・I3）。実装時間の実績は使わない。"""
    return float(effective_minutes(budget)) - adopted_minutes(items)


def safety_net_minutes(budget: dict[str, Any], explicit: Optional[float] = None) -> float:
    """安全網の上限（分）。明示が無ければ実効の予算 × 倍率（#2457 決定 5）。"""
    if explicit is not None:
        return float(explicit)
    return float(effective_minutes(budget)) * safety_net_factor()
