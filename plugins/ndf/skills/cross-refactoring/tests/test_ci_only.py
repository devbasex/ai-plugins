"""CI テストの宣言（`test.ci.only`）のあるリポジトリの cross-refactoring は手元でテストを走らせない（#3056 の受け入れ条件 11）。"""

from __future__ import annotations

import importlib
import pathlib

import pytest

from crossref_helpers import strategy_state

LINT = {"name": "ruff", "kind": "lint", "command": "ruff check .", "scope_command": "ruff check {paths}", "paths": ["."]}


def _strategy(refactor_lib, name="local-full", ci_only=True):
    ts = importlib.import_module("test_strategy")
    data = strategy_state(name, ci_only=ci_only)
    data["suites"].append(LINT)
    return ts, ts.Strategy.from_state(data)


@pytest.mark.parametrize("name", ["local-full", "local-scoped-ci-whole", "round-only"])
def test_baseline_runs_only_the_lint_suites(refactor_lib, tmp_path, name):
    baseline = importlib.import_module("refactor_lib.baseline")
    ts, strategy = _strategy(refactor_lib, name)
    mode, runs = baseline.commands_of(strategy, ["src"], tmp_path)
    assert [r.kind for r in runs] == [ts.LINT] and runs[0].command == "ruff check ."
    assert baseline.locations_of(strategy, ["src"], tmp_path) == []


def test_baseline_without_the_declaration_still_runs_the_tests(refactor_lib, tmp_path):
    baseline = importlib.import_module("refactor_lib.baseline")
    ts, strategy = _strategy(refactor_lib, ci_only=False)
    _, runs = baseline.commands_of(strategy, ["src"], tmp_path)
    assert ts.TEST in [r.kind for r in runs]


def test_template_probe_is_skipped(refactor_lib):
    template_check = importlib.import_module("refactor_lib.template_check")
    _, strategy = _strategy(refactor_lib)
    assert template_check.targets_of(strategy) == []
    _, strategy = _strategy(refactor_lib, ci_only=False)
    assert template_check.targets_of(strategy) != []


def test_item_verify_runs_only_the_lint_suites(refactor_lib, monkeypatch, tmp_path):
    scope_verdict = importlib.import_module("refactor_lib.scope_verdict")
    ts, strategy = _strategy(refactor_lib)
    state = {"strategy": strategy.as_state(), "work_dir": str(tmp_path)}
    ran = []

    def fake_run(commands, work, timeout, log):
        ran.append(list(commands))
        return ts.outcome(0, False), commands[-1]

    monkeypatch.setattr(scope_verdict.targets, "run_commands", fake_run)
    monkeypatch.setattr(scope_verdict.timeline, "state_test_timeout", lambda state: 60)
    monkeypatch.setattr(scope_verdict, "work_dir", lambda state: str(tmp_path))
    runs = [ts.ScopeRun("pytest", ts.TEST, "pytest a.py"), ts.ScopeRun("ruff", ts.LINT, "ruff check a.py")]
    assert scope_verdict._run_scope(pathlib.Path("s"), state, runs, tmp_path / "log").passed
    assert ran == [["ruff check a.py"]]
    ran.clear()
    assert scope_verdict._run_scope(pathlib.Path("s"), state, runs[:1], tmp_path / "log").passed
    assert ran == []


def test_added_tests_are_not_run(refactor_lib, monkeypatch, tmp_path):
    implement = importlib.import_module("refactor_lib.commands.implement")
    _, strategy = _strategy(refactor_lib)
    monkeypatch.setattr(implement.targets, "run_commands", lambda *a, **k: pytest.fail("手元でテストを走らせた"))
    intake = type("I", (), {"accepted": {"I-001": [{"files": ["tests/test_a.py"]}]}, "test_failed": {}, "sendbacks": {}})()
    implement._run_added_tests(pathlib.Path("s"), {"strategy": strategy.as_state(), "work_dir": str(tmp_path)}, intake)
    assert intake.test_failed == {}
