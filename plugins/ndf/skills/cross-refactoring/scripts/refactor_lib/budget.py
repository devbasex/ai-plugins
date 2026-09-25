"""想定最大時間から、採る項目と項目ごとの締め切りを決める（#933 の「時間の決め方」）。

**純粋な処理だけを置く。** 今の時刻は内部で取らず、引数で受ける。時刻を内部で取ると、
締め切りの計算がテストで再現できない。値の単位は、断りの無い限り分である。

改修計画で見積りを収め、実装の中は項目ごとの締め切りで着手を止める。手順の監視の上限は
`timeline` がこの締め切りから導く（決定 23。決定 4 の「実行中の CLI を止めない」を改めた）。
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


def reserve(baseline_seconds: Optional[float], ci_check: bool, fix_minutes: float) -> dict[str, float]:
    """予備時間 R の内訳（分）。

    全体のテストの所要は同じ実行の着手前の全体のテスト（`init`）の秒から見積もる。
    測れていなければ 0 にする。見積りが無いのに予備時間を大きく取ると、項目が 1 件も
    入らなくなる。最終ゲートの全体のテストは `--ci-check` があれば継続的統合が担い、
    想定最大時間の内で走らないため 0 にする。
    """
    whole = float(baseline_seconds) / 60 if baseline_seconds is not None else 0.0
    return {
        "danger_whole_test": whole,
        "final_whole_test": 0.0 if ci_check else whole,
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


def end_time(started_at: _dt.datetime, budget_minutes: int, reserve_total_minutes: float) -> _dt.datetime:
    """終わり T = started_at + budget − R。締め切りはここから逆算する。"""
    return started_at + _dt.timedelta(minutes=budget_minutes - reserve_total_minutes)


def deadlines(selected: list[dict[str, Any]], T: _dt.datetime) -> list[dict[str, Optional[_dt.datetime]]]:
    """採用の順（1..n）に、実装とテストの追加の着手の締め切りを返す。

    - 実装 i: `T − Σ_{j≥i} implement_j − Σ_{全件} verify_j`。検証は実装の手順の
      後に全件をまとめて走らせるため、i より前の項目の検証も末尾の側に残る
    - テストの追加 i: `T − Σ_{全件}(implement_j + verify_j) − Σ_{j≥i} test_j`。
      テストの追加は実装より前の手順なので、実装と検証の全件を先に差し引く。
      足すテストが無い項目（test が 0）は `None`
    """
    estimates = [c.get("estimate") or {} for c in selected]

    def part(name: str, e: dict[str, Any]) -> float:
        return float(e.get(name) or 0.0)

    verify_all = sum(part("verify", e) for e in estimates)
    impl_verify_all = sum(part("implement", e) + part("verify", e) for e in estimates)
    result = []
    for i, e in enumerate(estimates):
        impl_after = sum(part("implement", x) for x in estimates[i:])
        test_after = sum(part("test", x) for x in estimates[i:])
        start = T - _dt.timedelta(minutes=impl_after + verify_all)
        test_start = (
            T - _dt.timedelta(minutes=impl_verify_all + test_after)
            if part("test", e) > 0 else None
        )
        result.append({"start_deadline": start, "test_start_deadline": test_start})
    return result


def fix_end(started_at: _dt.datetime, budget_minutes: int, reserve: dict[str, Any]) -> _dt.datetime:
    """修正に使える終わりの時刻。

    **予備時間の `fix` は引かない。** T から測ると、控えておいた修正 1 回分が使われない。
    全体のテストの予備時間 2 つと、最終ゲートの修正の予備時間（`final_fix`。決定 26）を差し引いた
    終わりである。`final_fix` を引かないと、検証の直しが最終ゲートの修正の時間まで使う。
    """
    return started_at + _dt.timedelta(
        minutes=budget_minutes
        - float(reserve.get("danger_whole_test") or 0.0)
        - float(reserve.get("final_whole_test") or 0.0)
        - float(reserve.get("final_fix") or 0.0)
    )


def fix_time_left(
    started_at: _dt.datetime, budget_minutes: int, reserve: dict[str, Any], now: _dt.datetime
) -> float:
    """修正に使える残り（分）。終わりは `fix_end`。"""
    return (fix_end(started_at, budget_minutes, reserve) - now).total_seconds() / 60


def available_minutes(budget_minutes: int, elapsed_minutes: float, reserve: dict[str, Any]) -> float:
    """使える時間 A = budget − 経過 E − 予備時間 R（分）。負にもなる（何も入らない）。"""
    return float(budget_minutes) - float(elapsed_minutes) - reserve_total(reserve)
