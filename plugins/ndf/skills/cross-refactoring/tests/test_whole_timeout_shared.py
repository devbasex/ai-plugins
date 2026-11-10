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
    strategy = types.SimpleNamespace(whole_commands=lambda kind=None: [] if kind == "lint" else ["a", "b", "c"])
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
    passed, _, _ = gate._local_gate(None, {})
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
    runs = [baseline.ts.ScopeRun(c, "test", c) for c in ("a", "b", "c")]
    monkeypatch.setattr(baseline, "commands_of", lambda strategy, scope, work: ("whole", runs))
    monkeypatch.setattr(baseline.test_triage, "clear_junit", lambda work, strategy: None)
    run, given = _clock(monkeypatch, 60)
    monkeypatch.setattr(baseline, "run_with_timeout", run)
    record = baseline.run_baseline(types.SimpleNamespace(), tmp_path, 100, [], tmp_path)
    assert given == [100, 40], "2 本目で合計の上限に届いて打ち切る（suite ごとに 100 秒を渡さない）"
    assert record["timed_out"] is True and record["seconds"] == 100.0


def test_a_derived_round_command_does_not_run_the_suites_twice(refactor, tmp_path):
    """宣言から導いた round-only のラウンドテストは全体テストの `&&` 連結で、着手前に 2 度走らせない。"""
    baseline = sys.modules["refactor_lib.baseline"]
    ts = baseline.ts
    strategy = ts.Strategy(
        "round-only", "derived:test.suites", [ts.Suite("a", "run a"), ts.Suite("b", "run b")], round_command="run a && run b"
    )
    mode, runs = baseline.commands_of(strategy, [], tmp_path)
    assert (mode, [r.command for r in runs]) == ("round", ["run a", "run b"])


def _two_suites(ts):
    return ts.Strategy(
        "local-full",
        "test",
        [ts.Suite("a", "run a", "run-a {paths}", paths=["tests/a"]), ts.Suite("b", "run b", "run-b {paths}", paths=["tests/b"])],
    )


def test_targets_across_suites_run_each_suite_with_its_own_template(refactor, monkeypatch, tmp_path):
    """1 項目の `test_targets` が 2 つの suite にまたがれば、suite ごとの雛形で 2 本を組んで両方走らせる（#1354）。"""
    targets = sys.modules["refactor_lib.targets"]
    strategy = _two_suites(targets.ts)
    for d in ("a", "b"):
        (tmp_path / "tests" / d).mkdir(parents=True)
        (tmp_path / "tests" / d / "test_t.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(targets, "strategy_of", lambda state: strategy)
    runs, origin = targets.limited_runs({"target_scope": ["tests"]}, ["tests/a/test_t.py", "tests/b/test_t.py::y"], str(tmp_path))
    assert origin == "targets"
    commands = [r.command for r in runs]
    assert commands == ["run-a tests/a/test_t.py", "run-b tests/b/test_t.py"]
    assert targets.command_key({"scope_commands": targets.runs_state(runs)}) == tuple(commands)

    single, _ = targets.limited_runs({"target_scope": ["tests"]}, ["tests/a/test_t.py"], str(tmp_path))
    assert [r.command for r in single] == ["run-a tests/a/test_t.py"]

    run, given = _clock(monkeypatch, 30)
    seen: list[str] = []
    monkeypatch.setattr(targets, "run_with_timeout", lambda words, cwd, timeout, **kw: (seen.append(words), run(words, cwd, timeout))[1])
    result, _ = targets.run_commands(commands, str(tmp_path), 100, tmp_path / "verify.log")
    assert result.status == "passed"
    assert seen == commands
    assert given == [100, 70], "suite 群で 1 つの上限を分け合う"


_UNITTEST_WRAPPER = """import sys
bad = [a for a in sys.argv[1:] if "::" in a]
if bad:
    print("unittest cannot select: " + bad[0])
    sys.exit(2)
"""


def test_a_unittest_wrapper_does_not_reject_node_ids_from_the_plan(refactor, monkeypatch, tmp_path):
    """#1793 の R5（PR 254 の形）— unittest の包みの雛形と `::` 付きの `test_targets` で、範囲テストが選択子を拒まない。"""
    targets = sys.modules["refactor_lib.targets"]
    ts = targets.ts
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_episodes_pipeline.py").write_text("", encoding="utf-8")
    (tmp_path / "unit.py").write_text(_UNITTEST_WRAPPER, encoding="utf-8")
    strategy = ts.Strategy("local-full", "test", [ts.Suite("unit", "python3 unit.py", f"{sys.executable} unit.py {{paths}}")])
    monkeypatch.setattr(targets, "strategy_of", lambda state: strategy)
    runs, origin = targets.limited_runs({"target_scope": ["tests"]}, ["tests/test_episodes_pipeline.py::Check"], str(tmp_path))
    assert origin == "targets" and all("::" not in r.command for r in runs)
    result, _ = targets.run_commands([r.command for r in runs], str(tmp_path), 60, tmp_path / "verify.log")
    assert result.status == "passed"
    assert "cannot select" not in (tmp_path / "verify.log").read_text(encoding="utf-8")


def test_whole_fallback_reruns_every_suite(refactor, monkeypatch, tmp_path):
    """JUnit で見分けられないときの再検証は全 suite を走らせる。2 本目だけが落ちても直ったと誤らない。"""
    import subprocess

    wholetest = sys.modules["refactor_lib.wholetest"]
    ts = wholetest.timeline.ts
    monkeypatch.setattr(wholetest.timeline, "strategy_of", lambda state: ts.Strategy("local-full", "test", [ts.Suite("a", "run a")]))
    assert wholetest.whole_fallback_command({}) == "run a"
    two = ts.Strategy("local-full", "test", [ts.Suite("a", "true"), ts.Suite("b", "false")])
    monkeypatch.setattr(wholetest.timeline, "strategy_of", lambda state: two)
    command = wholetest.whole_fallback_command({})
    assert command == "( true ) && ( false )"
    assert subprocess.run(command, shell=True, cwd=tmp_path).returncode != 0
