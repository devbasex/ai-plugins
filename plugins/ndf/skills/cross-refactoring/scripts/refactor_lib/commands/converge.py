"""検証と修正（`verify` / `merge-fix`、#933 の F6 F7）。

**検証は HEAD で項目ごとの範囲テストを走らせる**（決定 14）。同じ語の並びの項目は
1 回だけ走らせて結果を共有する。全体のテストは、危険フラグが立ったときに検証の中で
1 度だけ走らせる。落ちたら落ちたテストだけを走らせ直して揺れ・元からの失敗・変更が
原因を見分け、変更が原因なら修正へ回す。修正の締め切りまでに通らなければ、
危険フラグの項目を新しい順に 1 件ずつ取り消し、通った時点で止める（決定 22。決定 15 を改めた）。

| 返す値 | 意味 | 駆動がすること |
| --- | --- | --- |
| `VERIFY=fix` | 直す項目が残った | 修正を 1 回起動し、`merge-fix` の後に `verify` へ戻る |
| `VERIFY=done` | 残った項目の範囲テストがすべて通った | 最終ゲートへ |
"""

from __future__ import annotations

import argparse
import pathlib
import time
from typing import Any, Optional

import statefile

from .. import budget, clock, culprit, danger, info, publish, scope_verdict, targets, timeline, triage, wholetest
from ..gitfacts import (
    collect_commit_facts,
    commit_files,
    commit_trailers,
    commits_in_range,
    discard_impl_leftovers,
    note_stopped,
    record_observed_model,
)
from ..items import (
    FAILING,
    IMPLEMENTED,
    VERIFIED,
    find_item,
    item_shas,
    live_items,
    newest_first,
)
from ..outbound import item_lines, plan_line
from ..paths import git_out, load_state, work_dir
from ..phases import add_phase_seconds, finish_phase, phase_record
from ..undo import discard_range, drop, resume_pending_drop
from ..verify import (
    verify_commit_basics,
    collect_test_changes,
    verify_test_changes,
)


# ---------- 範囲テスト ----------


def _revert_shared(
    path: pathlib.Path,
    state: dict[str, Any],
    group: list[dict[str, Any]],
    reason: Any,
) -> bool:
    """同じ語の並びを共有した項目を、新しい方から 1 件ずつ取り消す（AC15）。

    `reason` は理由の文か、項目から理由を作る関数（落ちた検査を理由に入れる。#1688 I6）。
    取り消すたびに共有したコマンドを走らせ直し、通った時点で止める。通る前に取り消した
    項目だけが見送り（`reverted`）になり、古い項目のコミットは残る。走らせ直すのは
    範囲テストで、全体のテストではない。
    通った時点で止めたら真、全件を取り消したら偽を返す。
    """
    remaining = newest_first(group)
    while remaining:
        target = remaining.pop(0)
        why = reason(target) if callable(reason) else reason
        target["failure_reason"] = why
        drop(path, state, [target["id"]], why)
        remaining = [i for i in remaining if i.get("status") in (FAILING, IMPLEMENTED, VERIFIED)]
        if not remaining:
            return False
        words = [r.command for r in targets.verify_runs(state, remaining[0])]
        if targets.run_or_stop(path, state, words, scope_verdict.verify_log(state, remaining[0]["id"])):
            for item in remaining:
                item["status"] = VERIFIED
            return True
    return False


def _fix_stop(state: dict[str, Any]) -> bool:
    """修正の試行を打ち切るか。**回数ではなく時計で決める**（決定 23）。

    修正に使える残り（`budget.fix_time_left`）が予備時間の `fix`（修正 1 回の見積り）に
    足りなければ打ち切る。範囲テストの修正と全体のテストの直しが同じ判定を使う
    （決定 22）。1 回の修正 = 実装担当の 1 起動で、次の試行の前にここで時計を見る。
    """
    reserve = (state.get("plan") or {}).get("reserve") or {}
    left = budget.fix_time_left(clock.parse(state["started_at"]), int(state["budget_minutes"]), reserve, clock.now())
    return left < float(reserve.get("fix") or 0.0)


STOP_REASON = scope_verdict.STOP_REASON


def _give_up(path: pathlib.Path, state: dict[str, Any]) -> bool:
    """修正に使える時間が尽きたら、落ちた項目を取り消す（設計の「検証と修正の繰り返し」2）。取り消したら真。

    理由には落ちた検査（suite・ファイル）を入れる（`scope_verdict.reason`。#1688 I6）。
    """
    if not _fix_stop(state):
        return False
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for item in live_items(state):
        if item.get("status") == FAILING:
            groups.setdefault(targets.command_key(item), []).append(item)
    for group in groups.values():
        _revert_shared(path, state, group, scope_verdict.reason)
    return bool(groups)


def _settle_scope(path: pathlib.Path, state: dict[str, Any]) -> None:
    """範囲テストで判定し、締め切りなら取り消し、取り消した後に巻き込まれた項目を走らせ直す（#1688 の順序 1〜4）。

    取り消しが無くなるまで繰り返す。取り消すたびに項目が減るため、項目の数の回数の内で終わる。
    """
    scope_verdict.judge_items(path, state, [i for i in live_items(state) if i.get("status") == IMPLEMENTED])
    statefile.save(path, state)
    for _ in range(len(state.get("items") or []) + 1):
        if not _give_up(path, state):
            return
        waiting = scope_verdict.waiting(state)
        if not waiting:
            return
        scope_verdict.judge_items(path, state, waiting)
        statefile.save(path, state)


# ---------- 危険フラグ ----------


def _item_files(work: str, item: dict[str, Any]) -> list[str]:
    files: list[str] = []
    for sha in item_shas(item):
        for f in commit_files(work, sha):
            if f not in files:
                files.append(f)
    return files


def _test_files(state: dict[str, Any], item: dict[str, Any]) -> Optional[list[str]]:
    """D4 で見る範囲テストのファイル（決定 21・#1334 決定 13）。

    対象から組み立てた項目は対象のパス、ラウンドテストをそのまま走らせる項目は `--scope` のテストの
    置き場所の配下の追跡ファイル。コマンドの語からは読まない。
    """
    if item.get("command_source") == "targets":
        return danger.limited_test_files_from_targets(list(item.get("test_targets") or []))
    return danger.limited_test_files_from_scope(list(state.get("target_scope") or []), work_dir(state))


def _d5(item: dict[str, Any]) -> bool:
    """公開の入出力が変わりうるか。**改修計画の時点で決めた値を読むだけで、LLM へ問わない**（決定 25）。

    改修計画の前に作った状態ファイル（`public_io` を持たない）は実装担当の `risk` を使う。
    `risk` は危険フラグを立てる側にだけ使う（決定 14）。
    """
    return bool(item.get("public_io", item.get("risk")))


def _flag_items(state: dict[str, Any]) -> list[str]:
    """検証を通った項目に危険フラグを付け、立った危険フラグの集合を返す。**付け済みの項目は見直さない。**"""
    work = work_dir(state)
    scope = list(state.get("target_scope") or [])
    flags: list[str] = []
    for item in live_items(state):
        if item.get("status") != VERIFIED:
            continue
        if not item.get("danger_checked"):
            found = danger.item_flags(work, item, item_shas(item), _item_files(work, item), scope, _test_files(state, item), _d5(item))
            item["danger"], item["danger_hits"] = found["flags"], found["hits"]
            item["danger_checked"] = True
        flags.extend(f for f in item.get("danger") or [] if f not in flags)
    return sorted(flags)


def _whole_test(path: pathlib.Path, state: dict[str, Any], flags: list[str]) -> bool:
    """危険フラグが立ったら全体テストを 1 度だけ走らせる（AC13 AC14）。

    落ちたら落ちたテストを見分け（決定 22）、変更起因のものがあれば修正へ回す。
    修正へ回したら真を返す。直しの途中（`resolution: fixing`）なら、全体テストは
    走らせず、落ちたテストだけを走らせ直す。全体テストを CI に任せる戦略では走らせず、最終ゲートへ寄せる。
    """
    record = state.setdefault("whole_test", {})
    if record.get("resolution") == "fixing":
        return _recheck_whole(path, state, record)
    if not flags or record.get("ran"):
        return False
    if timeline.strategy_of(state).whole_on_ci:
        wholetest.defer_to_final_gate(path, state, record, flags, [i["id"] for i in newest_first(live_items(state)) if i.get("danger")])
        return False
    info(f"⚠ 危険フラグ（{', '.join(flags)}）が立ったため、全体テストを 1 度走らせます")
    passed, timed_out, log = _run_whole_locally(path, state, record, flags)
    if passed:
        info("✅ 全体テストが通りました")
        return False
    return _triage_whole(path, state, record, flags, timed_out, log)


def _run_whole_locally(
    path: pathlib.Path, state: dict[str, Any], record: dict[str, Any], flags: list[str]
) -> tuple[bool, bool, pathlib.Path]:
    """全体テストを手元で走らせて記録し、`(通ったか, 打ち切ったか, ログ)` を返す。"""
    started = time.monotonic()
    log = pathlib.Path(state["tmp_dir"]) / "verify-whole-test.log"
    passed, timed_out, commands = wholetest.run_locally(state, log, path)
    record.update(
        {
            "ran": True,
            "flags": flags,
            "commands": commands,
            "status": "pass" if passed else "fail",
            "seconds": round(time.monotonic() - started, 1),
            "head": git_out(work_dir(state), ["rev-parse", "HEAD"]),
            "reverted": False,
        }
    )
    statefile.save(path, state)
    return passed, timed_out, log


def _triage_whole(
    path: pathlib.Path,
    state: dict[str, Any],
    record: dict[str, Any],
    flags: list[str],
    timed_out: bool,
    log: pathlib.Path,
) -> bool:
    """落ちた全体テストを見分け、変更起因のものがあれば修正へ回す。修正へ回したら真。"""
    flagged = [i for i in newest_first(live_items(state)) if i.get("danger")]
    record["items"] = [i["id"] for i in flagged]
    record.update(triage.classify(state, timed_out))
    statefile.save(path, state)
    if record.get("fallback_reason"):
        return wholetest.fallback(path, state, record, flags, flagged, log, _fix_or_narrow)
    info(
        f"🔎 落ちたテスト {len(record['failed_tests'])} 件: フレーキー {len(record['flaky'])} / "
        f"既存失敗 {len(record['preexisting'])} / 変更起因 {len(record['caused'])}"
    )
    if not record["caused"]:
        record["resolution"] = "kept"
        info("✅ 変更起因の失敗はありません。危険フラグの項目は取り消しません（最終ゲートが全体テストを走らせます）")
        return False
    culprit.judge(state, record, record, culprit.fix_deadline(state))  # 修正と取り消しの対象は原因の項目（#1649）
    record["resolution"] = "fixing"
    return _fix_or_narrow(path, state, record, log)


def _whole_items(state: dict[str, Any], record: dict[str, Any]) -> list[dict[str, Any]]:
    ids = set(record.get("items") or [])
    return [i for i in live_items(state) if i["id"] in ids]


def _fix_or_narrow(
    path: pathlib.Path,
    state: dict[str, Any],
    record: dict[str, Any],
    log: pathlib.Path,
) -> bool:
    """締め切りの内で原因が決まっていれば原因の項目を修正へ回し（真）、でなければ原因の項目から絞って取り消す（偽）。

    **1 回の修正 = 実装担当の 1 起動である。** 次の試行の前にここで時計を見るため、締め切りを担当の申告に頼らない。
    走らせ直すのは変更起因の suite ごとのファイル（`culprit.rerun_of`）で、すべて通ったときだけ通ったとする。無ければ
    全体テストのコマンドで確かめる。
    """
    items = _whole_items(state, record)
    rerun = culprit.rerun_of(record) or wholetest.whole_fallback_command(state)
    if items and culprit.fixable(record) and not _fix_stop(state):
        for item in items:
            item["status"] = FAILING
            item["last_log"] = str(log)
            item["whole_test_command"] = [culprit.one_command(rerun)]
        info(f"🔧 変更起因の失敗を直しに回します（原因の項目 {len(items)} 件）")
        return True
    narrow_log, whole = pathlib.Path(state["tmp_dir"]) / "verify-whole-narrow.log", not culprit.rerun_of(record)
    passed = culprit.narrow(path, state, record, STOP_REASON, lambda: targets.run_or_stop(path, state, rerun, narrow_log, whole=whole))
    info(f"↩ 原因の項目から順に取り消しました（{'落ちたテストが通った時点で止めた' if passed else '全件'}）。{plan_line(state)}")
    return False


def _recheck_whole(path: pathlib.Path, state: dict[str, Any], record: dict[str, Any]) -> bool:
    """直した後に、落ちたテストだけを走らせ直す。通れば残し、通らなければ次の試行か絞り込みへ。"""
    items = _whole_items(state, record)
    if not items:
        record["resolution"] = "narrowed"
        return False
    log = pathlib.Path(state["tmp_dir"]) / "verify-whole-rerun.log"
    rerun = culprit.rerun_of(record) or wholetest.whole_fallback_command(state)
    if targets.run_or_stop(path, state, rerun, log, whole=not culprit.rerun_of(record), phase="whole"):
        record["resolution"] = "fixed"
        for item in items:
            item.pop("whole_test_command", None)
        info("✅ 変更起因で落ちたテストが、直した後に通りました")
        return False
    return _fix_or_narrow(path, state, record, log)


# ---------- verify ----------


def _prepare(path: pathlib.Path, state: dict[str, Any]) -> None:
    discard_impl_leftovers(state, work_dir(state))
    resume_pending_drop(path, state)


def cmd_verify(args: argparse.Namespace) -> None:
    """項目を範囲テストで検証する。出力は `VERIFY=done|fix`。

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
    state.pop("launch_failure", None)
    _settle_scope(path, state)

    failing = [i for i in live_items(state) if i.get("status") == FAILING]
    if failing:
        _to_fix(path, state, failing, started, "範囲テストが落ちた項目")
        return

    if _whole_test(path, state, _flag_items(state)):
        failing = [i for i in live_items(state) if i.get("status") == FAILING]
        _to_fix(path, state, failing, started, "全体のテストを落とした原因の項目")
        return
    _account(state, started)
    finish_phase(state, "verify")
    state["phase"] = "final"
    statefile.save(path, state)
    # 最終ゲートへ入る時点の公開が、実行で最初の push になる（#1399）
    publish.enter_final_gate(path, state)
    kept = [i["id"] for i in live_items(state)]
    info(f"✅ 検証を終えました（残った項目 {len(kept)} 件）。{plan_line(state)}")
    for line in item_lines(state, kept):
        info(f"   {line}")
    statefile.emit(VERIFY="done")


def _to_fix(
    path: pathlib.Path,
    state: dict[str, Any],
    failing: list[dict[str, Any]],
    started: float,
    what: str,
) -> None:
    """落ちた項目を修正へ回す（`VERIFY=fix`）。修正の起点は今の HEAD。"""
    state["fix"] = {
        "items": [i["id"] for i in failing],
        "base_sha": git_out(work_dir(state), ["rev-parse", "HEAD"]),
        "attempt": int((state.get("fix_stats") or {}).get("launches") or 0) + 1,
    }
    _account(state, started)
    statefile.save(path, state)
    info(f"❌ {what} {len(failing)} 件を修正へ回します")
    for line in item_lines(state, [i["id"] for i in failing]):
        info(f"   {line}")
    statefile.emit(VERIFY="fix")


def _account(state: dict[str, Any], started: float) -> None:
    """検証の所要を足し込む（配分テーブルの `verify`）。項目は検証した改善項目の数。"""
    seconds = time.monotonic() - started
    stats = state.setdefault("verify_stats", {"items": 0, "seconds": 0.0})
    stats["seconds"] = round(float(stats.get("seconds") or 0.0) + seconds, 1)
    stats["items"] = sum(1 for i in state.get("items") or [] if int(i.get("verify_runs") or 0) > 0)
    add_phase_seconds(state, "verify", seconds)


# ---------- merge-fix ----------


def _fix_problems(
    state: dict[str, Any],
    facts: list[dict[str, Any]],
    allowed: set[str],
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


def _inspect_fix_commits(
    state: dict[str, Any],
    work: str,
    fix: dict[str, Any],
    fix_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """修正の起点からコミットを集め、取り込み可否の材料を返す。"""
    head = git_out(work, ["rev-parse", "HEAD"]) or ""
    ordered = commits_in_range(work, fix.get("base_sha"), head)
    if ordered is None:
        return {
            "head": head,
            "ordered": [],
            "problems": [f"修正の範囲を確定できません（起点 {fix.get('base_sha')}）"],
        }
    facts = collect_commit_facts(work, ordered, set(ordered), "", state["head_branch"])
    return {
        "head": head,
        "ordered": ordered,
        "problems": _fix_problems(state, facts, {t["id"] for t in fix_items}),
    }


def _apply_fix_result(
    path: pathlib.Path,
    state: dict[str, Any],
    work: str,
    result: dict[str, Any],
) -> None:
    """違反した修正を取り消し、または採用したコミットを項目へ関連付ける。"""
    ordered = result["ordered"]
    problems = result["problems"]
    if problems and ordered:
        for problem in problems:
            info(f"❌ {problem}")
        # 修正のコミットはどの改善項目にも記録されていないため、取り消しの判定が消す
        discard_range(path, state, "手順を外れた修正")
        info(f"↩ 修正の範囲 {len(ordered)} コミットを取り消しました")
        return
    if ordered:
        for sha in reversed(ordered):
            item_id = str(commit_trailers(work, sha).get("Item-Id") or "").strip()
            item = find_item(state, item_id, required=False)
            if item is not None:
                item["commits"]["fix"].append(sha)


def _account_fix(state: dict[str, Any], fix_items: list[dict[str, Any]]) -> None:
    """修正回数、項目状態、修正手順の所要時間を更新する。"""
    for item in fix_items:
        item["fix_count"] = int(item.get("fix_count") or 0) + 1
        if item.get("status") == FAILING:
            item["status"] = IMPLEMENTED
    stats = state.setdefault("fix_stats", {"launches": 0, "seconds": 0.0})
    started = phase_record(state, "fix").get("launch_started_at")
    seconds = max(clock.seconds_between(started, clock.now()) or 0.0, 0.0)
    stats["launches"] = int(stats.get("launches") or 0) + 1
    stats["seconds"] = round(float(stats.get("seconds") or 0.0) + seconds, 1)
    add_phase_seconds(state, "fix", seconds)


def cmd_merge_fix(args: argparse.Namespace) -> None:
    """修正の結果を取り込む。取り込んだ項目は `implemented` へ戻り、次の `verify` が見直す。

    **結果ファイルの申告は使わない。** 修正の起点から HEAD までのコミットを `Item-Id`
    で読み、修正の対象の項目のものだけを受け取る。1 件でも手順を外れたら範囲ごと
    取り消す（どのコミットが安全かを決められないため）。どちらの場合も修正の回数は
    数える（報告に出す）。往復を止めるのは締め切りである（決定 23）。
    """
    path, state = load_state(args.id)
    work = work_dir(state)
    discard_impl_leftovers(state, work)
    fix = state.get("fix")
    if not fix:
        info("↻ 取り込む修正はありません")
        return
    record_observed_model(state, str(state["implementer"]), "fix")
    note_stopped(state, str(state["implementer"]), "fix")
    fix_items = [find_item(state, i, required=False) for i in fix.get("items") or []]
    fix_items = [t for t in fix_items if t is not None]
    result = _inspect_fix_commits(state, work, fix, fix_items)
    _apply_fix_result(path, state, work, result)
    _account_fix(state, fix_items)
    state["fix"] = None
    statefile.save(path, state)
    info(f"修正を取り込みました（{len(result['ordered'])} コミット / 対象 {len(fix_items)} 件）。{plan_line(state)}")
