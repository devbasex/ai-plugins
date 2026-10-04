"""副命令 `reassign <ID> <工程>`: 結果を残さなかった担当を、規則の答えどおりに起動し直すか振り替える（#919）。

監視の後・取り込み（`merge-*`）の前に 1 回打つ。起動し直すか・誰へ振り替えるかは規則
（`assignment.after_no_result`）だけが決め、ここは答えを記録（`no_results`）へ追記して担当を書き換える。

| 終了コード | 意味 | 出力の行 |
| ---: | --- | --- |
| 0 | 結果なしとして扱わない（結果がある・上限での打ち切り `timeout` / `stalled`。決定 8） | `REASSIGN=none` |
| 7 | 同じ工程を起動する（`propose` / `plan` / `add-tests` / `implement`） | `IMPL=<席>`（提案は `PROPOSERS`）、振り替えなら `REASSIGNED` |
| 2 | 次の起動から担当を替えた、または今のまま起動し直す（`fix` / `final-fix`） | `IMPL=<席>`、振り替えなら `REASSIGNED` |
| 3 | 振り替え先が無い（`abort`） | `REASSIGN=abort` |
| 4 | 範囲を確定できない | — |

**範囲の取り消しは `plan` / `add-tests` / `implement` だけ。** 規則に答えを求める前に、工程の起点から
HEAD までを取り消す（`intake.close_without_result`）。採用済みの項目のコミットは起点より前にある。
`final-fix` の取り消しは今の `merge-final-fix`、`fix` は今の `merge-fix` の受け取りと次の `verify` が持つ。

**保存は出力の前に行う。** 打ち直しでは、同じ工程・試行・担当の件がこの起動の終わりより後に記録済みなら、
記録した答えを出力し直すだけにする（同じ件を重ねない）。
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any, Optional

import assignee_env
import assignment
import models as models_lib
import statefile

from .. import clock, die, info
from ..intake import IntakeScope, close_without_result
from ..paths import load_state
from ..results import STOPPED_REASONS, read_result

STEPS = ("propose", "plan", "add-tests", "implement", "fix", "final-fix")
RELAUNCH_IN_PLACE = ("propose", "plan", "add-tests", "implement")  # 同じ工程の中で起動し直す工程（7）
UNDO_STEPS = ("plan", "add-tests", "implement")  # reassign が範囲を取り消す工程


def step_attempt(state: dict[str, Any], step: str) -> int:
    """記録の `attempt`。`fix` は修正の試行番号、`final-fix` は修正ラウンド、ほかは 1。"""
    if step == "fix":
        return int((state.get("fix") or {}).get("attempt") or 1)
    if step == "final-fix":
        return int((state.get("final_gate") or {}).get("fix_rounds") or 0)
    return 1


def _current(state: dict[str, Any], step: str) -> assignment.Assignee:
    implementer = str(state.get("implementer") or "")
    seat = str((state.get("final_gate") or {}).get("impl") or implementer) if step == "final-fix" else implementer
    account = (state.get("implementer_account") or None) if seat == implementer else None
    return assignment.Assignee(seat, account)


def _later(at: Any, ended: Any) -> bool:
    """記録の時刻 `at` が起動の終わり `ended` より後か。終わりが読めなければ偽（新しい結果なしとして扱う）。"""
    a, b = clock.parse(str(at or "")), clock.parse(str(ended or ""))
    return a is not None and b is not None and a >= b


def _recorded(state: dict[str, Any], step: str, attempt: int, failed: assignment.Assignee, ended: Any) -> Optional[dict]:
    for e in reversed(state.get("no_results") or []):
        if (
            e.get("step") == step
            and e.get("attempt") == attempt
            and e.get("seat") == failed.seat
            and (e.get("account") or None) == failed.account
            and _later(e.get("at"), ended)
        ):
            return e
    return None


def _decision_of(entry: dict[str, Any]) -> assignment.NoResultDecision:
    action = str(entry.get("decision") or assignment.ABORT)
    to = None
    if action == assignment.REASSIGN:
        to = assignment.Assignee(str(entry.get("to") or ""), entry.get("to_account") or None)
    elif action == assignment.RELAUNCH:
        to = assignment.Assignee(str(entry.get("seat") or ""), entry.get("account") or None)
    return assignment.NoResultDecision(action, to, action != assignment.RELAUNCH, "")


def _decide(
    state: dict[str, Any],
    failed: assignment.Assignee,
    reason: str,
    step: str,
    attempt: int,
    busy: list[str],
) -> assignment.NoResultDecision:
    log = state.setdefault("no_results", [])
    d = assignment.after_no_result(
        failed,
        reason,
        available=list((state.get("participants") or {}).get("available") or state.get("runtimes") or []),
        log=log,
        step=step,
        attempt=attempt,
        host=str(state.get("host") or ""),
        busy=busy,
        initial_account=assignee_env.initial_account(),
        pick_account=assignee_env.account_picker(),
    )
    log.append(assignment.no_result_entry(step, attempt, failed, reason, d, statefile.now()))
    return d


def _set_implementer(state: dict[str, Any], to: assignment.Assignee, step: str) -> None:
    """実装担当を振り替え先へ書き換える。`final-fix` は最終ゲートの担当も替える。"""
    if to.seat != state.get("implementer"):
        state["implementer_model"] = models_lib.model_record((state.get("models") or {}).get(to.runtime()))
    state["implementer"] = to.seat
    state["implementer_account"] = to.account or ""
    state["implementer_reason"] = "reassigned"
    if step == "final-fix":
        state.setdefault("final_gate", {})["impl"] = to.seat


def _apply(state: dict[str, Any], step: str, d: assignment.NoResultDecision) -> None:
    if d.action == assignment.REASSIGN and d.to is not None:
        _set_implementer(state, d.to, step)
    elif d.action == assignment.ABORT:
        if step == "fix":
            state["fix_no_relaunch"] = True
        elif step == "final-fix":
            state.setdefault("final_gate", {})["no_relaunch"] = True


def _target_label(to: assignment.Assignee) -> str:
    return f"claude@{to.account}" if to.account else to.seat


def _emit_decision(step: str, failed: assignment.Assignee, reason: str, d: assignment.NoResultDecision) -> None:
    if d.action == assignment.ABORT:
        info(f"⚠ {failed.label()} は結果を残さず、振り替え先もありません（{reason}）")
        statefile.emit(REASSIGN="abort")
        sys.exit(3)
    assert d.to is not None
    lines = {"IMPL": d.to.seat}
    if d.action == assignment.REASSIGN:
        lines["REASSIGNED"] = f"{failed.seat}={_target_label(d.to)}:{reason}"
        info(f"↪ {step}: {failed.label()} → {d.to.label()}（{reason}）")
    else:
        info(f"↻ {step}: {failed.label()} を同じ担当で起動し直します（{reason}）")
    statefile.emit(**lines)
    sys.exit(7 if step in RELAUNCH_IN_PLACE else 2)


def _reassign_implementer(path: pathlib.Path, state: dict[str, Any], step: str) -> None:
    failed = _current(state, step)
    if not failed.seat:
        die("実装担当が記録されていません", code=4)
    attempt = step_attempt(state, step)
    outcome = read_result(state, failed.seat, step)
    if outcome.payload is not None or outcome.reason in STOPPED_REASONS:
        statefile.emit(REASSIGN="none")
        sys.exit(0)
    reason = str(outcome.reason or "missing")
    entry = _recorded(state, step, attempt, failed, (outcome.monitor or {}).get("ended_at"))
    if entry is not None:
        _emit_decision(step, failed, reason, _decision_of(entry))
    if step in UNDO_STEPS:
        record = state.setdefault("phases", {}).setdefault(step, {})
        scope = IntakeScope(
            holder=record,
            base_key="base_sha",
            records=record,
            phase=step,
            attempt=attempt,
            impl=failed.label(),
            label=f"{step}-no-result",
        )
        if close_without_result(path, state, scope, outcome).range_unknown:
            statefile.save(path, state)
            die(f"{step} の範囲を確定できませんでした（起点 {record.get('base_sha')}）。検証できない変更は採りません", code=4)
    d = _decide(state, failed, reason, step, attempt, busy=[])
    _apply(state, step, d)
    statefile.save(path, state)
    _emit_decision(step, failed, reason, d)


def _reassign_proposers(path: pathlib.Path, state: dict[str, Any], seats: list[str]) -> None:
    """提案担当の全員が結果なしのときだけ振り替える（前提 9）。1 者でも結果があれば 0。"""
    accounts = dict(state.get("proposer_accounts") or {})
    launched = seats or list(state.get("runtimes") or [])
    outcomes = {s: read_result(state, s, "propose") for s in launched}
    if not outcomes or any(o.payload is not None for o in outcomes.values()):
        statefile.emit(REASSIGN="none")
        sys.exit(0)
    targets: list[assignment.Assignee] = []
    moved: list[str] = []
    for seat, outcome in outcomes.items():
        failed = assignment.Assignee(seat, accounts.get(seat) or None)
        reason = str(outcome.reason or "missing")
        entry = _recorded(state, "propose", 1, failed, (outcome.monitor or {}).get("ended_at"))
        if entry is not None:
            d = _decision_of(entry)
        else:
            # 同じ呼び出しで起動した者（みな結果なし）と、先に選んだ振り替え先は候補にしない
            busy = [s for s in launched if s != seat] + [t.seat for t in targets]
            d = _decide(state, failed, reason, "propose", 1, busy)
        if d.to is None:
            continue
        targets.append(d.to)
        if d.to.account:
            accounts[d.to.seat] = d.to.account
        if d.action == assignment.REASSIGN:
            moved.append(f"{seat}={_target_label(d.to)}:{reason}")
    if any(accounts.values()):
        state["proposer_accounts"] = {k: v for k, v in accounts.items() if v}
    statefile.save(path, state)
    if not targets:
        info("⚠ 提案担当の全員が結果を残さず、振り替え先もありません")
        statefile.emit(REASSIGN="abort")
        sys.exit(3)
    lines = {"PROPOSERS": " ".join(dict.fromkeys(t.seat for t in targets))}
    if moved:
        lines["REASSIGNED"] = " ".join(moved)
    statefile.emit(**lines)
    sys.exit(7)


def cmd_reassign(args: argparse.Namespace) -> None:
    """結果を残さなかった担当について、規則の答えを記録して担当を書き換え、出力と終了コードで駆動へ返す。"""
    path, state = load_state(args.id)
    if args.phase == "propose":
        _reassign_proposers(path, state, list(getattr(args, "seats", None) or []))
    _reassign_implementer(path, state, args.phase)
