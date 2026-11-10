"""修正の繰り返しの判定（#2457 決定 3・4、不変条件 I4・I6・I7）。

検証と最終ゲートで変更起因の失敗が出た改善項目ごとに、落ちたテストの集合 F(n) を履歴（`fix_history`）へ積み、
`progress.classify_progress` の判定で次の手を決める。**時計と回数を見ない。**

| 判定 | 次の手 |
| --- | --- |
| 初めて落ちた・前進 | 同じ修正担当で修正を続ける（`fix`） |
| 停滞・振動（修正担当を替えていない） | 判定し直し。成立なら修正担当を別のランタイムへ替える（`switch`。1 項目に 1 回） |
| 停滞・振動（替えた後）か成立しない | その項目のコミットだけを打ち消す（`revert`。理由 `stalled_after_switch` / `infeasible`） |
| 判定し直しで担い手が居ない | 止めて人へ渡す（`halt`） |

項目の `status` を `reverted` にする理由は `infeasible` と `stalled_after_switch` の 2 つだけである（I4）。
"""

from __future__ import annotations

from typing import Any, Optional

import statefile

from . import progress
from .items import FAILING, live_items

FIX = "fix"
SWITCH = "switch"
REVERT = "revert"
HALT = "halt"

INFEASIBLE = "infeasible"
STALLED_AFTER_SWITCH = "stalled_after_switch"
REVERT_REASONS = (INFEASIBLE, STALLED_AFTER_SWITCH)
REASON_TEXT = {
    INFEASIBLE: "成立しないと判定した",
    STALLED_AFTER_SWITCH: "修正担当を替えた後も修正が停滞か振動した",
}


def failing_set(item: dict[str, Any]) -> list[str]:
    """項目の落ちたテストの ID の並び（無関係な失敗とフレーキーは呼ぶ側が除いてある）。ID が無ければ検査の鍵、
    それも無ければ `<whole>` の 1 要素（決定 12）。"""
    check = item.get("failed_check") or {}
    tests = [str(t) for t in check.get("tests") or []]
    if tests:
        return sorted(set(tests))
    if check.get("suite") or check.get("command"):
        files = ",".join(sorted(str(f) for f in check.get("files") or []))
        return [f"{check.get('kind') or ''}:{check.get('suite') or check.get('command')}:{files}"]
    return [progress.WHOLE]


def fixer_of(state: dict[str, Any], item: dict[str, Any]) -> str:
    """今の修正担当。替えていなければ実装担当。"""
    return str(item.get("fixer") or state.get("implementer") or "")


def other_runtime(state: dict[str, Any], exclude: list[str]) -> Optional[str]:
    """実装担当（と `exclude`）以外の参加者を、ホスト → 参加者の並びの順で 1 者選ぶ（決定 3。`assignment.choose_implementer`）。"""
    import assignment

    skip = {str(state.get("implementer") or ""), *exclude}
    candidates = [r for r in state.get("runtimes") or [] if r not in skip]
    if not candidates:
        return None
    return assignment.choose_implementer(candidates, str(state.get("host") or ""))[0]


def note_round(state: dict[str, Any], item: dict[str, Any]) -> str:
    """検証で落ちた項目の F(n) を履歴へ積み、判定を返す。同じ巡の同じ修正の後に 2 度積まない（`fix_seq`）。"""
    history = item.setdefault("fix_history", [])
    seq = int(item.get("fix_count") or 0)
    if history and int(history[-1].get("round") or 0) == seq:
        return str(history[-1].get("progress") or progress.FIRST)
    failing = failing_set(item)
    sets = [h.get("failing") or [] for h in history] + [failing]
    verdict = progress.classify_progress(sets)
    history.append({"round": seq, "by": fixer_of(state, item), "failing": failing, "progress": verdict})
    return verdict


def next_move(state: dict[str, Any], item: dict[str, Any], verdict: str) -> str:
    """判定から次の手を返す（判定し直しの答えは `judge_answer` が反映する）。"""
    if verdict not in progress.STOPPING:
        return FIX
    if item.get("fixer_switched"):
        return REVERT
    return SWITCH


def switch_fixer(state: dict[str, Any], item: dict[str, Any], judge_by: Optional[str]) -> Optional[str]:
    """修正担当を 1 回だけ替える（I7）。替え先が無ければ `None`。"""
    if item.get("fixer_switched"):
        return None
    new = other_runtime(state, [x for x in (fixer_of(state, item),) if x])
    if new is None:
        return None
    item["fixer"], item["fixer_switched"] = new, True
    return new


def note_feasibility(item: dict[str, Any], by: Optional[str], feasible: Optional[bool], reason: str, evidence: str, rejudge: bool) -> None:
    """成立の判定を項目の `feasibility[]` へ積む（受け入れ条件 6）。`feasible` が `None` は「判定できない」。"""
    item.setdefault("feasibility", []).append(
        {
            "by": by,
            "feasible": feasible,
            "reason": reason,
            "evidence": evidence,
            "at": statefile.now(),
            "rejudge": bool(rejudge),
            "seq": int(item.get("fix_count") or 0),
        }
    )


def failing_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in live_items(state) if i.get("status") == FAILING]
