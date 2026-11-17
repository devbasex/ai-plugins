"""全体テストの guard（#3112）: worker の中で設定の suite の全体の実行を止め、範囲テストと静的解析は通す。"""

from __future__ import annotations

import json

import pytest

from ndf import identity
from ndf.domains.hooks import metrics
from ndf.testing import scoped

from ._hooks import bash, call_entry, deny_reason, records

SCOPE = "uv run --frozen pytest {paths} -q"
SUITES = [
    {"name": "unit", "runner": "pytest", "command": "uv run --frozen pytest . -q -n 4", "scope_command": SCOPE},
    {"name": "lint", "kind": "lint", "runner": "ruff", "command": "uv run --frozen ruff check ."},
]


def _declare(root, ci_only):
    test = {"suites": SUITES, **({"ci": {"only": True}} if ci_only else {})}
    (root / ".ndf").mkdir()
    (root / ".ndf" / "project.json").write_text(json.dumps({"version": 1, "test": test}), encoding="utf-8")


@pytest.mark.parametrize("ci_only", [False, True])
@pytest.mark.parametrize(
    ("command", "denied"),
    [
        ("uv run --frozen pytest . -q -n 4", True),
        ("cd sub && uv run --frozen pytest . -q -n 4 --tb=short | tail -5", True),
        ("uv run --frozen pytest -q", True),
        (scoped.fill_paths(SCOPE, ["tests/test_a.py", "tests/test_b.py"]), False),
        ("uv run --frozen pytest tests/test_a.py -n 4", False),
        ("uv run --frozen ruff check .", False),
        ("git status", False),
    ],
)
def test_worker_is_refused_only_the_whole_suite(tmp_path, monkeypatch, ci_only, command, denied):
    _declare(tmp_path, ci_only)
    monkeypatch.setenv(identity.WORKER_KIND_ENV, "impl")
    reason = deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command)))
    assert (reason is not None) is denied
    if denied:
        assert "全体テスト" in reason and "scope_command" in reason


def test_outside_a_worker_the_whole_suite_runs(tmp_path):
    _declare(tmp_path, False)
    assert call_entry("claude", "PreToolUse", bash(tmp_path, "uv run --frozen pytest . -q -n 4")) is None


def test_refusals_are_recorded_and_counted(tmp_path, monkeypatch):
    _declare(tmp_path, False)
    monkeypatch.setenv(identity.WORKER_KIND_ENV, "impl")
    for command in ("uv run --frozen pytest . -q -n 4", "uv run --frozen pytest -q"):
        assert deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command))) is not None
    assert call_entry("claude", "PreToolUse", bash(tmp_path, SCOPE.format(paths="tests/test_a.py"))) is None
    recs = records()
    assert recs[-2]["notes"]["full_test"] == "uv run --frozen pytest -q" and recs[-2]["notes"]["worker_kind"] == "impl"
    assert metrics.stats(recs)["guards"]["full-test"]["denies"] == 2
