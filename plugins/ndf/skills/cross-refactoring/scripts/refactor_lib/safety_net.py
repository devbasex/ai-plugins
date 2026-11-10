"""安全網（#2457 決定 5・不変条件 I8）。

コーディングの CLI（テストの追加・実装・修正・最終ゲート修正・フレーキーの直し）の起動ごとの秒（監視の開始から終わり）を
累計し、上限に届いたら項目を取り消さずに止める。提案・計画・判定・テストの実行・CI の待ちと止まっていた時間は数えない。
上限は `--safety-net-minutes`（無ければ実効の予算 × `limits.json` の `cross-refactoring.safety-net` の倍率）である。
"""

from __future__ import annotations

import math
from typing import Any, Optional

import statefile

from . import budget

CODING_PHASES = ("add-tests", "implement", "fix", "final-fix", "flaky-fix")


def record(state: dict[str, Any]) -> dict[str, Any]:
    net = state.get("safety_net")
    if not isinstance(net, dict):
        net = state["safety_net"] = {"limit_minutes": None, "used_seconds": 0.0, "halted_at": None}
    return net


def limit_minutes(state: dict[str, Any]) -> float:
    """安全網の上限（分）。"""
    plan_budget = state.get("budget") or {"minutes": int(state.get("budget_minutes") or 0)}
    explicit = state.get("safety_net_minutes")
    return budget.safety_net_minutes(plan_budget, float(explicit) if explicit is not None else None)


def used_seconds(state: dict[str, Any]) -> float:
    return float(record(state).get("used_seconds") or 0.0)


def remaining_seconds(state: dict[str, Any]) -> int:
    """上限までの残り（秒）。0 以下なら新しいコーディングの CLI を起動しない。"""
    return math.floor(limit_minutes(state) * 60 - used_seconds(state))


def refresh(state: dict[str, Any]) -> None:
    """上限を書き直す。打ち直しで上限を大きくして残りができたら、止めた印を消す（続ける）。"""
    net = record(state)
    net["limit_minutes"] = limit_minutes(state)
    if net.get("halted_at") and remaining_seconds(state) > 0:
        net["halted_at"] = None


def add(state: dict[str, Any], phase: str, seconds: float, items: Optional[list[dict[str, Any]]] = None) -> None:
    """コーディングの CLI の起動 1 回の秒を累計へ足す。修正の秒は対象の項目の数で等分して項目の `coding_seconds.fix` へ足す。"""
    if phase not in CODING_PHASES:
        return
    net = record(state)
    net["used_seconds"] = round(float(net.get("used_seconds") or 0.0) + max(0.0, float(seconds)), 1)
    net["limit_minutes"] = limit_minutes(state)
    if phase in ("fix", "final-fix") and items:
        share = max(0.0, float(seconds)) / len(items)
        for item in items:
            coding = item.setdefault("coding_seconds", {"test": 0.0, "implement": 0.0, "fix": 0.0})
            coding["fix"] = round(float(coding.get("fix") or 0.0) + share, 1)
    if remaining_seconds(state) <= 0 and not net.get("halted_at"):
        net["halted_at"] = statefile.now()


def halted(state: dict[str, Any]) -> bool:
    return remaining_seconds(state) <= 0


def halt_reason(state: dict[str, Any]) -> str:
    return (
        f"安全網の上限 {limit_minutes(state):.0f} 分に当たった（コーディングの CLI の累計 {used_seconds(state) / 60:.1f} 分）。"
        "続けるなら --safety-net-minutes を大きくして同じコマンドを打ち直す。やめるなら項目のコミットを git revert して "
        "plan-comment --scan-reverts を打つ"
    )
