"""全体テストの上限を suite 群で共有する（#1334 の round 2）。

suite ごとに上限を丸ごと渡すと、N 本の suite で上限の N 倍まで走る。test-run.py の whole と同じく、
最終ゲート・危険フラグの全体テスト・着手前のテストも開始時刻からの残りの秒だけを後の suite へ渡す。
"""

from __future__ import annotations

import sys
import types

import pytest


def _clock(monkeypatch, seconds_per_suite):
    """各 suite が `seconds_per_suite` 秒かかる偽の実行。渡した上限の並びを返す。"""
    now = [0.0]
    monkeypatch.setattr(sys.modules["test_triage"].time, "monotonic", lambda: now[0])
    given: list[int] = []

    def run(command, cwd, timeout, *_, **__):
        given.append(timeout)
        if seconds_per_suite > timeout:
            now[0] += timeout
            return None, True
        now[0] += seconds_per_suite
        return 0, False

    return run, given


def _whole_state(monkeypatch, module):
    strategy = types.SimpleNamespace(whole_commands=lambda: ["a", "b", "c"])
    timeline = sys.modules["refactor_lib.timeline"]
    monkeypatch.setattr(timeline, "strategy_of", lambda state: strategy)
    monkeypatch.setattr(timeline, "state_whole_timeout", lambda state: 100)
    monkeypatch.setattr(sys.modules["refactor_lib.triage"], "clear_junit", lambda state: None)
    monkeypatch.setattr(module, "work_dir", lambda state: "/nonexistent")


@pytest.mark.parametrize(("per_suite", "expected"), [(30, [100, 70, 40]), (60, [100, 40])])
def test_local_gate_shares_the_limit_across_suites(refactor, monkeypatch, per_suite, expected):
    gate = sys.modules["refactor_lib.commands.gate"]
    _whole_state(monkeypatch, gate)
    run, given = _clock(monkeypatch, per_suite)
    monkeypatch.setattr(gate, "run_with_timeout", run)
    passed, _, _ = gate._local_gate({})
    assert given == expected
    assert passed is (per_suite == 30)


@pytest.mark.parametrize(("per_suite", "expected"), [(30, [100, 70, 40]), (60, [100, 40])])
def test_run_locally_shares_the_limit_across_suites(refactor, monkeypatch, tmp_path, per_suite, expected):
    wholetest = sys.modules["refactor_lib.wholetest"]
    _whole_state(monkeypatch, wholetest)
    run, given = _clock(monkeypatch, per_suite)
    monkeypatch.setattr(wholetest, "run_with_timeout", run)
    passed, timed_out, _ = wholetest.run_locally({}, tmp_path / "whole.log")
    assert given == expected
    assert (passed, timed_out) == ((True, False) if per_suite == 30 else (False, True))


def test_run_baseline_shares_the_limit_across_suites(refactor, monkeypatch, tmp_path):
    baseline = sys.modules["refactor_lib.baseline"]
    monkeypatch.setattr(baseline, "commands_of", lambda strategy, scope, work: ("whole", ["a", "b", "c"]))
    monkeypatch.setattr(baseline.test_triage, "clear_junit", lambda work, strategy: None)
    run, given = _clock(monkeypatch, 60)
    monkeypatch.setattr(baseline, "run_with_timeout", run)
    with pytest.raises(SystemExit):
        baseline.run_baseline(types.SimpleNamespace(), tmp_path, 100, [], tmp_path)
    assert given == [100, 40], "2 本目で合計の上限に届いて止まる（suite ごとに 100 秒を渡さない）"


def test_a_derived_round_command_does_not_run_the_suites_twice(refactor, tmp_path):
    """宣言から導いた round-only のラウンドテストは全体テストの `&&` 連結で、着手前に 2 度走らせない。"""
    baseline = sys.modules["refactor_lib.baseline"]
    ts = baseline.ts
    strategy = ts.Strategy(
        "round-only", "derived:test.suites", [ts.Suite("a", "run a"), ts.Suite("b", "run b")], round_command="run a && run b"
    )
    assert baseline.commands_of(strategy, [], tmp_path) == ("round", ["run a", "run b"])
