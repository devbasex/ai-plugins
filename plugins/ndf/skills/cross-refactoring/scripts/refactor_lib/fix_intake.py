"""修正の取り込み（`merge-fix` の中身、#933 の F7・#1793 の R3）。入口は `commands/merge_fix.py`。

検証（`commands/converge.py`）も取り込んでいない修正を先に取り込むためここを呼ぶ（`commands` 層どうしは取り込まない）。

検証（`verify`）が落ちた項目を修正へ回し、駆動が修正担当を 1 回起動した後に打たれる。
**結果ファイルの申告は使わない。** 修正の起点から HEAD までのコミットを `Item-Id` で読み、
修正の対象の項目のものだけを受け取る。

**直さなかった項目**（修正の起動が結果を残して終わり、修正の範囲にその項目のコミットが 1 つも無い項目）には
`unfixed` を付け、`failing` のまま残す。落ちたテストの集合が変わらないため、次の検証が停滞として数え、判定し直しへ
回す（#2457 の棚卸し #29）。結果を残さずに終わった起動は振り替えの経路（#919）で扱い、ここでは数えない。

**手順を外れたコミットだけを差し戻す**（棚卸し #31）。修正の対象でない `Item-Id` のコミットと記名の欠けたコミットは
そのコミットだけを `git revert` で打ち消し、手順どおりのコミットは受け取る。範囲の外の変更は取り消さず
`outside_scope` に残す（#24）。
"""

from __future__ import annotations

import pathlib
from typing import Any, Optional

import statefile

from . import clock, info, intake
from .gitfacts import (
    collect_commit_facts,
    commit_trailers,
    commits_in_range,
    discard_impl_leftovers,
    note_stopped,
    record_observed_model,
)
from .items import FAILING, IMPLEMENTED, find_item, implement_shas
from .outbound import plan_line
from .paths import git_out, head_sha, load_state, work_dir
from .phases import add_phase_seconds, phase_record
from .results import read_result
from .scope_check import judge_commits, note_outside
from .worktree import reset_hard, revert_range  # 手順を外れた修正のコミットだけを打ち消す
from . import ledger
from .verify import collect_test_changes, verify_commit_basics, verify_test_changes

UNFIXED_REASON = "修正担当が直さなかった（修正の範囲にこの項目のコミットが無い）"


def _fix_problems(
    state: dict[str, Any],
    facts: list[dict[str, Any]],
    allowed: set[str],
    rewrites: Optional[dict[str, list[dict[str, str]]]] = None,
) -> list[dict[str, str]]:
    """手順を外れたコミット（`{"sha", "problem", "item"}`）。**外れたコミットだけを差し戻す**（#2457 の棚卸し #31）。

    範囲の外の変更は差し戻さず、項目の `outside_scope` に残す（#24）。期待値の欠けはその単位のコミットを差し戻す（#25）。
    `facts` は新しい順。呼び手の書き換えとして通したファイルは `rewrites` へ項目ごとに集める（I5）。
    """
    bad: list[dict[str, str]] = []
    units: dict[str, list[dict[str, Any]]] = {}
    for fact in reversed(facts):
        item_id = str((fact.get("trailers") or {}).get("Item-Id") or "")
        if item_id not in allowed:
            bad.append(
                {
                    "sha": fact["sha"],
                    "item": item_id,
                    "problem": f"コミット {fact['sha'][:7]} の Item-Id（{item_id or 'なし'}）は修正の対象ではありません",
                }
            )
            continue
        problem = verify_commit_basics(fact, "コミットが範囲にありません", check_test=False)
        if problem:
            bad.append({"sha": fact["sha"], "item": item_id, "problem": problem})
            continue
        units.setdefault(item_id, []).append(fact)
    for item_id, unit in units.items():
        item = find_item(state, item_id, required=False) or {}
        context = [*implement_shas(item), *((item.get("commits") or {}).get("fix") or [])]
        verdict = judge_commits(state, [f["sha"] for f in unit], context)
        note_outside(item or None, verdict)
        problem = verify_test_changes(collect_test_changes(unit))
        if problem:
            bad.extend({"sha": f["sha"], "item": item_id, "problem": problem} for f in unit)
        elif verdict.rewrites and rewrites is not None:
            rewrites[item_id] = verdict.rewrites
    return bad


def _inspect_fix_commits(
    state: dict[str, Any],
    work: str,
    fix: dict[str, Any],
    fix_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """修正の起点からコミットを集め、取り込み可否の材料を返す。`known` は範囲を確定できたか。"""
    head = head_sha(work) or ""
    ordered = commits_in_range(work, fix.get("base_sha"), head)
    if ordered is None:
        return {
            "head": head,
            "ordered": [],
            "known": False,
            "problems": [f"修正の範囲を確定できません（起点 {fix.get('base_sha')}）"],
        }
    facts = collect_commit_facts(work, ordered, set(ordered), "", state["head_branch"])
    rewrites: dict[str, list[dict[str, str]]] = {}
    return {
        "head": head,
        "ordered": ordered,
        "known": True,
        "problems": _fix_problems(state, facts, {t["id"] for t in fix_items}, rewrites),
        "rewrites": rewrites,
    }


def _apply_fix_result(
    path: pathlib.Path,
    state: dict[str, Any],
    work: str,
    result: dict[str, Any],
) -> None:
    """手順を外れたコミットだけを打ち消して差し戻しに残し、残りのコミットを項目へ関連付ける（#2457 の棚卸し #31）。"""
    ordered = result["ordered"]
    if not result.get("known"):
        for problem in result["problems"]:
            info(f"❌ {problem}")
        return
    problems = result["problems"]
    bad = {p["sha"] for p in problems}
    if bad:
        before = head_sha(work) or ""
        conflict = revert_range(work, [s for s in ordered if s in bad])  # `ordered` は新しい順
        if conflict:
            reset_hard(work, before)
            info(f"⚠ 手順を外れた修正のコミット {conflict[:12]} を打ち消せません（衝突）。打ち消さずに差し戻しだけを残します")
            bad = set()
        else:
            made = (git_out(work, ["rev-list", f"-{len(bad)}", "HEAD"]) or "").splitlines()
            for sha in reversed(made):
                ledger.note_orchestrator_commit(state, sha)
        for p in problems:
            info(f"↪ {p['problem']}（差し戻し）")
            item = find_item(state, p.get("item") or "", required=False)
            row = {"kind": "fix_procedure", "reason": p["problem"], "commit": p["sha"], "at": statefile.now()}
            (item.setdefault("sendbacks", []) if item is not None else state.setdefault("sendbacks", [])).append(row)
    for sha in reversed(ordered):
        if sha in bad:
            continue
        item_id = str(commit_trailers(work, sha).get("Item-Id") or "").strip()
        item = find_item(state, item_id, required=False)
        if item is not None:
            item["commits"]["fix"].append(sha)
    for item_id, found in (result.get("rewrites") or {}).items():
        item = find_item(state, item_id, required=False)
        if item is not None:
            item.setdefault("review_scope_judgements", []).extend(found)


def _mark_unfixed(
    state: dict[str, Any],
    work: str,
    fix: dict[str, Any],
    fix_items: list[dict[str, Any]],
    result: dict[str, Any],
    left_result: bool,
) -> list[str]:
    """直さなかった項目に `unfixed`（修正の起動の番号）と理由を付け、その ID を返す（#1793 の R3・I5・I6）。

    結果を残して終わった起動で、範囲を確定でき、範囲のコミットの `Item-Id` にその項目が無いものだけを数える。
    **判定は手順を外れて範囲ごと捨てる前のコミットで行う**（直そうとしてコミットした項目は直さなかったと扱わない）。
    """
    if not left_result or not result.get("known"):
        return []
    touched = {str(commit_trailers(work, sha).get("Item-Id") or "").strip() for sha in result["ordered"]}
    attempt = int(fix.get("attempt") or 1)
    unfixed = []
    for item in fix_items:
        if item["id"] in touched:
            continue
        item["unfixed"] = attempt
        unfixed.append(item["id"])
    return unfixed


def _account_fix(state: dict[str, Any], fix_items: list[dict[str, Any]]) -> None:
    """修正回数、項目状態、修正手順の所要時間を更新する。直さなかった項目は `failing` のまま残す。"""
    for item in fix_items:
        item["fix_count"] = int(item.get("fix_count") or 0) + 1
        if item.get("status") == FAILING and not item.get("unfixed"):
            item["status"] = IMPLEMENTED
    stats = state.setdefault("fix_stats", {"launches": 0, "seconds": 0.0})
    started = phase_record(state, "fix").get("launch_started_at")
    seconds = max(clock.seconds_between(started, clock.now()) or 0.0, 0.0)
    stats["launches"] = int(stats.get("launches") or 0) + 1
    stats["seconds"] = round(float(stats.get("seconds") or 0.0) + seconds, 1)
    add_phase_seconds(state, "fix", seconds)


def merge(state_id: Any) -> None:
    """修正の結果を取り込む。取り込んだ項目は `implemented` へ戻り、次の `verify` が見直す。

    手順を外れたコミットだけを差し戻す。修正の回数は数える（報告に出す）。往復を止めるのは前進の判定（#2457 決定 4）である。
    """
    path, state = load_state(state_id)
    work = work_dir(state)
    discard_impl_leftovers(state, work)
    fix = state.get("fix")
    if not fix:
        info("↻ 取り込む修正はありません")
        return
    record_observed_model(state, ran := intake.ran_seat(state, "fix", int(fix.get("attempt") or 1), str(state["implementer"])), "fix")
    note_stopped(state, ran, "fix")
    left_result = read_result(state, ran, "fix").payload is not None
    fix_items = [find_item(state, i, required=False) for i in fix.get("items") or []]
    fix_items = [t for t in fix_items if t is not None]
    result = _inspect_fix_commits(state, work, fix, fix_items)
    unfixed = _mark_unfixed(state, work, fix, fix_items, result, left_result)
    _apply_fix_result(path, state, work, result)
    _account_fix(state, fix_items)
    state["fix"] = None
    statefile.save(path, state)
    info(f"修正を取り込みました（{len(result['ordered'])} コミット / 対象 {len(fix_items)} 件）。{plan_line(state)}")
    if unfixed:
        info(f"⚠ 修正担当が直さなかった項目 {len(unfixed)} 件（{', '.join(unfixed)}）は、次の検証で停滞として数えます")
