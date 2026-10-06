"""採り直し（`readopt`、#1743 の F7・決定 10）。

検証（直しの試行を含む）が終わった後、残った時間 `final_end_at − 今 − Σ plan.reserve` に入る候補を、計画と同じ順位
（`budget.rank_key`）と選び方（`budget.select`。入らない候補は飛ばして次を見る）で採り直す。候補は `budget` で見送った
候補と、実装の終わりまでにコミットが無かった持ち越しの項目だけである（I15）。見積り・範囲テスト・等級・`risk` は
`merge-plan` が計画の時点で書いた値を使い、**LLM を呼ばない**（I14）。D5（`public_io`）は実装担当の `risk` を使う。

| 終了コード | 意味 |
| --- | --- |
| 0 | 1 件以上を採り直した。`TESTS_NEEDED=0|1` を出し、テストの追加・実装・検証をもう 1 巡回す |
| 2 | 入る候補が無い（持ち越しの項目を `not_done` で見送った）か、計画が無い。最終ゲートへ |

`phase` を `final` にするのは実行全体でここだけである（最終ゲートの入口の `final-gate` を除く）。
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Optional

import statefile

from .. import budget, clock, info, timeline
from ..items import CARRIED, DEFERRED, PLANNED, defer, new_items
from ..paths import load_state
from ..vocabulary import DEFER_BUDGET, DEFER_NOT_DONE

SELECTED = "selected"
NO_FIT = "no_fit"
# 採るときに前の巡の記録を `phases_history` へ移す手順（巡ごとに取り込みと所要を数える）
ROUND_PHASES = ("add-tests", "implement", "verify", "fix")
NOT_DONE_REASON = "実装の終わりまでにコミットが無く、残った時間に入らない"


def _budget_candidates(state: dict[str, Any]) -> list[dict[str, Any]]:
    """`budget` で見送った候補（計画の時点の見積りを持つもの）。"""
    ids = {d.get("item_id") for d in state.get("deferred_items") or [] if d.get("defer_reason") == DEFER_BUDGET}
    return [c for c in state.get("candidates") or [] if c.get("id") in ids and c.get("estimate")]


def _archive_round(state: dict[str, Any], round_no: int) -> None:
    """前の巡の手順の記録と全体テストの記録を履歴へ移す。直しの途中の全体テストは移さない。"""
    phases = state.get("phases") or {}
    history = state.setdefault("phases_history", [])
    for name in ROUND_PHASES:
        if name in phases:
            history.append({"round": round_no, "name": name, "record": phases.pop(name)})
    whole = state.get("whole_test")
    if whole and whole.get("resolution") != "fixing":
        state.setdefault("whole_test_history", []).append({"round": round_no, "record": state.pop("whole_test")})


def _reopen(item: dict[str, Any], round_no: int) -> None:
    """持ち越しの項目を、新しい巡の未着手の項目へ戻す（変更は取り込みが捨ててある）。"""
    item.update(
        {
            "status": PLANNED,
            "round": round_no,
            "commits": {"test": None, "implement": None, "fix": []},
            "seconds": {},
            "fix_count": 0,
            "danger": [],
        }
    )
    for key in ("failure_reason", "danger_checked", "danger_hits", "diff_lines", "review_test_judgements"):
        item.pop(key, None)


def _adopt(state: dict[str, Any], selected: list[dict[str, Any]], carried: list[dict[str, Any]], round_no: int) -> list[str]:
    """採った候補を項目にし、採った項目の ID を返す。`budget` の見送りの記録は外す。"""
    ids: list[str] = []
    items = state.setdefault("items", [])
    carried_ids = {id(i) for i in carried}
    for candidate in selected:
        if id(candidate) in carried_ids:
            _reopen(candidate, round_no)
            ids.append(candidate["id"])
            continue
        item = new_items([candidate], first_rank=len(items) + 1, round_no=round_no)[0]
        item["public_io"], item["public_io_source"] = bool(item.get("risk")), "runtime"
        items.append(item)
        ids.append(item["id"])
        state["deferred_items"] = [
            d
            for d in state.get("deferred_items") or []
            if not (d.get("item_id") == candidate["id"] and d.get("defer_reason") == DEFER_BUDGET)
        ]
    return ids


def _rewrite_limits(state: dict[str, Any]) -> None:
    """採っていて未検証の項目で、テストの追加の終わりと実装の終わりを書き直す（I13）。最終ゲート修正の打ち切りは動かさない。"""
    limits = state["limits"]
    final_end = clock.parse(limits["final_end_at"])
    pending = timeline.pending_items(state.get("items") or [])
    implement_end = budget.implement_end(final_end, (state.get("plan") or {}).get("reserve") or {}, pending)
    limits["implement_end_at"] = clock.iso(implement_end)
    limits["add_tests_end_at"] = clock.iso(budget.add_tests_end(implement_end, pending))


def _close(path: Any, state: dict[str, Any], record: dict[str, Any], available: float, shortest: Optional[float]) -> None:
    """入る候補が無い。持ち越しの項目を `not_done` で見送り、最終ゲートへ移す。"""
    for item in state.get("items") or []:
        if item.get("status") == CARRIED:
            # 状態を見送りにする前に見送りへ足す（#1658 の I7）
            defer(state, item, DEFER_NOT_DONE, NOT_DONE_REASON)
            item["status"] = DEFERRED
            item["failure_reason"] = NOT_DONE_REASON
    record.update({"reason": NO_FIT, "selected": []})
    state["readopt"]["events"].append(record)
    state["phase"] = "final"
    statefile.save(path, state)
    smallest = f"最も短い候補の見積り {shortest:.1f} 分" if shortest is not None else "候補なし"
    info(f"採り直し: 残った時間 {available:.1f} 分に入る候補がありません（{smallest}）。最終ゲートへ進みます")
    sys.exit(2)


def _replayed(state: dict[str, Any]) -> bool:
    """叩き直しなら最後の記録を返して真（採り直しを重ねない）。`no_fit` の記録なら終了コード 2 で抜ける。"""
    readopt = state.get("readopt") or {}
    events = readopt.get("events") or []
    last = events[-1] if events else None
    if state.get("phase") == "readopt" or last is None:
        return False
    if last.get("reason") == SELECTED and int(last.get("round") or 0) == int(readopt.get("round") or 1):
        info(f"↻ 採り直しは記録済みです（{len(last.get('selected') or [])} 件。巡 {last.get('round')}）")
        statefile.emit(TESTS_NEEDED=1 if any(i.get("tests") and i.get("status") == PLANNED for i in state.get("items") or []) else 0)
        return True
    if last.get("reason") == NO_FIT:
        info("↻ 採り直しの判定は記録済みです（入る候補が無い）。最終ゲートへ進みます")
        sys.exit(2)
    return False


def cmd_readopt(args: argparse.Namespace) -> None:
    """検証の後、残った時間に入る見送りの候補を採り直す。"""
    path, state = load_state(args.id)
    if not state.get("plan"):
        info("採り直しの対象がありません（リファクタリング計画の前）。最終ゲートへ進みます")
        sys.exit(2)
    readopt = state.setdefault("readopt", {"round": 1, "events": []})
    readopt.setdefault("events", [])
    if _replayed(state):
        return
    round_no = int(readopt.get("round") or 1)
    now = clock.now()
    reserve = (state.get("plan") or {}).get("reserve") or {}
    available = budget.readopt_available(clock.parse(timeline.limits_of(state)["final_end_at"]), reserve, now)
    carried = [i for i in state.get("items") or [] if i.get("status") == CARRIED]
    ranked = sorted([*carried, *_budget_candidates(state)], key=budget.rank_key)
    record = {"at": clock.iso(now), "round": round_no, "available_minutes": round(available, 2), "carried": [i["id"] for i in carried]}
    selected = budget.select(ranked, available)[0] if available > 0 and state.get("phase") == "readopt" else []
    if not selected:
        shortest = min((budget.estimate_total(c.get("estimate") or {}) for c in ranked), default=None)
        _close(path, state, record, available, shortest)
        return
    _archive_round(state, round_no)
    ids = _adopt(state, selected, carried, round_no + 1)
    readopt["round"] = round_no + 1
    record.update({"round": round_no + 1, "reason": SELECTED, "selected": ids})
    readopt["events"].append(record)
    _rewrite_limits(state)
    tests_needed = any(i.get("tests") for i in state["items"] if i["id"] in ids)
    state["phase"] = "add-tests" if tests_needed else "implement"
    statefile.save(path, state)
    left = len(_budget_candidates(state))
    info(
        f"採り直し: 残った時間 {available:.1f} 分に {len(ids)} 件（{', '.join(ids)}）を採りました（巡 {round_no + 1}。採らずに残った budget の候補 {left} 件）"
    )
    statefile.emit(TESTS_NEEDED=1 if tests_needed else 0)
