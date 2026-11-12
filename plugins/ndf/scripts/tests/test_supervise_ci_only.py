"""CI テストの宣言（`test.ci.only: true`）のあるリポジトリのプラン（#3056 の I2〜I4・I8）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_supervise import CARMO_PROJECT, cli, foreign_repo  # noqa: E402

pytestmark = pytest.mark.ci_only_route  # 雛形が宣言を読む道を確かめる（conftest の `_local_tests_route` を外す）

LINT = {"name": "pint", "runner": "pint", "kind": "lint", "command": "pint --test", "scope_command": "pint --test {paths}"}
LOCAL_TESTS = ("test-limited", "test-all")


def _repo(tmp_path, only=True):
    root = foreign_repo(tmp_path)
    decl = json.loads(json.dumps(CARMO_PROJECT))
    decl["test"]["suites"].append(LINT)
    if only:
        decl["test"]["ci"]["only"] = True
    (root / ".ndf" / "project.json").write_text(json.dumps(decl))
    return root


def _steps(tmp_path, root, *args):
    out = tmp_path / "plan.json"
    p = cli("new", *args, "--worktree", str(root), "--out", str(out), cwd=root)
    assert p.returncode == 0, p.stdout + p.stderr
    plan = json.loads(out.read_text())
    return plan, {s["id"]: s for s in plan["steps"]}


def _impl(tmp_path, root, *extra):
    return _steps(tmp_path, root, "impl", "--issue", "1", "--tests", "tests/Unit/A.php", "--title", "T", *extra)


def _before_merge(plan) -> list[dict]:
    ids = [s["id"] for s in plan["steps"]]
    return plan["steps"][: ids.index("merge")]


@pytest.mark.parametrize("mode", ["standard", "light"])
def test_impl_plan_has_no_local_tests_and_drafts_the_pr_right_after_impl(tmp_path, mode):
    plan, steps = _impl(tmp_path, _repo(tmp_path), "--mode", mode)
    assert not set(LOCAL_TESTS) & set(steps)
    assert steps["impl"]["next"] == "pr-draft"
    draft = steps["pr-draft"]
    assert draft["type"] == "pr" and draft["body"] == "template" and draft["next"] == "base-sync"
    assert steps["base-sync"]["next"] == "lint-limited" and steps["lint-limited"]["next"] == "lint-all"
    assert steps["lint-all"]["next"] == ("design-tests" if mode == "standard" else "pr")
    assert plan["テストの戦略"]["name"] == "local-scoped-ci-whole"


def test_impl_plan_peeks_without_waiting_and_fixes_through_the_ci_watch(tmp_path):
    plan, steps = _impl(tmp_path, _repo(tmp_path))
    assert steps["pr"]["next"] == "ci-peek" and steps["ci-peek"]["next"] == "doc-lint"
    assert steps["doc-lint"]["next"] == "ci-peek-2" and steps["ci-peek-2"]["next"] == "ready"
    for sid in ("ci-peek", "ci-peek-2", "ci-peek-merge"):
        assert steps[sid]["on_fail"] == "ci-fix" and steps[sid]["on_exit"]["2"] == "stop"
        assert "ci-watch.py peek --pr {pr} --state {state_dir}" in steps[sid]["cmd"]
    assert steps["ci-peek-merge"]["on_exit"]["3"] == "stop" and "--after-merge" in steps["ci-peek-merge"]["cmd"]
    assert steps["ci-fix"]["type"] == "work" and steps["ci-fix"]["next"] == "lint-limited"
    assert steps["merge"]["on_fail"] == steps["merge-approved"]["on_fail"] == "ci-peek-merge"
    for step in _before_merge(plan):
        cmd = step.get("cmd") or ""
        assert ("test-run.py whole" not in cmd or "--only-kind lint" in cmd) and "--watch" not in cmd


def test_impl_plan_runs_the_whole_lint_locally(tmp_path):
    """テストを CI に任せても、`command` を持つ静的解析の suite は手元で全体に打つ（scope_command だけでは見逃す）。"""
    _, steps = _impl(tmp_path, _repo(tmp_path))
    lint = steps["lint-all"]
    assert "test-run.py whole --only-kind lint" in lint["cmd"] and lint["on_fail"] == "fix-lint-all"
    assert steps["fix-lint-all"]["type"] == "work" and steps["fix-lint-all"]["next"] == "lint-limited"


def test_whole_lint_from_the_template_keeps_the_target_paths(tmp_path):
    """宣言にテストの suite だけがあり静的解析を雛形で渡すとき、`lint-all` は計画と同じ対象を `--paths` で渡す（空なら打たずに通る）。"""
    root = foreign_repo(tmp_path)
    decl = json.loads(json.dumps(CARMO_PROJECT))
    decl["test"]["ci"]["only"] = True
    (root / ".ndf" / "project.json").write_text(json.dumps(decl))
    _, steps = _steps(
        tmp_path, root, "impl", "--issue", "1", "--tests", "src", "--title", "T", "--test-kind", "lint", "--test-cmd", "mypy {paths}"
    )
    assert "--only-kind lint" in steps["lint-all"]["cmd"] and "--paths src" in steps["lint-all"]["cmd"]


def test_fix_plan_drafts_the_pr_first(tmp_path):
    plan, steps = _steps(tmp_path, _repo(tmp_path), "fix", "--issue", "1", "--tests", "tests/Unit/A.php", "--title", "T")
    assert plan["steps"][0]["id"] == "pr-draft" and not set(LOCAL_TESTS) & set(steps)


def test_check_plan_brings_back_the_refactoring_and_peeks_before_ready(tmp_path):
    plan, steps = _steps(tmp_path, _repo(tmp_path), "check", "--pr", "5", "--scope", "app", "tests", "--mode", "standard")
    assert {"assess", "refactor"} <= set(steps) and not set(LOCAL_TESTS) & set(steps)
    assert steps["base-sync"]["next"] == "ci-peek" and steps["ci-peek"]["next"] == "ready"
    assert steps["ci-fix"]["next"] == "ci-peek" and "git push" in steps["ci-fix"]["prompt"]
    assert steps["ci-peek"]["on_exit"]["2"] == "abort" and steps["merge"]["on_fail"] == "ci-peek-merge"
    assert "--review" not in steps["record"]["cmd"]


def test_since_last_check_plan_brings_back_the_refactoring(tmp_path):
    root = _repo(tmp_path)
    plan, steps = _steps(tmp_path, root, "check", "--since-last", "--id", "c", "--base", "main", "--mode", "standard")
    assert {"assess", "refactor"} <= set(steps) and "test-all" not in steps
    assert steps["ci-peek"]["next"] == "finish" and steps["base-sync"]["next"] == "ci-peek"
    assert "--review" not in steps["prepare"]["cmd"] and "--review" not in plan["実行の条件"]["cmd"]


@pytest.mark.suspended_refactor
def test_without_the_declaration_the_check_plans_keep_the_refactoring_off(tmp_path):
    root = _repo(tmp_path, only=False)
    _, steps = _steps(tmp_path, root, "check", "--pr", "5", "--scope", "app", "--mode", "standard")
    assert "assess" not in steps and "refactor" not in steps and "ci-peek" not in steps
    assert "--review" in steps["record"]["cmd"]
    plan, steps = _steps(tmp_path, root, "check", "--since-last", "--id", "c", "--base", "main", "--mode", "standard")
    assert "refactor" not in steps and "--review" in plan["実行の条件"]["cmd"]
    _, steps = _impl(tmp_path, root)
    assert "pr-draft" not in steps and "test-limited" in steps


def test_worker_instructions_say_not_to_run_tests_locally(tmp_path):
    from supervise_lib import prompts, worker_steps

    for system in (prompts.WORK_SYSTEM, prompts.FULL_SYSTEM):
        assert prompts.system_for(system, False) == system
        assert prompts.CI_TESTS_LINE in prompts.system_for(system, True)
        assert prompts.LOCAL_TESTS_LINE not in prompts.system_for(system, True)
    plan, _ = _impl(tmp_path, _repo(tmp_path))
    assert worker_steps.ci_only_plan(type("Ctx", (), {"plan": plan})())
    plan, _ = _impl(tmp_path / "b", _repo(tmp_path / "b", only=False))
    assert not worker_steps.ci_only_plan(type("Ctx", (), {"plan": plan})())
