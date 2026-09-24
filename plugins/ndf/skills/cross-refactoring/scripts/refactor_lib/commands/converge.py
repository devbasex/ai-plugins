"""検証と修正（`verify` / `merge-fix` / `merge-test-judgements`、#933 の F6 F7）。

**検証は HEAD で項目ごとの限ったテストを走らせる**（決定 14）。同じ語の並びの項目は
1 回だけ走らせて結果を共有する。全体のテストは、危険の印が立ったときに検証の中で
1 度だけ走らせる。落ちたら印を持つ項目を取り消し、検証の中では走らせ直さない
（決定 15。取り消した後の HEAD は最終ゲートが確かめる）。

| 返す値 | 意味 | 駆動がすること |
| --- | --- | --- |
| `VERIFY=fix` | 直す項目が残った | 修正を 1 回起動し、`merge-fix` の後に `verify` へ戻る |
| `VERIFY=done` | 残った項目の限ったテストがすべて通った | 最終ゲートへ |
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time
from typing import Any, Optional

import jev
import statefile

from .. import budget, clock, danger, info
from ..gitfacts import (
    revert_range,
    collect_commit_facts,
    commit_files,
    commit_trailers,
    commits_in_range,
    discard_impl_leftovers,
    flush_pending_push,
    push_with_retry_marker,
    read_result,
    record_observed_model,
    run_with_timeout,
    safe_int,
)
from ..items import (
    FAILING,
    IMPLEMENTED,
    VERIFIED,
    find_item,
    item_label,
    item_shas,
    live_items,
)
from ..outbound import dropped_line, item_lines, plan_line
from ..paths import git_out, load_state
from ..phases import add_phase_seconds, finish_phase
from ..undo import drop, resume_pending_drop
from ..verify import (
    verify_commit_basics,
    collect_test_changes,
    merge_test_judgements,
    verify_test_changes,
)
from ..vocabulary import DEFAULT_MAX_FIX_ROUNDS, DEFAULT_TEST_TIMEOUT, JEV_RISK_CONFIDENCE


# ---------- 限ったテスト ----------

def _run(state: dict[str, Any], words: list[str], log: pathlib.Path) -> bool:
    """語の並びをシェルを通さずに走らせる（AC10b）。打ち切りは失敗。"""
    timeout = safe_int(state.get("test_timeout"), DEFAULT_TEST_TIMEOUT)
    code, timed_out = run_with_timeout(list(words), str(state["worktrees"]["work"]),
                                       timeout, output=log)
    return (not timed_out) and code == 0


def _log_path(state: dict[str, Any], item_id: str) -> pathlib.Path:
    return pathlib.Path(state["tmp_dir"]) / f"verify-{item_id}.log"


def _run_limited(state: dict[str, Any], items: list[dict[str, Any]]) -> None:
    """項目ごとに限ったテストを走らせ、`verified` / `failing` にする。同じ語の並びは 1 回だけ。"""
    results: dict[tuple[str, ...], tuple[bool, pathlib.Path]] = {}
    for item in items:
        key = tuple(item.get("command") or [])
        if key not in results:
            log = _log_path(state, item["id"])
            results[key] = (_run(state, list(key), log), log)
        passed, log = results[key]
        item["status"] = VERIFIED if passed else FAILING
        item["last_log"] = str(log)
        item["verify_runs"] = int(item.get("verify_runs") or 0) + 1


def _newest_first(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """新しい項目から。実装は順位の順に積まれるため、順位の大きい方が新しい。"""
    return sorted(items, key=lambda i: int(i.get("rank") or 0), reverse=True)


def _revert_shared(
    path: pathlib.Path, state: dict[str, Any], group: list[dict[str, Any]], reason: str,
) -> None:
    """同じ語の並びを共有した項目を、新しい方から 1 件ずつ取り消す（AC15）。

    取り消すたびに共有したコマンドを走らせ直し、通った時点で止める。通る前に取り消した
    項目だけが見送り（`reverted`）になり、古い項目のコミットは残る。走らせ直すのは
    限ったテストで、全体のテストではない。
    """
    remaining = _newest_first(group)
    while remaining:
        target = remaining.pop(0)
        target["failure_reason"] = reason
        drop(path, state, [target["id"]], reason)
        remaining = [i for i in remaining if i.get("status") in (FAILING, IMPLEMENTED, VERIFIED)]
        if not remaining:
            return
        if _run(state, list(remaining[0].get("command") or []), _log_path(state, remaining[0]["id"])):
            for item in remaining:
                item["status"] = VERIFIED
            return


def _give_up(path: pathlib.Path, state: dict[str, Any]) -> None:
    """修正の上限か残り時間が尽きた項目を取り消す（設計の「検証と修正の繰り返し」2）。"""
    limit = safe_int(state.get("max_fix_rounds"), DEFAULT_MAX_FIX_ROUNDS)
    plan = state.get("plan") or {}
    reserve = plan.get("reserve") or {}
    left = budget.fix_time_left(clock.parse(state["started_at"]), int(state["budget_minutes"]),
                                reserve, clock.now())
    no_time = left < float(reserve.get("fix") or 0.0)
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for item in live_items(state):
        if item.get("status") == FAILING:
            groups.setdefault(tuple(item.get("command") or []), []).append(item)
    for group in groups.values():
        exhausted = any(int(i.get("fix_count") or 0) >= limit for i in group)
        if not (exhausted or no_time):
            continue
        reason = (f"修正の上限 {limit} 回に達しても限ったテストが通らなかった" if exhausted
                  else "修正に使える時間が残っていなかった")
        _revert_shared(path, state, group, reason)


# ---------- 危険の印 ----------

def _item_files(work: str, item: dict[str, Any]) -> list[str]:
    files: list[str] = []
    for sha in item_shas(item):
        for f in commit_files(work, sha):
            if f not in files:
                files.append(f)
    return files


def _test_files(state: dict[str, Any], item: dict[str, Any]) -> Optional[list[str]]:
    """D4 で見る限ったテストのファイル（決定 21）。"""
    if item.get("command_source") == "targets":
        return danger.limited_test_files_from_targets(list(item.get("test_targets") or []))
    round_test = (state.get("round_test") or {}).get("command")
    if not round_test:
        return None
    return danger.limited_test_files_from_round_test(round_test, str(state["worktrees"]["work"]))


def _diff_stat(work: str, item: dict[str, Any]) -> str:
    """Jev へ送る `git diff --stat` の行。**差分の本文は送らない**（非機能の条件）。"""
    lines = []
    for sha in item_shas(item):
        out = git_out(work, ["show", "--stat", "--format=", sha]) or ""
        lines.extend(line for line in out.splitlines() if line.strip())
    return "\n".join(lines)


def _d5(state: dict[str, Any], item: dict[str, Any]) -> bool:
    """公開の入出力が変わりうるか。Jev が確信度 0.7 以上で真なら立てる。使えなければ `risk`。

    **`risk` は印を立てる側にだけ使う**（決定 14）。申告で検証を減らさない。
    """
    if (state.get("judge") or {}).get("kind") == "jev":
        text = json.dumps({
            "path": item.get("path"), "symbol": item.get("symbol"),
            "smell": item.get("smell"), "technique": item.get("technique"),
            "rationale": item.get("rationale"), "plan": item.get("plan"),
            "agreed_by": len(item.get("proposed_by") or []),
            "diff_stat": _diff_stat(str(state["worktrees"]["work"]), item),
        }, ensure_ascii=False)
        result = jev.ask_boolean(
            text, "Could this refactoring change the public input or output of the code?")
        if result is not None:
            return bool(result[0] and result[1] >= JEV_RISK_CONFIDENCE)
        judge = state["judge"]
        judge["failures"] = int(judge.get("failures") or 0) + 1
    return bool(item.get("risk"))


def _flag_items(state: dict[str, Any]) -> list[str]:
    """検証を通った項目に危険の印を付け、立った印の集合を返す。**付け済みの項目は見直さない。**"""
    work = str(state["worktrees"]["work"])
    scope = list(state.get("target_scope") or [])
    flags: list[str] = []
    for item in live_items(state):
        if item.get("status") != VERIFIED:
            continue
        if not item.get("danger_checked"):
            found = danger.item_flags(
                work, item, item_shas(item), _item_files(work, item), scope,
                _test_files(state, item), _d5(state, item))
            item["danger"], item["danger_hits"] = found["flags"], found["hits"]
            item["danger_checked"] = True
        flags.extend(f for f in item.get("danger") or [] if f not in flags)
    return sorted(flags)


def _whole_test(path: pathlib.Path, state: dict[str, Any], flags: list[str]) -> None:
    """危険の印が立ったら全体のテストを 1 度だけ走らせる（AC13 AC14）。落ちたら印の項目を取り消す。"""
    record = state.setdefault("whole_test", {})
    if not flags or record.get("ran"):
        return
    command = str((state.get("baseline_test") or {}).get("command") or "")
    work = str(state["worktrees"]["work"])
    info(f"⚠ 危険の印（{', '.join(flags)}）が立ったため、全体のテストを 1 度走らせます: {command}")
    started = time.monotonic()
    log = pathlib.Path(state["tmp_dir"]) / "verify-whole-test.log"
    code, timed_out = run_with_timeout(command, work,
                                       safe_int(state.get("test_timeout"), DEFAULT_TEST_TIMEOUT),
                                       output=log)
    passed = (not timed_out) and code == 0
    record.update({
        "ran": True, "flags": flags, "status": "pass" if passed else "fail",
        "seconds": round(time.monotonic() - started, 1),
        "head": git_out(work, ["rev-parse", "HEAD"]), "reverted": False,
    })
    statefile.save(path, state)
    if passed:
        info("✅ 全体のテストが通りました")
        return
    flagged = [i for i in _newest_first(live_items(state)) if i.get("danger")]
    reason = f"危険の印（{', '.join(flags)}）で走らせた全体のテストが落ちた"
    for item in flagged:
        item["failure_reason"] = reason
    drop(path, state, [i["id"] for i in flagged], reason)
    record["reverted"] = True
    info(f"↩ {dropped_line(state, len(flagged))}。取り消した後の HEAD は最終ゲートが全体のテストで確かめます")


# ---------- verify ----------

def _prepare(path: pathlib.Path, state: dict[str, Any]) -> None:
    discard_impl_leftovers(state, str(state["worktrees"]["work"]))
    resume_pending_drop(path, state)
    flush_pending_push(path, state, state)


def cmd_verify(args: argparse.Namespace) -> None:
    """項目を限ったテストで検証する。出力は `VERIFY=done|fix`。

    終了コード: 0（`VERIFY` で分岐する）/ 4 = 中断（取り消しの失敗など）。
    """
    path, state = load_state(args.id)
    if state.get("fix"):
        # **取り込んでいない修正を先に取り込む。** 修正の後に落ちて再開すると、修正の
        # コミットが項目に結ばれないまま HEAD に残り、検証だけが先に進む。
        info("↻ 取り込んでいない修正があります。先に取り込みます")
        cmd_merge_fix(args)
        path, state = load_state(args.id)
    _prepare(path, state)
    state["phase"] = "verify"
    started = time.monotonic()
    _run_limited(state, [i for i in live_items(state) if i.get("status") == IMPLEMENTED])
    statefile.save(path, state)
    _give_up(path, state)

    failing = [i for i in live_items(state) if i.get("status") == FAILING]
    if failing:
        state["fix"] = {
            "items": [i["id"] for i in failing],
            "base_sha": git_out(str(state["worktrees"]["work"]), ["rev-parse", "HEAD"]),
            "attempt": int((state.get("fix_stats") or {}).get("launches") or 0) + 1,
        }
        _account(state, started)
        statefile.save(path, state)
        info(f"❌ 限ったテストが落ちた項目 {len(failing)} 件を修正へ回します")
        for line in item_lines(state, [i["id"] for i in failing]):
            info(f"   {line}")
        statefile.emit(VERIFY="fix")
        return

    _whole_test(path, state, _flag_items(state))
    _account(state, started)
    finish_phase(state, "verify")
    state["phase"] = "final"
    statefile.save(path, state)
    push_with_retry_marker(path, state, state)
    kept = [i["id"] for i in live_items(state)]
    info(f"✅ 検証を終えました（残った項目 {len(kept)} 件）。{plan_line(state)}")
    for line in item_lines(state, kept):
        info(f"   {line}")
    statefile.emit(VERIFY="done")


def _account(state: dict[str, Any], started: float) -> None:
    """検証の所要を足し込む（配分テーブルの `verify`）。項目は検証した改善項目の数。"""
    seconds = time.monotonic() - started
    stats = state.setdefault("verify_stats", {"items": 0, "seconds": 0.0})
    stats["seconds"] = round(float(stats.get("seconds") or 0.0) + seconds, 1)
    stats["items"] = sum(1 for i in state.get("items") or [] if int(i.get("verify_runs") or 0) > 0)
    add_phase_seconds(state, "verify", seconds)


# ---------- merge-fix ----------

def _fix_problems(
    state: dict[str, Any], facts: list[dict[str, Any]], allowed: set[str],
) -> list[str]:
    """修正のコミットが手順を満たすか。**1 件でも外れたら修正の範囲ごと取り消す。**"""
    scope = list(state.get("target_scope") or [])
    problems = []
    for fact in facts:
        item_id = str((fact.get("trailers") or {}).get("Item-Id") or "")
        if item_id not in allowed:
            problems.append(f"コミット {fact['sha'][:7]} の Item-Id（{item_id or 'なし'}）は修正の対象ではありません")
            continue
        problem = verify_commit_basics(fact, scope, "コミットが範囲にありません", check_test=False)
        problem = problem or verify_test_changes(collect_test_changes([fact]))
        if problem:
            problems.append(problem)
    return problems


def cmd_merge_fix(args: argparse.Namespace) -> None:
    """修正の結果を取り込む。取り込んだ項目は `implemented` へ戻り、次の `verify` が見直す。

    **結果ファイルの申告は使わない。** 修正の起点から HEAD までのコミットを `Item-Id`
    で読み、修正の対象の項目のものだけを受け取る。1 件でも手順を外れたら範囲ごと
    取り消す（どのコミットが安全かを決められないため）。どちらの場合も修正の回数は
    進める（進めないと上限に届かず、検証と修正を往復し続ける）。
    """
    path, state = load_state(args.id)
    work = str(state["worktrees"]["work"])
    discard_impl_leftovers(state, work)
    fix = state.get("fix")
    if not fix:
        info("↻ 取り込む修正はありません")
        return
    record_observed_model(state, str(state["implementer"]), "fix")
    head = git_out(work, ["rev-parse", "HEAD"]) or ""
    ordered = commits_in_range(work, fix.get("base_sha"), head)
    targets = [find_item(state, i, required=False) for i in fix.get("items") or []]
    targets = [t for t in targets if t is not None]
    if ordered is None:
        problems = [f"修正の範囲を確定できません（起点 {fix.get('base_sha')}）"]
        ordered = []
    else:
        facts = collect_commit_facts(work, ordered, set(ordered), "", state["head_branch"])
        problems = _fix_problems(state, facts, {t["id"] for t in targets})
    if problems and ordered:
        for problem in problems:
            info(f"❌ {problem}")
        state["pending_push"] = True
        statefile.save(path, state)
        revert_range(work, ordered, head)
        info(f"↩ 修正の範囲 {len(ordered)} コミットを取り消しました")
    elif ordered:
        for sha in reversed(ordered):
            item_id = str(commit_trailers(work, sha).get("Item-Id") or "").strip()
            item = find_item(state, item_id, required=False)
            if item is not None:
                item["commits"]["fix"].append(sha)
    for item in targets:
        item["fix_count"] = int(item.get("fix_count") or 0) + 1
        if item.get("status") == FAILING:
            item["status"] = IMPLEMENTED
    stats = state.setdefault("fix_stats", {"launches": 0, "seconds": 0.0})
    started = ((state.get("phases") or {}).get("fix") or {}).get("launch_started_at")
    seconds = max(clock.seconds_between(started, clock.now()) or 0.0, 0.0)
    stats["launches"] = int(stats.get("launches") or 0) + 1
    stats["seconds"] = round(float(stats.get("seconds") or 0.0) + seconds, 1)
    add_phase_seconds(state, "fix", seconds)
    state["fix"] = None
    statefile.save(path, state)
    if state.get("pending_push"):
        push_with_retry_marker(path, state, state)
    info(f"修正を取り込みました（{len(ordered)} コミット / 対象 {len(targets)} 件）。{plan_line(state)}")


# ---------- merge-test-judgements ----------

def _read_verdicts(state: dict[str, Any]) -> list[dict[str, Any]]:
    outcome = read_result(state, str(state["implementer"]), "judge-test-changes")
    found = (outcome.payload or {}).get("verdicts")
    return [v for v in found if isinstance(v, dict)] if isinstance(found, list) else []


def cmd_merge_test_judgements(args: argparse.Namespace) -> None:
    """段 2（AI エージェント）の答えを取り込む（#443、実装計画 I7）。

    `changed` の項目は取り消す。`undecidable` と答えの欠けたものは保留を解かず、
    最終ゲートのレビューへ引き継ぐ（`review_test_judgements`）。**答えが欠けたものを
    `unchanged` に倒さない。**
    """
    path, state = load_state(args.id)
    pending = [i for i in live_items(state) if i.get("pending_test_judgements")]
    if not pending:
        info("判定を待っているテストはありません")
        return
    verdicts = _read_verdicts(state)
    changed = []
    for item in pending:
        # **答えは項目ごとに引く。** 2 つの項目が同じテストのファイルを保留にしていると、
        # パスだけで引けば片方の差分への `changed` が両方を取り消す。`item_id` の無い答えは
        # 旧い形として、その項目の答えにも数える。
        mine = [v for v in verdicts if v.get("item_id") in (None, "", item["id"])]
        outcome = merge_test_judgements(item["pending_test_judgements"], mine)
        item.pop("pending_test_judgements", None)
        if outcome["problem"]:
            item["failure_reason"] = outcome["problem"]
            changed.append(item["id"])
        elif outcome["pending"]:
            item["review_test_judgements"] = outcome["pending"]
    statefile.save(path, state)
    if changed:
        drop(path, state, changed, "段 2 の判定でテストの期待する振る舞いが変わっていた")
        info(f"❌ 期待する振る舞いを変えた項目 {len(changed)} 件を取り消しました")
    carried = [i["id"] for i in live_items(state) if i.get("review_test_judgements")]
    if carried:
        info(f"{len(carried)} 件のテストの差分はレビューへ引き継ぎます: {', '.join(carried)}")
