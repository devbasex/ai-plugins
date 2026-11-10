"""検証（`verify`、#933 の F6・#2457 決定 3・4）。修正の取り込み（`merge-fix`）は `fix_intake.py` にある。

**検証は HEAD で項目ごとの範囲テストを走らせる**（決定 14）。同じ語の並びの項目は 1 回だけ走らせて結果を共有する。
全体のテストは、危険フラグが立ったときに走らせ、直した後は落ちたテストを確かめ直す。落ちたら落ちたテストを
走らせ直してフレーキー・既存失敗・変更起因を見分け、変更起因なら原因の項目を修正へ回す。

**時計と回数で取り消さない**（#2457 の原則 1・2）。落ちた項目ごとに落ちたテストの集合の履歴を見て（`fixloop`）、
前進する限り修正を続ける。初めて落ちた項目と、停滞か振動した項目は、実装担当と別のランタイムが成立を判定する。
成立しない項目と、修正担当を替えた後も停滞か振動した項目だけを、そのコミットの `git revert` で打ち消す。

| 返す値 | 意味 | 駆動がすること |
| --- | --- | --- |
| `VERIFY=fix` | 直す項目が残った（`FIX_IMPL` が修正担当） | 修正を 1 回起動し、`merge-fix` の後に `verify` へ戻る |
| `VERIFY=judge` | 成立の判定が要る（`JUDGE_IMPL` が判定の担い手） | 判定の CLI を起動し、`judge` の後に `verify` へ戻る |
| `VERIFY=halt` | 打ち消しの衝突か、判定の担い手が居ないまま停滞した（`HALT_REASON`） | 止めて人へ渡す（`gate`） |
| `VERIFY=done` | 残った項目の範囲テストがすべて通った | 採り直しの判定（`readopt`）へ。push は最終ゲートの入口の 1 度だけ |
"""

from __future__ import annotations

import argparse
import pathlib
import time
from typing import Any, Optional

import statefile

from .. import culprit, danger, fixloop, info, progress, scope_verdict, targets, timeline, triage, wholetest
from ..gitfacts import commit_files, discard_impl_leftovers
from ..items import FAILING, VERIFIED, in_current_round, item_shas, live_items, newest_first
from ..outbound import item_lines, plan_line
from ..paths import head_sha, load_state, work_dir
from ..phases import add_phase_seconds, finish_phase
from ..undo import UndoConflict, resume_pending_drop, revert_items
from ..fix_intake import merge as merge_fix


# ---------- 範囲テスト ----------


STOP_REASON = scope_verdict.STOP_REASON


class Halt(Exception):
    """止めて人へ渡す（打ち消しの衝突・判定の担い手が居ないまま停滞。#2457 決定 3・6）。"""


def _items_in(state: dict[str, Any], status: str) -> list[dict[str, Any]]:
    """生きている項目のうち、状態が `status` のもの。"""
    return [i for i in live_items(state) if i.get("status") == status]


def _settle_scope(path: pathlib.Path, state: dict[str, Any]) -> None:
    """判定を待つ項目を範囲テストで判定する（#1688 の順序）。直さなかった項目も取り消さない（停滞として数える）。"""
    scope_verdict.judge_items(path, state, scope_verdict.pending(state))
    statefile.save(path, state)


def _judged_at(item: dict[str, Any], seq: int, rejudge: bool) -> Optional[dict[str, Any]]:
    """この修正の回（`seq`）の判定（判定し直しか初回か）。無ければ `None`。"""
    for entry in reversed(item.get("feasibility") or []):
        if int(entry.get("seq", -1)) == seq and bool(entry.get("rejudge")) == rejudge:
            return entry
    return None


def _needs_judge(item: dict[str, Any], verdict: str) -> Optional[bool]:
    """判定が要るなら判定し直しか（真）初回か（偽）を返す。要らなければ `None`。"""
    seq = int(item.get("fix_count") or 0)
    if verdict in progress.STOPPING:
        return None if _judged_at(item, seq, True) else True
    if not item.get("feasibility"):
        return None if _judged_at(item, seq, False) else False
    return None


def _revert(path: pathlib.Path, state: dict[str, Any], items: list[dict[str, Any]], why: str) -> None:
    """成立しない項目のコミットだけを打ち消す（I4・I5）。衝突したら止めて人へ渡す（I9）。"""
    for item in items:
        item["revert_reason"] = why
    try:
        revert_items(path, state, [i["id"] for i in items], fixloop.REASON_TEXT[why])
    except UndoConflict as exc:
        state["halt"] = {"reason": "revert_conflict", "detail": str(exc), "item": exc.item_id, "at": statefile.now()}
        statefile.save(path, state)
        raise Halt(str(exc)) from None


def _plan_failing(path: pathlib.Path, state: dict[str, Any], failing: list[dict[str, Any]]) -> Optional[str]:
    """落ちた項目ごとに次の手を決める。判定が要れば判定の担い手を返す（`VERIFY=judge`）。打ち消しは済ませる。"""
    wanted: dict[str, bool] = {}
    for item in failing:
        verdict = fixloop.note_round(state, item)
        rejudge = _needs_judge(item, verdict)
        if rejudge is not None:
            wanted[item["id"]] = rejudge
    if wanted:
        judge_by = fixloop.other_runtime(state, [])
        if judge_by is not None:
            state["feasibility_request"] = {"items": sorted(wanted), "rejudge": wanted, "by": judge_by, "at": statefile.now()}
            statefile.save(path, state)
            return judge_by
        for item in failing:
            if item["id"] in wanted:
                fixloop.note_feasibility(item, None, None, "判定の担い手が居ない（参加者が実装担当だけ）", "", wanted[item["id"]])
    revert_now: dict[str, list[dict[str, Any]]] = {}
    for item in failing:
        seq = int(item.get("fix_count") or 0)
        answer = _judged_at(item, seq, True) or _judged_at(item, seq, False)
        if answer is not None and answer.get("feasible") is False:
            revert_now.setdefault(fixloop.INFEASIBLE, []).append(item)
            continue
        verdict = str((item.get("fix_history") or [{}])[-1].get("progress") or progress.FIRST)
        move = fixloop.decide(state, item, verdict)
        if move == fixloop.REVERT:
            revert_now.setdefault(fixloop.STALLED_AFTER_SWITCH, []).append(item)
        elif move == fixloop.SWITCH:
            if answer is not None and answer.get("feasible") is None and answer.get("rejudge"):
                state["halt"] = {
                    "reason": "no_judge",
                    "detail": f"{item['id']} の修正が停滞し、成立を判定し直す担い手が居ない",
                    "item": item["id"],
                    "at": statefile.now(),
                }
                statefile.save(path, state)
                raise Halt(state["halt"]["detail"])
            new = fixloop.switch_fixer(state, item, answer.get("by") if answer else None)
            if new is None:
                state["halt"] = {
                    "reason": "no_fixer",
                    "detail": f"{item['id']} の修正が停滞し、替える修正担当が居ない",
                    "item": item["id"],
                    "at": statefile.now(),
                }
                statefile.save(path, state)
                raise Halt(state["halt"]["detail"])
            info(f"🔁 {item['id']} の修正が{('停滞' if verdict == progress.STALLED else '振動')}したため、修正担当を {new} へ替えます")
    for why, items in revert_now.items():
        _revert(path, state, items, why)
    statefile.save(path, state)
    return None


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
    置き場所の配下の追跡ファイル。コマンドの語からは読まない。対象の無い不要コードの削除（`deletion`）は
    ファイルを持たない（`None`）ので D4 が立ち、全体テストが 1 度走る（#1814 決定 7）。
    """
    if item.get("command_source") == "deletion":
        return None
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
    """検証を通った項目に危険フラグを付け、今の巡で採った項目に立った危険フラグの集合を返す。**付け済みの項目は見直さない。**

    前の巡の項目のフラグは、その巡の全体テストか最終ゲートへ寄せた記録が持つ（#1743 決定 10）。
    """
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
        if in_current_round(state, item):
            flags.extend(f for f in item.get("danger") or [] if f not in flags)
    return sorted(flags)


def _whole_test(path: pathlib.Path, state: dict[str, Any], flags: list[str]) -> bool:
    """危険フラグが立ったら全体テストを巡ごとに走らせる（AC13 AC14。前の巡の記録は `whole_test_history` にある）。
    直した後は落ちたテストを確かめ直す（#2457 の棚卸し #33）。

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
            "head": head_sha(work_dir(state)),
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
    flagged = [i for i in newest_first(live_items(state)) if i.get("danger") and in_current_round(state, i)]
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
    culprit.judge(state, record, record)  # 修正の対象は原因の項目（#1649）
    record["resolution"] = "fixing"
    return _fix_or_narrow(path, state, record, log)


def _whole_items(state: dict[str, Any], record: dict[str, Any]) -> list[dict[str, Any]]:
    ids = set(record.get("items") or [])
    return [i for i in live_items(state) if i["id"] in ids]


def _rerun_plan(state: dict[str, Any], record: dict[str, Any]) -> tuple[Any, bool]:
    """走らせ直すコマンドと、全体テストの上限を使うか。変更起因の suite が無ければ全体テストのコマンドで確かめる。"""
    rerun = culprit.rerun_of(record)
    return rerun or wholetest.whole_fallback_command(state), not rerun


def _fix_or_narrow(
    path: pathlib.Path,
    state: dict[str, Any],
    record: dict[str, Any],
    log: pathlib.Path,
) -> bool:
    """原因の項目を修正へ回す（真）。原因が決まらなければ取り消さず、最終ゲートへ寄せる（偽。#2457 決定 4）。"""
    items = _whole_items(state, record)
    rerun, whole = _rerun_plan(state, record)
    if items and culprit.fixable(record):
        for item in items:
            item["status"] = FAILING
            item["last_log"] = str(log)
            item["whole_test_command"] = [culprit.one_command(rerun)]
            item["failed_check"] = {
                "kind": "test",
                "suite": "whole",
                "command": culprit.one_command(rerun),
                "files": [],
                "tests": list(record.get("caused") or []),
            }
        info(f"🔧 変更起因の失敗を直しに回します（原因の項目 {len(items)} 件）")
        return True
    record["resolution"] = "deferred"
    info("ℹ 全体テストの変更起因の失敗の原因の項目を決められません。取り消さず、最終ゲート修正へ寄せます")
    return False


def _recheck_whole(path: pathlib.Path, state: dict[str, Any], record: dict[str, Any]) -> bool:
    """直した後に、落ちたテストだけを走らせ直す。通れば残し、通らなければ次の試行か絞り込みへ。"""
    items = _whole_items(state, record)
    if not items:
        record["resolution"] = "narrowed"
        return False
    log = pathlib.Path(state["tmp_dir"]) / "verify-whole-rerun.log"
    rerun, whole = _rerun_plan(state, record)
    if targets.run_or_stop(path, state, rerun, log, whole=whole, phase="whole"):
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
    """項目を範囲テストで検証する。出力は `VERIFY=done|fix|judge|halt`。

    終了コード: 0（`VERIFY` で分岐する）/ 4 = 中断（起動の失敗など）。
    """
    path, state = load_state(args.id)
    if state.get("fix"):
        # **取り込んでいない修正を先に取り込む。** 修正の後に落ちて再開すると、修正の
        # コミットが項目に結ばれないまま HEAD に残り、検証だけが先に進む。
        info("↻ 取り込んでいない修正があります。先に取り込みます")
        merge_fix(args.id)
        path, state = load_state(args.id)
    _prepare(path, state)
    state["phase"] = "verify"
    started = time.monotonic()
    state.pop("launch_failure", None)
    try:
        for _ in range(len(state.get("items") or []) + 1):
            _settle_scope(path, state)
            failing = _items_in(state, FAILING)
            if not failing and _whole_test(path, state, _flag_items(state)):
                failing = _items_in(state, FAILING)
            if failing:
                judge_by = _plan_failing(path, state, failing)
                if judge_by is not None:
                    _account(state, started)
                    statefile.save(path, state)
                    info(f"⚖ 成立の判定を {judge_by} に依頼します（{', '.join(state['feasibility_request']['items'])}）")
                    statefile.emit(VERIFY="judge", JUDGE_IMPL=judge_by)
                    return
                failing = _items_in(state, FAILING)
                if failing:
                    _to_fix(path, state, failing, started, "落ちた項目")
                    return
            if not scope_verdict.pending(state):
                break
    except Halt as exc:
        _account(state, started)
        statefile.save(path, state)
        info(f"⏸ 止めて人へ渡します: {exc}")
        statefile.emit(VERIFY="halt", HALT_REASON=str(exc).replace("\n", " "))
        return
    _account(state, started)
    finish_phase(state, "verify")
    # 次は採り直しの判定。公開は最終ゲートの入口の 1 度だけで、巡ごとに push しない（#1399・#1743 決定 10）
    state["phase"] = "readopt"
    statefile.save(path, state)
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
    """落ちた項目を修正へ回す（`VERIFY=fix`）。修正の起点は今の HEAD。修正担当は先頭の項目の担当で、同じ担当の項目をまとめる。"""
    impl = fixloop.fixer_of(state, failing[0])
    group = [i for i in failing if fixloop.fixer_of(state, i) == impl]
    state["fix"] = {
        "items": [i["id"] for i in group],
        "base_sha": head_sha(work_dir(state)),
        "attempt": int((state.get("fix_stats") or {}).get("launches") or 0) + 1,
        "impl": impl,
    }
    _account(state, started)
    statefile.save(path, state)
    info(f"❌ {what} {len(group)} 件を修正へ回します（修正担当 {impl}）")
    for line in item_lines(state, [i["id"] for i in group]):
        info(f"   {line}")
    statefile.emit(VERIFY="fix", FIX_IMPL=impl)


def _account(state: dict[str, Any], started: float) -> None:
    """検証の所要を足し込む（配分テーブルの `verify`）。項目は検証した改善項目の数。"""
    seconds = time.monotonic() - started
    stats = state.setdefault("verify_stats", {"items": 0, "seconds": 0.0})
    stats["seconds"] = round(float(stats.get("seconds") or 0.0) + seconds, 1)
    stats["items"] = sum(1 for i in state.get("items") or [] if int(i.get("verify_runs") or 0) > 0)
    add_phase_seconds(state, "verify", seconds)
