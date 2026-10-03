"""このリポジトリの宣言の lint の suite が、項目の検証で整形・静的解析の違反を拾う（#464 の AC1〜AC3・AC8）。

一時の git リポジトリへ `scripts/check-lint.sh` を写し、項目のコミットを積んで `targets.verify_runs` が組んだ範囲テストを
走らせる。ツールは差し替えた `uv` が、このリポジトリの lint の依存（`uv.lock`）で起動する。
"""

from __future__ import annotations

import copy
import importlib
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[5]
DECL = json.loads((ROOT / ".ndf" / "project.json").read_text(encoding="utf-8"))
REAL_UV_BIN = shutil.which("uv")

# --project だけを、このリポジトリ（lint の依存を持つ）へ向けて実物の uv へ渡す
REAL_UV = """#!/usr/bin/env bash
args=()
skip=0
for a in "$@"; do
  if [ "$skip" -eq 1 ]; then args+=("$REAL_ROOT"); skip=0; continue; fi
  [ "$a" = "--project" ] && skip=1
  args+=("$a")
done
exec "$REAL_UV_BIN" "${args[@]}"
"""


@pytest.fixture
def targets(refactor):
    return importlib.import_module("refactor_lib.targets")


@pytest.fixture
def ts(refactor):
    return sys.modules["test_strategy"]


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "check-lint.sh", repo / "scripts" / "check-lint.sh")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "mod.py").write_text('VALUE = "ok"\n', encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@e.st", "commit", "-qm", "base")
    return repo


def _commit(repo, files):
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@e.st", "commit", "-qm", "item")
    return _git(repo, "rev-parse", "HEAD")


def _lint_runs(targets, ts, repo, sha):
    state = {"worktrees": {"work": str(repo)}, "strategy": ts.resolve(DECL).as_state()}
    item = {"commits": {"implement": sha}, "scope_commands": []}
    return [r for r in targets.verify_runs(state, item) if r.kind == ts.LINT and "check-lint.sh" in r.command]


def _run(repo, tmp_path, command):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "uv").write_text(REAL_UV, encoding="utf-8")
    (bindir / "uv").chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path), "REAL_ROOT": str(ROOT), "REAL_UV_BIN": str(REAL_UV_BIN)}
    return subprocess.run(command, shell=True, cwd=repo, capture_output=True, text=True, env=env)


def test_the_declaration_has_a_lint_suite_with_a_scope_template(ts):
    """AC1 — `kind: lint` で `{paths}` を持つ suite があり、範囲テストに `check-lint.sh` が入る。"""
    strategy = ts.resolve(DECL)
    lint = [s for s in strategy.suites if s.kind == ts.LINT and "check-lint.sh" in (s.scope_command or "")]
    assert len(lint) == 1 and "{paths}" in lint[0].scope_command
    runs = ts.scope_runs(strategy, [], ["claude", "a.md"])
    assert any(r.command.startswith("bash scripts/check-lint.sh -- ") and "claude" in r.command for r in runs)


@pytest.mark.skipif(REAL_UV_BIN is None, reason="uv が無い")
def test_an_item_with_a_format_violation_fails_its_verification(targets, ts, repo, tmp_path):
    """AC2 — ruff format に違反する `.py` を変えた項目の lint の範囲テストが 0 以外を返す。"""
    sha = _commit(repo, {"pkg/mod.py": "VALUE = {'a':1}\n"})
    runs = _lint_runs(targets, ts, repo, sha)
    assert len(runs) == 1 and "pkg/mod.py" in runs[0].command
    proc = _run(repo, tmp_path, runs[0].command)
    assert proc.returncode == 1, proc.stderr
    assert "ruff format --check" in proc.stderr


@pytest.mark.skipif(REAL_UV_BIN is None, reason="uv が無い")
def test_an_item_touching_only_md_and_json_passes(targets, ts, repo, tmp_path):
    """AC3 — 整形の対象外のファイルだけを変えた項目では、組まれても 0 で通る。"""
    sha = _commit(repo, {"README.md": "# r\n", "conf.json": "{}\n"})
    for run in _lint_runs(targets, ts, repo, sha):
        proc = _run(repo, tmp_path, run.command)
        assert proc.returncode == 0, proc.stderr


def test_existing_suites_build_the_same_commands_as_before(ts):
    """AC8 — lint の suite と `ci_jobs` を足しても、既存の suite の範囲テストと全体テストは同じ文で組まれる。"""
    before = copy.deepcopy(DECL)
    before["test"]["suites"] = [{k: v for k, v in s.items() if k != "ci_jobs"} for s in before["test"]["suites"] if s["name"] != "lint"]
    before["test"].pop("ci_exempt", None)
    files = ["plugins/ndf/scripts/lib/test_strategy.py", "scripts/check-script-structure.py"]
    tests = ["plugins/ndf/scripts/tests/test_lib_test_strategy.py"]
    old, new = ts.resolve(before), ts.resolve(DECL)
    lint_cmd = "bash scripts/check-lint.sh"
    assert [r.command for r in ts.scope_runs(new, tests, files) if not r.command.startswith(lint_cmd)] == [
        r.command for r in ts.scope_runs(old, tests, files)
    ]
    assert [c for c in new.whole_commands() if c != lint_cmd] == old.whole_commands()
    assert lint_cmd in new.whole_commands("lint")
