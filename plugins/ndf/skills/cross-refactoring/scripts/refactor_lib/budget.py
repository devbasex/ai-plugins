"""想定最大時間から、採る項目と実装の終わりを決める（#933 の「時間の決め方」、#1743）。

**純粋な処理だけを置く。** 今の時刻は内部で取らず、引数で受ける。時刻を内部で取ると、
締め切りの計算がテストで再現できない。値の単位は、断りの無い限り分である。

改修計画で見積りを収め、実装を止める時刻は「実装の終わり」の 1 つだけにする（#1743 決定 9）。
項目ごとの期限は持たない。検証の後に時間が残れば、見送った候補を採り直す（決定 10）。
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

from .allocation import lookup
from .vocabulary import SEVERITY_ORDER

# 等級の順位。Jev か実装担当が付ける（#933 決定 11）。付いていない候補は最も低い 0 とみなす。
TIER_ORDER = {"high": 3, "medium": 2, "low": 1}


def item_estimate(table: dict[str, Any], technique: str, has_tests: bool) -> dict[str, float]:
    """項目 1 件の見積り（分）。テストを足さない項目はテストの分を 0 にする。"""
    return {
        "test": float(table["test"]) if has_tests else 0.0,
        "implement": lookup(table, f"structure/{technique}"),
        "verify": float(table["verify"]),
    }


def estimate_total(estimate: dict[str, Any]) -> float:
    """見積りの合計（分）。"""
    return sum(float(estimate.get(name) or 0.0) for name in ("test", "implement", "verify"))


def reserve(
    strategy: Any,
    whole_seconds: Optional[float],
    ci_seconds: Optional[float],
    ci_gate: bool,
    fix_minutes: float,
) -> dict[str, float]:
    """予備時間 R の内訳（分。#1334 決定 8）。

    危険フラグの全体テストは手元で走らせる戦略なら所要 w、全体テストを CI に任せる戦略なら 0（最終ゲートへ寄せる）。
    最終ゲートは手元なら w、CI で見る（戦略か `--ci-check`）なら CI の壁時計 c。w も c も測れていなければ 0 にする。
    見積りが無いのに予備時間を大きく取ると、項目が 1 件も入らなくなる。
    """
    import test_strategy as ts

    danger, final = ts.reserve_seconds(strategy, whole_seconds, ci_seconds, ci_gate)
    return {
        "danger_whole_test": danger / 60,
        "final_whole_test": final / 60,
        "fix": float(fix_minutes),
        # 最終ゲートの修正 1 回分（決定 26）。検証の直しに食わせず、最終ゲートが落ちたとき
        # 必ず 1 度は直しを試みるための時間である。
        "final_fix": float(fix_minutes),
    }


def reserve_total(r: dict[str, Any]) -> float:
    """予備時間の合計（分）。"""
    return sum(float(v or 0.0) for v in r.values())


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


def select(ranked: list[dict[str, Any]], available_minutes: float) -> tuple[list[dict], list[dict]]:
    """順位の順にたどり、入る項目は入れ、入らない項目は飛ばして次を見る（#933 決定 10）。

    入らなくなった時点で打ち切ると、大きな項目 1 件の後ろの小さな項目が入らず時間が
    余る。飛ばしても、順位の高い項目が先に着手される順序は変わらない。
    戻り値は（採用, 予算で見送り）で、どちらも順位の順を保つ。
    """
    selected: list[dict] = []
    skipped: list[dict] = []
    left = float(available_minutes)
    for candidate in ranked:
        cost = estimate_total(candidate.get("estimate") or {})
        # 分の小数の足し引きで丁度の枠が誤差で外れないよう、わずかな幅を許す。
        if cost <= left + 1e-9:
            selected.append(candidate)
            left -= cost
        else:
            skipped.append(candidate)
    return selected, skipped


def plan_reserve(state: dict[str, Any], table: dict[str, Any]) -> dict[str, float]:
    """状態から w と c を選んでバッファ R を出す。`init` の見込みと `merge-plan` が同じ式を使う（#1743 の F1・F2）。"""
    from . import timeline

    baseline = state.get("baseline_test") or {}
    strategy = timeline.strategy_of(state)
    whole_seconds = baseline.get("seconds") if baseline.get("mode") in ("whole", "round") else baseline.get("whole_seconds")
    return reserve(
        strategy,
        whole_seconds,
        baseline.get("ci_seconds"),
        strategy.whole_on_ci or bool(state.get("ci_check")),
        float(table["fix"]),
    )


def measured_verify_minutes(state: dict[str, Any]) -> float:
    """着手前に手元で走らせた範囲テストの実測（分）。範囲テストでなければ 0（#1334 決定 8）。"""
    baseline = state.get("baseline_test") or {}
    return float(baseline.get("seconds") or 0.0) / 60 if baseline.get("mode") == "scope" else 0.0


def shortest_item_minutes(table: dict[str, Any], measured_verify: float) -> float:
    """1 件の長さ L（分）。配分テーブルの手法のうち最も短い実装と検証の和（テストの追加を除く。#1743 決定 2）。"""
    implement = min([float(table["structure"]), *(float(v) for v in (table.get("kinds") or {}).values())])
    return implement + max(float(table["verify"]), float(measured_verify))


def _sum_part(items: list[dict[str, Any]], name: str) -> float:
    return sum(float((i.get("estimate") or {}).get(name) or 0.0) for i in items)


def implement_end(final_end: _dt.datetime, reserve_minutes: dict[str, Any], items: list[dict[str, Any]]) -> _dt.datetime:
    """実装の終わり = 最終ゲート修正の打ち切り − バッファ − Σ（採っていて未検証の項目の検証の見積り）（I13）。

    呼ぶ側が採っていて未検証の項目だけを渡す。項目の実装の見積りは引かない（実装担当の速さで止めない）。
    """
    return final_end - _dt.timedelta(minutes=reserve_total(reserve_minutes) + _sum_part(items, "verify"))


def add_tests_end(implement_end_at: _dt.datetime, items: list[dict[str, Any]]) -> _dt.datetime:
    """テストの追加の終わり = 実装の終わり − Σ（同じ項目の実装の見積り）（I13）。"""
    return implement_end_at - _dt.timedelta(minutes=_sum_part(items, "implement"))


def readopt_available(final_end: _dt.datetime, reserve_minutes: dict[str, Any], now: _dt.datetime) -> float:
    """採り直しに使える残り（分）= 最終ゲート修正の打ち切り − 今 − バッファ（I14）。負にもなる。"""
    return (final_end - now).total_seconds() / 60 - reserve_total(reserve_minutes)


def fix_end(started_at: _dt.datetime, budget_minutes: int, reserve: dict[str, Any]) -> _dt.datetime:
    """修正に使える終わりの時刻。上限の表の `fix_end_at` の元で、`merge-plan` の時点に 1 度だけ出す。

    **予備時間の `fix` は引かない。** 控えておいた修正 1 回分を使えるようにするためである。
    全体のテストの予備時間 2 つと、最終ゲートの修正の予備時間（`final_fix`。決定 26）を差し引いた
    終わりである。`final_fix` を引かないと、検証の直しが最終ゲートの修正の時間まで使う。
    """
    return started_at + _dt.timedelta(
        minutes=budget_minutes
        - float(reserve.get("danger_whole_test") or 0.0)
        - float(reserve.get("final_whole_test") or 0.0)
        - float(reserve.get("final_fix") or 0.0)
    )


def fix_time_left(fix_end_at: _dt.datetime, now: _dt.datetime) -> float:
    """修正に使える残り（分）。終わりは上限の表の `fix_end_at`（再開でずれた値。#1743 決定 5）。"""
    return (fix_end_at - now).total_seconds() / 60


def available_minutes(budget_minutes: int, elapsed_minutes: float, reserve: dict[str, Any]) -> float:
    """使える時間 A = budget − 経過 E − 予備時間 R（分）。負にもなる（何も入らない）。"""
    return float(budget_minutes) - float(elapsed_minutes) - reserve_total(reserve)
