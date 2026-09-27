"""着手前のテスト（#1334 F3）。`commands/setup.py` の `init` が呼ぶ。

戦略ごとに走らせるものが違う。`local-full` は全体テスト、`local-scoped-ci-whole` は `--scope` のテストの置き場所の
範囲テスト（全体テストは手元で走らせない。I4）、`round-only` は全体テストとラウンドテスト。**落ちても止めない。**
落ちたテストは JUnit から読んで既存失敗として書き、最終ゲートは既存失敗の外で新しく落ちたテストが無ければ通る（I5）。
"""

from __future__ import annotations

import pathlib
import time
from typing import Any

import statefile
import test_strategy as ts
import test_triage

from . import ABORT, die, info
from .gitfacts import run_with_timeout
from .paths import git_out
from .scope import test_locations


def commands_of(strategy: ts.Strategy, scope: list[str], work: pathlib.Path) -> tuple[str, list[Any]]:
    """着手前に走らせるもの。`(mode, コマンドの並び)`。mode は `whole` / `scope` / `round`。"""
    if strategy.name == ts.ROUND_ONLY:
        # 全体テストの後にラウンドテストを 1 回。同じコマンドなら 2 度走らせない（#880）。
        # 宣言から導いたラウンドテストは全体テストの `&&` 連結で、中身が同じなのでこれも 2 度走らせない。
        commands = list(strategy.whole_commands())
        if strategy.round_command and strategy.round_command not in commands and strategy.round_command != " && ".join(commands):
            commands.append(strategy.round_command)
        return "round", commands
    if strategy.whole_on_ci:
        locations = test_locations(scope, str(work))
        words = []
        scoped = strategy.scoped_suites()
        for suite in scoped:
            mine = [loc for loc in locations if ts.suite_for(strategy, loc) is suite] or (locations if len(scoped) == 1 else [])
            if mine:
                words.append(ts.scope_words(str(suite.scope_command), mine))
        return "scope", words
    return "whole", strategy.whole_commands()


def run_baseline(strategy: ts.Strategy, work: pathlib.Path, timeout: int, scope: list[str], tmp_dir: pathlib.Path) -> dict[str, Any]:
    """着手前のテストを戦略に沿って実行して記録する。上限を超えたときだけ止める。"""
    mode, commands = commands_of(strategy, scope, work)
    test_triage.clear_junit(str(work), strategy)
    started = time.monotonic()
    status = "green"
    for i, command in enumerate(commands):
        log = tmp_dir / f"init-{mode}-{i}.log"
        # 上限は suite 群全体で 1 つ（test-run.py の whole と同じ）
        code, timed_out = test_triage.run_within(
            timeout, started, lambda left, command=command, log=log: run_with_timeout(command, str(work), left, output=log)
        )
        shown = command if isinstance(command, str) else " ".join(command)
        if timed_out:
            die(f"着手前のテストが {timeout} 秒で終わりませんでした（{shown}）。打ち切りました")
            raise SystemExit(ABORT)
        if code != 0:
            status = "red"
    seconds = round(time.monotonic() - started, 1)
    record: dict[str, Any] = {
        "mode": mode,
        "command": " && ".join(c if isinstance(c, str) else " ".join(c) for c in commands) or None,
        "status": status,
        "checked_at": statefile.now(),
        "seconds": seconds,
        # **HEAD も残す。** 全体テストが落ちたとき、既存失敗かをこの SHA で見分け（決定 22）、報告と改修計画に基準として出す。
        "head": git_out(str(work), ["rev-parse", "HEAD"]),
        "existing_failures": [],
        "existing_failures_reason": None,
    }
    if status == "red":
        ids, reason = test_triage.read_junit(str(work), strategy)
        record["existing_failures"] = ids
        record["existing_failures_reason"] = reason
        shown = f"{len(ids)} 件を既存失敗として記録" if ids is not None else f"落ちたテストを読めない（{reason}）"
        info(
            f"⚠ 着手前のテストが失敗しています（{record['command']}）。{shown}して続けます（既存失敗の外で新しく落ちたテストが無ければ最終ゲートは通ります）"
        )
    elif commands:
        info(f"✅ 着手前のテスト成功: {record['command']}（{seconds} 秒 / {mode}）")
    else:
        info("ℹ 着手前に走らせるテストがありません（--scope のテストの置き場所を受け持つ suite が無い）")
    return record


def round_record(strategy: ts.Strategy, baseline: dict[str, Any]) -> dict[str, Any]:
    """`round-only` のラウンドテストの記録（#880）。着手前のテストがそのまま走らせたので、結果を写す。

    ほかの戦略は `command` を `None` で残す。項目の検証は戦略の雛形で組み立てた語の並びだけを使う（AC10b）。
    """
    command = strategy.round_command if strategy.name == ts.ROUND_ONLY else None
    return {"command": command, "status": baseline["status"], "checked_at": baseline["checked_at"]}
