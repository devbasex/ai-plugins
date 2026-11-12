"""手元のテストの guard（hook_lib/ci_test_guard.py。#3056 の I9）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_run import SCRIPT, deny_reason  # noqa: E402

PYTEST = {
    "name": "pytest",
    "runner": "pytest",
    "command": "uv run --frozen --project . --all-extras pytest . -q -n 4",
    "scope_command": "uv run --frozen --project . --all-extras pytest {paths} -q -n 4",
}
PHPUNIT = {"name": "phpunit", "runner": "phpunit", "command": "docker compose exec -T app ./vendor/bin/phpunit"}
RUFF = {"name": "ruff", "runner": "ruff", "kind": "lint", "command": "uv run ruff check .", "scope_command": "uv run ruff check {paths}"}


def repo(tmp_path, only=True, suites=(PYTEST, PHPUNIT, RUFF)):
    root = tmp_path / "repo"
    (root / ".ndf").mkdir(parents=True)
    test = {"suites": list(suites), **({"ci": {"only": only}} if only is not None else {})}
    (root / ".ndf" / "project.json").write_text(json.dumps({"test": test}), encoding="utf-8")
    (root / "sub").mkdir()
    return root


def bash(root, command, cwd=None):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(cwd or root), "tool_input": {"command": command}}


@pytest.mark.parametrize(
    "command",
    [
        PYTEST["command"],
        "uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests/test_x.py -q",
        "cd plugins && uv run pytest x -q --tb=short | tail -5",
        "git status; pytest -q",
        "timeout 600 uv run --frozen --project . --all-extras pytest .",
        "bash -c 'uv run pytest x'",
        "docker compose exec -T app ./vendor/bin/phpunit tests/Unit",
        "./vendor/bin/phpunit",
        "uv run --no-sync pytest tests -q",
        "python3 -m pytest x -q",
        "uv run python -m pytest x",
    ],
)
def test_test_suite_launches_are_denied(tmp_path, command):
    root = repo(tmp_path)
    reason = deny_reason(bash(root, command, cwd=root / "sub"), tmp_path)
    assert reason and "gh pr checks" in reason and "merged-steps.py probe" in reason


@pytest.mark.parametrize(
    "command",
    [
        "uv run ruff check plugins",
        "uv pip install pytest",
        "grep -n pytest pyproject.toml",
        "python3 plugins/ndf/scripts/test-run.py scope --paths tests",
        "cat <<'EOF'\npytest -q\nEOF",
        "echo 'uv run pytest'",
        "pip install -U pytest",
        "python3 -m pip install pytest",
    ],
)
def test_lint_and_other_commands_pass(tmp_path, command):
    assert deny_reason(bash(repo(tmp_path), command), tmp_path) is None


GO = {"name": "go", "runner": "go", "command": "go test ./..."}
NPM = {"name": "npm", "runner": "npm", "command": "npm test"}


@pytest.mark.parametrize(
    ("command", "denied"),
    [
        ("go test ./...", True),
        ("go test -run TestX ./pkg", True),
        ("npm test", True),
        ("test -f go.mod && go build ./...", False),
        ("test -d .git", False),
        ("npm run build", False),
        ("go vet ./...", False),
    ],
)
def test_a_form_widened_to_the_second_word_is_matched_by_the_runner(tmp_path, command, denied):
    """起動の形を 2 語目まで広げた suite（`go test`・`npm test`）は、2 語目の `test` をランナーとみなさない。"""
    root = repo(tmp_path, suites=(GO, NPM))
    assert (deny_reason(bash(root, command), tmp_path) is not None) is denied


NPM_RUN = {"name": "npm", "runner": "npm", "command": "npm run test"}


@pytest.mark.parametrize(
    ("command", "denied"),
    [
        ("go test ./...", True),
        ("npm test", True),
        ("npm run test", True),
        ("go build ./...", False),
        ("npm install", False),
        ("npm run lint", False),
    ],
)
def test_a_subcommand_tool_widens_the_form_even_when_the_word_is_a_directory(tmp_path, command, denied):
    """`go`・`npm` のようにサブコマンドを取るツールは、`test/` があっても 2 語目まで広げ、`run` なら 3 語目まで広げる。"""
    root = repo(tmp_path, suites=(GO, NPM, NPM_RUN))
    (root / "test").mkdir()
    assert (deny_reason(bash(root, command), tmp_path) is not None) is denied


PYTEST_DIRECT = {"name": "pytest", "runner": "pytest", "command": "pytest tests -q"}
PYTEST_OPT = {"name": "pytest", "runner": "pytest", "command": "pytest -q"}


@pytest.mark.parametrize("suite", [PYTEST_DIRECT, PYTEST_OPT])
@pytest.mark.parametrize(
    ("command", "denied"),
    [
        ("pytest tests/test_a.py -q", True),
        ("pytest -q", True),
        ("python3 -m pytest x", True),
        ("grep -rn pytest tests", False),
    ],
)
def test_a_path_or_option_second_word_does_not_widen_the_form(tmp_path, suite, command, denied):
    """2 語目がパス（`tests`）かオプション（`-q`）の suite は、起動の形を広げずランナー 1 語で照らす。"""
    root = repo(tmp_path, suites=(suite,))
    (root / "tests").mkdir()
    assert (deny_reason(bash(root, command), tmp_path) is not None) is denied


@pytest.mark.parametrize("only", [None, False])
def test_without_the_declaration_nothing_is_denied(tmp_path, only):
    root = repo(tmp_path, only=only)
    assert deny_reason(bash(root, PYTEST["command"]), tmp_path) is None


def test_codex_shell_list_is_read_and_denied(tmp_path):
    root = repo(tmp_path)
    data = {
        "hook_event_name": "PreToolUse",
        "tool_name": "shell",
        "cwd": str(root),
        "tool_input": {"command": ["bash", "-lc", "uv run pytest x -q"]},
    }
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--runtime", "codex"], input=json.dumps(data), capture_output=True, text=True, timeout=20
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "CI テストの宣言" in out["permissionDecisionReason"]


def test_launch_form_stops_at_the_runner_or_the_paths():
    sys.path.insert(0, str(SCRIPT.parent / "lib"))
    sys.path.insert(0, str(SCRIPT.parent))
    from hook_lib import ci_test_guard as g

    assert g.launch_form(PYTEST) == ["uv", "run", "--frozen", "--project", ".", "--all-extras", "pytest"]
    assert g.launch_form({"runner": "x", "command": "make test", "scope_command": "make test-one {paths}"}) == ["make", "test-one"]
    assert g.launch_form({"runner": "make", "command": "make test"}) == ["make", "test"]
