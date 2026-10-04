"""項目の範囲テストの判定（#1688 F4・F5、I4〜I7）。`commands/converge.py` の `verify` が呼ぶ。

項目ごとに範囲テスト（テストの種別と静的解析）を走らせ、`verified` / `failing` にする。同じコマンドの並びは 1 回だけ。
落ちたら落ちた検査（`failed_check`: 種別・suite・コマンド・ファイル・落ちたテスト）を項目に残す。

**落ちたのがテストの種別で、宣言に静的解析の suite があるとき（状態の `strategy.suites` で見る）は原因の項目を決める**
（`culprit.determine`。手がかりは落ちたテストの本文に現れた変えたファイルと、項目を外した走らせ直し）。原因が決まり、
その中に自分が無ければ、項目は取り消さず `implemented` のまま待たせ（`blocked_by`。巻き込まれた項目）、原因の項目を
`failing` にする。原因が決まらない・自分を含む・静的解析の suite が無いときは、従来どおり自分の失敗として扱う。

テストの名前・パスで全体を見るテストを見分けない（AC10）。起動の失敗なら、その項目の状態を変えずに止まる（I8）。
"""

from __future__ import annotations

import pathlib
from typing import Any, Optional

import failure_paths
import junit
import test_strategy as ts
import test_triage

from . import culprit, info, launch, targets, timeline
from .gitfacts import changed_files
from .items import FAILING, IMPLEMENTED, VERIFIED, find_item, item_shas, live_items
from .paths import work_dir

STOP_REASON = "修正に使える時間の内に通らなかった"


def verify_log(state: dict[str, Any], item_id: str) -> pathlib.Path:
    return pathlib.Path(state["tmp_dir"]) / f"verify-{item_id}.log"


def judges_culprits(state: dict[str, Any]) -> bool:
    """原因の判定を行うか（I4・I7）。宣言は読み直さず、状態ファイルの戦略に静的解析の suite があるかで決める。"""
    return any(s.kind == ts.LINT for s in timeline.strategy_of(state).suites)


class _Run:
    """1 つのコマンドの並びの結果（同じ並びの項目で共有する）。"""

    def __init__(self, passed: bool, failed: Optional[ts.ScopeRun], log: pathlib.Path, outcome: ts.Outcome):
        self.passed, self.failed, self.log, self.outcome = passed, failed, log, outcome
        self.tests: list[str] = []
        self.texts: dict[str, str] = {}
        self.note = ""
        self.verdict: Optional[culprit.Verdict] = None


def _suite(state: dict[str, Any], name: str) -> Optional[ts.Suite]:
    return next((s for s in timeline.strategy_of(state).suites if s.name == name), None)


def _read_failures(state: dict[str, Any], run: _Run) -> None:
    """落ちたテストの種別の suite の JUnit から、落ちた ID と本文を読む。読めなければ `note` に理由を残す。"""
    suite = _suite(state, run.failed.suite) if run.failed else None
    if suite is None or not suite.junit:
        run.note = "宣言の suites[].junit が無く、落ちたテストを読めない"
        return
    if run.outcome.status == ts.TIMED_OUT:
        run.note = "範囲テストが上限で打ち切られ、落ちたテストを読めない"
        return
    work = work_dir(state)
    tracked = test_triage.tracked_files(work)
    run.texts = junit.read_failure_texts(pathlib.Path(work) / suite.junit, tracked) or {}
    ids, reason = test_triage.read_junit(work, ts.Strategy("scope", "scope", [suite]), tracked)
    if ids is None or not ids:
        run.note = reason or "JUnit に落ちたテストが無かった"
        return
    run.tests = ids


def _run_scope(path: pathlib.Path, state: dict[str, Any], runs: list[ts.ScopeRun], log: pathlib.Path) -> _Run:
    """範囲テストを 1 本ずつ走らせる（`targets.run_or_stop` と同じ上限と止め方）。落ちた 1 本を残す。"""
    strategy = timeline.strategy_of(state)
    test_triage.clear_junit(work_dir(state), strategy)
    outcome, last = targets.run_commands([r.command for r in runs], work_dir(state), timeline.state_test_timeout(state), log)
    if outcome.launch_failed:
        launch.stop(path, state, "verify", last, outcome, log)
    if outcome.status == ts.PASSED:
        return _Run(True, None, log, outcome)
    failed = next((r for r in runs if r.command == last), None)
    result = _Run(False, failed, log, outcome)
    if failed is not None and failed.kind == ts.TEST:
        _read_failures(state, result)
    return result


def _given(state: dict[str, Any], item: dict[str, Any], run: ts.ScopeRun) -> list[str]:
    """その suite に渡したファイル（静的解析は項目の変えたファイル、テストは対象）。"""
    if run.kind == ts.LINT:
        return targets.lint_files(state, run.suite, changed_files(work_dir(state), item_shas(item)))
    return [str(t).split("::", 1)[0] for t in item.get("test_targets") or []]


def _failed_check(state: dict[str, Any], item: dict[str, Any], run: _Run) -> dict[str, Any]:
    """項目の落ちた検査（`FailedCheck`）。"""
    if run.failed is None:
        return {"kind": "", "suite": "", "command": "", "files": [], "tests": [], "note": run.outcome.reason}
    mine = changed_files(work_dir(state), item_shas(item))
    body = targets.log_text(run.log) + "\n".join(run.texts.values())
    files = failure_paths.mentioned(body, mine) or _given(state, item, run.failed)
    check = {"kind": run.failed.kind, "suite": run.failed.suite, "command": run.failed.command, "files": files, "tests": list(run.tests)}
    if run.outcome.status == ts.TIMED_OUT:
        check["note"] = "上限で打ち切った"
    if run.note:
        check["note"] = run.note
    return check


def _determine(state: dict[str, Any], run: _Run) -> Optional[culprit.Verdict]:
    """テストの種別の失敗から原因の項目を決める（I4）。判定しないときは `None`。"""
    if run.verdict is not None or run.failed is None or run.failed.kind != ts.TEST or not run.tests:
        return run.verdict
    if not judges_culprits(state):
        return None
    run.verdict = culprit.determine(state, run.tests, run.texts, culprit.fix_deadline(state))
    return run.verdict


def _blocked(item: dict[str, Any], verdict: Optional[culprit.Verdict]) -> bool:
    return verdict is not None and verdict.basis != culprit.UNDETERMINED and bool(verdict.culprits) and item["id"] not in verdict.culprits


def _mark_culprits(
    state: dict[str, Any], item: dict[str, Any], run: _Run, verdict: culprit.Verdict, marks: dict[str, dict[str, Any]]
) -> None:
    """巻き込んだ原因の項目へ付ける印を集める（検証の 1 回を終えてから付ける。後の項目の通過で上書きしない）。"""
    for culprit_id in verdict.culprits:
        evidence = verdict.evidence.get(culprit_id) or {}
        marks.setdefault(
            culprit_id,
            {
                "kind": ts.TEST,
                "suite": run.failed.suite if run.failed else "",
                "command": run.failed.command if run.failed else "",
                "files": list(evidence.get("paths") or []),
                "tests": list(evidence.get("tests") or run.tests),
                "by_item": item["id"],
            },
        )


def _settle_item(state: dict[str, Any], item: dict[str, Any], run: _Run, marks: dict[str, dict[str, Any]]) -> None:
    item["last_log"] = str(run.log)
    # 全体のテストの直しで渡したコマンドは、範囲テストの結果で置き換わる。
    item.pop("whole_test_command", None)
    item["verify_runs"] = int(item.get("verify_runs") or 0) + 1
    if run.passed:
        item["status"] = VERIFIED
        item.pop("failed_check", None)
        item.pop("blocked_by", None)
        return
    item["failed_check"] = _failed_check(state, item, run)
    verdict = _determine(state, run)
    if _blocked(item, verdict):
        item["status"] = IMPLEMENTED
        item["blocked_by"] = {"items": list(verdict.culprits), "tests": list(run.tests), "basis": verdict.basis}
        _mark_culprits(state, item, run, verdict, marks)
        info(
            f"⏸ {item['id']} の範囲テストは {', '.join(verdict.culprits)} の変更で落ちました（手がかり {verdict.basis}）。取り消さずに待ちます"
        )
        return
    item["status"] = FAILING
    item.pop("blocked_by", None)


def _run_once(path: pathlib.Path, state: dict[str, Any], items: list[dict[str, Any]]) -> None:
    results: dict[tuple[str, ...], _Run] = {}
    marks: dict[str, dict[str, Any]] = {}
    for item in items:
        runs = targets.verify_runs(state, item)
        key = targets.run_key(runs)
        if key not in results:
            results[key] = _run_scope(path, state, runs, verify_log(state, item["id"]))
        _settle_item(state, item, results[key], marks)
    for culprit_id, check in marks.items():
        target = find_item(state, culprit_id, required=False)
        if target is None or target.get("status") not in (VERIFIED, IMPLEMENTED, FAILING):
            continue
        if target.get("status") != FAILING:
            # 検証を通った（か待っていた）項目が、他の項目の範囲テストを落とした原因に決まった
            target["failed_check"] = check
            target.pop("blocked_by", None)
        target["status"] = FAILING


def waiting(state: dict[str, Any]) -> list[dict[str, Any]]:
    """巻き込まれた項目のうち、原因の項目がどれも `failing` でないもの（走らせ直す。I5）。"""
    out = []
    for item in live_items(state):
        blocked = item.get("blocked_by") or {}
        if item.get("status") != IMPLEMENTED or not blocked:
            continue
        causes = [find_item(state, i, required=False) for i in blocked.get("items") or []]
        if not any(c is not None and c.get("status") == FAILING for c in causes):
            out.append(item)
    return out


def judge_items(path: pathlib.Path, state: dict[str, Any], items: list[dict[str, Any]]) -> None:
    """項目を範囲テストで判定し、原因の項目が同じ回で片づいた巻き込まれた項目を走らせ直す（I5）。

    走らせ直すたびに対象は前の回の部分集合になるため、項目の数の回数の内で終わる。
    """
    pending = list(items)
    for _ in range(len(items) + 1):
        if not pending:
            return
        _run_once(path, state, pending)
        pending = [i for i in waiting(state) if i in pending]


def reason(item: dict[str, Any]) -> str:
    """締め切りで取り消すときの理由（I6）。落ちた検査を読めなければ従来の理由。"""
    check = item.get("failed_check") or {}
    files = ", ".join(check.get("files") or [])
    if check.get("by_item"):
        tests = ", ".join((check.get("tests") or [])[:3])
        return (
            f"範囲テスト {check.get('suite')} で {check['by_item']} のテスト {tests} を落とし（{files or 'ファイル不明'}）、{STOP_REASON}"
        )
    if check.get("kind") == ts.LINT and check.get("suite"):
        return f"静的解析 {check['suite']} が {files or 'ファイル不明'} で落ち、{STOP_REASON}"
    if check.get("kind") == ts.TEST and check.get("suite"):
        tests = ", ".join((check.get("tests") or [])[:3])
        where = f"の {tests}" if tests else ""
        return f"範囲テスト {check['suite']}{where} が{f' {files} で' if files else ''}落ち、{STOP_REASON}"
    return f"範囲テストが{STOP_REASON}"
