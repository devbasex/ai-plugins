"""着手前のテスト（#1334 F3・#1483）。`commands/setup.py` の `init` が呼ぶ。

戦略ごとに走らせるものが違う。`local-full` は全体テスト、`local-scoped-ci-whole` は `--scope` のテストの置き場所の
範囲テスト（テストの全体テストは手元で走らせない。I4）、`round-only` は全体テストとラウンドテスト。静的解析の
全体テストは戦略に関わらず手元で走らせる（#1483 決定 7）。**落ちても止めない。** suite ごとの成否を
`baseline_test.suites` に書き（最終ゲートの静的解析の判定に使う。I12）、落ちたテストは JUnit から読んで既存失敗として書く。
最終ゲートは既存失敗の外で新しく落ちたテストが無ければ通る（I5）。起動の失敗なら止める（#1483 E7）。
"""

from __future__ import annotations

import pathlib
import time
from typing import Any

import statefile
import test_strategy as ts
import test_triage

from . import ABORT, die, info, launch
from .gitfacts import run_with_timeout
from .paths import git_out
from .scope import test_locations


def _whole_runs(strategy: ts.Strategy, kind: Any = None) -> list[ts.ScopeRun]:
    """全体テストを suite ごとの `ScopeRun` にする（suite に `command` が無ければラウンドテスト）。"""
    runs = [ts.ScopeRun(s.name, s.kind, s.command) for s in strategy.suites if s.command and (kind is None or s.kind == kind)]
    if runs or any(s.command for s in strategy.suites):
        return runs
    return [ts.ScopeRun("round", strategy.round_kind, c) for c in strategy.whole_commands(kind)]


def commands_of(strategy: ts.Strategy, scope: list[str], work: pathlib.Path) -> tuple[str, list[ts.ScopeRun]]:
    """着手前に走らせるもの。`(mode, 範囲テストか全体テストの並び)`。mode は `whole` / `scope` / `round`。"""
    if strategy.name == ts.ROUND_ONLY:
        # 全体テストの後にラウンドテストを 1 回。同じコマンドなら 2 度走らせない（#880）。
        runs = _whole_runs(strategy)
        commands = [r.command for r in runs]
        if strategy.round_command and strategy.round_command not in commands and strategy.round_command != " && ".join(commands):
            runs.append(ts.ScopeRun("round", strategy.round_kind, strategy.round_command))
        return "round", runs
    if strategy.whole_on_ci:
        locations = test_locations(scope, str(work))
        runs = ts.scope_runs(strategy, locations, []) if locations else []
        return "scope", runs + _whole_runs(strategy, ts.LINT)
    return "whole", _whole_runs(strategy)


def _run_suites(
    runs: list[ts.ScopeRun], mode: str, timeout: int, started: float, work: pathlib.Path, tmp_dir: pathlib.Path
) -> tuple[str, dict[str, str]]:
    """runs を走らせて `(テストの成否, suite ごとの成否)` を返す。上限を超えたときと起動の失敗のときは止める。"""
    status = "green"
    suites: dict[str, str] = {}
    for i, run in enumerate(runs):
        log = tmp_dir / f"init-{mode}-{i}.log"
        # 上限は suite 群全体で 1 つ（test-run.py の whole と同じ）
        code, timed_out = test_triage.run_within(
            timeout, started, lambda left, command=run.command, log=log: run_with_timeout(command, str(work), left, output=log)
        )
        result = ts.outcome(code, timed_out)
        if result.status == ts.TIMED_OUT:
            die(f"着手前のテストが {timeout} 秒で終わりませんでした（{run.command}）。打ち切りました")
            raise SystemExit(ABORT)
        if result.launch_failed:
            launch.stop(None, {}, "baseline", run.command, result, log)
        passed = result.status == ts.PASSED
        suites[run.suite] = "green" if passed and suites.get(run.suite) != "red" else "red"
        if not passed and run.kind == ts.TEST:
            status = "red"
    return status, suites


def _report_baseline(record: dict[str, Any], runs: list[ts.ScopeRun], lint_red: list[str], seconds: float, mode: str) -> None:
    if record["status"] == "red":
        ids, reason = record["existing_failures"], record["existing_failures_reason"]
        shown = f"{len(ids)} 件を既存失敗として記録" if ids is not None else f"落ちたテストを読めない（{reason}）"
        info(
            f"⚠ 着手前のテストが失敗しています（{record['command']}）。{shown}して続けます（既存失敗の外で新しく落ちたテストが無ければ最終ゲートは通ります）"
        )
    elif runs and not lint_red:
        info(f"✅ 着手前のテスト成功: {record['command']}（{seconds} 秒 / {mode}）")
    elif not runs:
        info("ℹ 着手前に走らせるテストがありません（--scope のテストの置き場所を受け持つ suite が無い）")
    if lint_red:
        info(f"⚠ 着手前に静的解析の suite（{', '.join(lint_red)}）が落ちています。最終ゲートは変更したファイルに絞って判定します")


def run_baseline(strategy: ts.Strategy, work: pathlib.Path, timeout: int, scope: list[str], tmp_dir: pathlib.Path) -> dict[str, Any]:
    """着手前のテストを戦略に沿って実行して記録する。上限を超えたときと起動の失敗のときだけ止める。"""
    mode, runs = commands_of(strategy, scope, work)
    test_triage.clear_junit(str(work), strategy)
    started = time.monotonic()
    status, suites = _run_suites(runs, mode, timeout, started, work, tmp_dir)
    seconds = round(time.monotonic() - started, 1)
    record: dict[str, Any] = {
        "mode": mode,
        "command": " && ".join(r.command for r in runs) or None,
        "status": status,
        "suites": suites,
        "checked_at": statefile.now(),
        "seconds": seconds,
        # **HEAD も残す。** 全体テストが落ちたとき、既存失敗かをこの SHA で見分け（決定 22）、報告と改修計画に基準として出す。
        "head": git_out(str(work), ["rev-parse", "HEAD"]),
        "existing_failures": [],
        "existing_failures_reason": None,
    }
    lint_red = sorted(r.suite for r in runs if r.kind == ts.LINT and suites.get(r.suite) == "red")
    if status == "red":
        record["existing_failures"], record["existing_failures_reason"] = test_triage.read_junit(str(work), strategy)
    _report_baseline(record, runs, lint_red, seconds, mode)
    return record


def round_record(strategy: ts.Strategy, baseline: dict[str, Any]) -> dict[str, Any]:
    """`round-only` のラウンドテストの記録（#880）。着手前のテストがそのまま走らせたので、結果を写す。

    ほかの戦略は `command` を `None` で残す。項目の検証は戦略の雛形で組み立てた語の並びだけを使う（AC10b）。
    """
    command = " ; ".join(strategy.round_commands()) or None if strategy.name == ts.ROUND_ONLY else None
    return {"command": command, "status": baseline["status"], "checked_at": baseline["checked_at"]}
