"""検証（`verify`、#933 の F6・#1793 の R3）。修正の取り込み（`merge-fix`）は `fix_intake.py` にある。

**検証は HEAD で項目ごとの範囲テストを走らせる**（決定 14）。同じ語の並びの項目は
1 回だけ走らせて結果を共有する。全体のテストは、危険フラグが立ったときに検証の中で
1 度だけ走らせる。落ちたら落ちたテストだけを走らせ直して揺れ・元からの失敗・変更が
原因を見分け、変更が原因なら修正へ回す。修正の締め切りまでに通らなければ、
危険フラグの項目を新しい順に 1 件ずつ取り消し、通った時点で止める（決定 22。決定 15 を改めた）。
修正担当が直さなかった項目は、締め切りを待たずに検証の最初に取り消す（#1793 の R3）。

| 返す値 | 意味 | 駆動がすること |
| --- | --- | --- |
| `VERIFY=fix` | 直す項目が残った | 修正を 1 回起動し、`merge-fix` の後に `verify` へ戻る |
| `VERIFY=done` | 残った項目の範囲テストがすべて通った | 採り直しの判定（`readopt`）へ。push は最終ゲートの入口の 1 度だけ |
"""

from __future__ import annotations

import argparse
import pathlib
import time
from typing import Any, Optional

import statefile

from .. import budget, clock, culprit, danger, info, scope_verdict, targets, timeline, triage, wholetest
from ..gitfacts import commit_files, discard_impl_leftovers
from ..items import FAILING, IMPLEMENTED, VERIFIED, in_current_round, item_shas, live_items, newest_first
from ..outbound import item_lines, plan_line
from ..paths import head_sha, load_state, work_dir
from ..phases import add_phase_seconds, finish_phase
from ..undo import drop, resume_pending_drop
from ..fix_intake import UNFIXED_REASON, merge as merge_fix


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

    修正に使える残り（上限の表の `fix_end_at` までの `budget.fix_time_left`。再開でずれた値）が予備時間の `fix`
    （修正 1 回の見積り）に足りなければ打ち切る。範囲テストの修正と全体のテストの直しが同じ判定を使う
    （決定 22）。1 回の修正 = 実装担当の 1 起動で、次の試行の前にここで時計を見る。
    """
    reserve = (state.get("plan") or {}).get("reserve") or {}
    left = budget.fix_time_left(culprit.fix_deadline(state), clock.now())
    return bool(state.get("fix_no_relaunch")) or left < float(reserve.get("fix") or 0.0)  # 旗は振り替え先の無い修正（#919）


STOP_REASON = scope_verdict.STOP_REASON


def _items_in(state: dict[str, Any], status: str) -> list[dict[str, Any]]:
    """生きている項目のうち、状態が `status` のもの。"""
    return [i for i in live_items(state) if i.get("status") == status]


def _give_up(path: pathlib.Path, state: dict[str, Any]) -> bool:
    """修正に使える時間が尽きたら、落ちた項目を取り消す（設計の「検証と修正の繰り返し」2）。取り消したら真。

    理由には落ちた検査（suite・ファイル）を入れる（`scope_verdict.reason`。#1688 I6）。
    """
    if not _fix_stop(state):
        return False
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for item in _items_in(state, FAILING):
        groups.setdefault(targets.command_key(item), []).append(item)
    for group in groups.values():
        _revert_shared(path, state, group, scope_verdict.reason)
    if groups:
        # 時間で直しの試行を打ち切ったこと（報告の「省いたもの」。#1743）
        state.setdefault("fix_stats", {})["cut_off"] = True
    return bool(groups)


def _drop_unfixed(path: pathlib.Path, state: dict[str, Any]) -> None:
    """直さなかった項目（`merge-fix` が `unfixed` を付けた生きている項目）を 1 回の取り消しでまとめて取り消す（#1793 の R3）。

    締め切りを待たない。同じ項目を同じ状態で修正へ渡し直しても結果は変わらないためである。"""
    unfixed = [i for i in live_items(state) if i.get("unfixed")]
    if not unfixed:
        return
    for item in unfixed:
        item["failure_reason"] = UNFIXED_REASON
    info(f"↩ 修正担当が直さなかった項目 {len(unfixed)} 件（{', '.join(i['id'] for i in unfixed)}）を取り消します")
    drop(path, state, [i["id"] for i in unfixed], UNFIXED_REASON)


def _settle_scope(path: pathlib.Path, state: dict[str, Any]) -> None:
    """直さなかった項目を取り消し、判定を待つ項目を範囲テストで判定し、締め切りなら取り消して判定し直す
    （#1688 の順序 1〜4・#1793 の R3 と確かめ直し）。

    取り消しが無くなるまで繰り返す。取り消すたびに項目が減るため、項目の数の回数の内で終わる。
    """
    _drop_unfixed(path, state)
    scope_verdict.judge_items(path, state, scope_verdict.pending(state))
    statefile.save(path, state)
    for _ in range(len(state.get("items") or []) + 1):
        if not _give_up(path, state):
            return
        pending = scope_verdict.pending(state)
        if not pending:
            return
        scope_verdict.judge_items(path, state, pending)
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
    """危険フラグが立ったら全体テストを巡ごとに 1 度だけ走らせる（AC13 AC14。前の巡の記録は `whole_test_history` にある）。

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
    culprit.judge(state, record, record, culprit.fix_deadline(state))  # 修正と取り消しの対象は原因の項目（#1649）
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
    """締め切りの内で原因が決まっていれば原因の項目を修正へ回し（真）、でなければ原因の項目から絞って取り消す（偽）。

    **1 回の修正 = 実装担当の 1 起動である。** 次の試行の前にここで時計を見るため、締め切りを担当の申告に頼らない。
    走らせ直すコマンドは `_rerun_plan` が決め、すべて通ったときだけ通ったとする。
    """
    items = _whole_items(state, record)
    rerun, whole = _rerun_plan(state, record)
    if items and culprit.fixable(record) and not _fix_stop(state):
        for item in items:
            item["status"] = FAILING
            item["last_log"] = str(log)
            item["whole_test_command"] = [culprit.one_command(rerun)]
        info(f"🔧 変更起因の失敗を直しに回します（原因の項目 {len(items)} 件）")
        return True
    narrow_log = pathlib.Path(state["tmp_dir"]) / "verify-whole-narrow.log"
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
    """項目を範囲テストで検証する。出力は `VERIFY=done|fix`。

    終了コード: 0（`VERIFY` で分岐する）/ 4 = 中断（取り消しの失敗など）。
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
    for _ in range(len(state.get("items") or []) + 1):
        _settle_scope(path, state)
        failing = _items_in(state, FAILING)
        if failing:
            _to_fix(path, state, failing, started, "範囲テストが落ちた項目")
            return
        if _whole_test(path, state, _flag_items(state)):
            _to_fix(path, state, _items_in(state, FAILING), started, "全体のテストを落とした原因の項目")
            return
        # 全体テストの取り消しで確かめ直す項目が残ったら、範囲テストの判定へ戻る（全体テストは走らせ直さない）
        if not scope_verdict.pending(state):
            break
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
    """落ちた項目を修正へ回す（`VERIFY=fix`）。修正の起点は今の HEAD。"""
    state["fix"] = {
        "items": [i["id"] for i in failing],
        "base_sha": head_sha(work_dir(state)),
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
