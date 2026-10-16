"""依存の用意を worktree の作成箇所から呼ぶ（#1337）。3 層の `ensure_worktree`（W2）と、
着手前の HEAD の一時の worktree（`test_triage.failing_at`。W6）と、Python の包み（`lib/worktree_deps.py`）を見る。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import test_strategy as ts  # noqa: E402
import test_triage  # noqa: E402
import worktree_deps  # noqa: E402
from supervise_lib import paths  # noqa: E402


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def repo_with_vendor(tmp_path: Path, deps: dict | None) -> Path:
    """origin を持つ clone。メインディレクトリに `vendor/bin/pint` を置き、`vendor/` を無視する。"""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    git(origin, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "init")
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
    (repo / ".git" / "info" / "exclude").write_text("vendor/\n.worktrees/\n.ndf/\n", encoding="utf-8")
    pint = repo / "vendor" / "bin" / "pint"
    pint.parent.mkdir(parents=True)
    pint.write_text("#!/bin/sh\necho pint-ok\n", encoding="utf-8")
    pint.chmod(0o755)
    if deps is not None:
        (repo / ".ndf").mkdir()
        (repo / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "deps": deps}), encoding="utf-8")
    return repo


def pint_runs(worktree: Path) -> bool:
    p = subprocess.run([str(worktree / "vendor" / "bin" / "pint")], capture_output=True, text=True)
    return p.stdout.strip() == "pint-ok"


def plan(repo: Path) -> dict:
    return {
        "フェーズ": "試験",
        "課題": [],
        "作業場所": str(repo / ".worktrees" / "feat" / "a"),
        "branch": "feat/a",
        "起点": "origin/main",
        "steps": [{"id": "t", "type": "run", "cmd": "true", "next": "end"}],
    }


# --- W2 -------------------------------------------------------------------------


def test_ensure_worktree_prepares_dependencies(tmp_path: Path) -> None:
    repo = repo_with_vendor(tmp_path, {"copy_from_main": ["vendor"]})
    assert paths.ensure_worktree(plan(repo)) is None
    assert pint_runs(repo / ".worktrees" / "feat" / "a")


def test_ensure_worktree_reports_failure_and_keeps_worktree(tmp_path: Path) -> None:
    repo = repo_with_vendor(tmp_path, {"run": ["exit 7"]})
    err = paths.ensure_worktree(plan(repo))
    assert err is not None and err.startswith("作業ツリーは作ったが依存の用意に失敗した: 依存の用意: 失敗（run[0] exit 7")
    wt = repo / ".worktrees" / "feat" / "a"
    assert wt.is_dir()
    # 使い回すとき、未用意ならやり直す
    (repo / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "deps": {"copy_from_main": ["vendor"]}}), encoding="utf-8")
    assert paths.ensure_worktree(plan(repo)) is None
    assert pint_runs(wt)


def test_ensure_worktree_without_deps_starts_no_extra_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """I1: 宣言が無ければ git の worktree 作成のほかにプロセスを足さない。"""
    repo = repo_with_vendor(tmp_path, None)
    real = subprocess.run
    calls: list[list[str]] = []

    def spy(cmd, **kw):
        calls.append(list(cmd))
        return real(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    assert paths.ensure_worktree(plan(repo)) is None
    assert all(c[0] == "git" for c in calls), calls
    assert not (repo / ".worktrees" / "feat" / "a" / "vendor").exists()


# --- W6 -------------------------------------------------------------------------


def _strategy() -> ts.Strategy:
    suite = ts.Suite(name="unit", command="true", scope_command="vendor/bin/pint {paths}", paths=["t"])
    return ts.Strategy("declared", "test", [suite])


def test_failing_at_prepares_the_baseline_tree(tmp_path: Path) -> None:
    repo = repo_with_vendor(tmp_path, {"copy_from_main": ["vendor"]})
    sha = git(repo, "rev-parse", "HEAD").strip()
    seen: list[bool] = []

    def run(words, cwd, timeout, log):
        seen.append(pint_runs(Path(cwd)))
        return 0, False

    test_triage.failing_at(str(repo), sha, _strategy(), ["t/a.py::x"], 60, tmp_path, run)
    assert seen == [True]


def test_failing_at_cannot_tell_when_preparation_fails(tmp_path: Path) -> None:
    repo = repo_with_vendor(tmp_path, {"run": ["false"]})
    sha = git(repo, "rev-parse", "HEAD").strip()
    ran: list[str] = []
    out = test_triage.failing_at(str(repo), sha, _strategy(), ["t/a.py::x"], 60, tmp_path, lambda *a: ran.append("x") or (1, False))
    assert out is None and ran == []


def test_failing_at_bounds_preparation_by_the_triage_limit(tmp_path: Path) -> None:
    """依存の用意も走らせ直しと同じ上限の中で行い、使い切れば見分けられない（None）。"""
    repo = repo_with_vendor(tmp_path, {"run": ["sleep 30"]})
    sha = git(repo, "rev-parse", "HEAD").strip()
    ran: list[str] = []
    t0 = time.monotonic()
    out = test_triage.failing_at(str(repo), sha, _strategy(), ["t/a.py::x"], 2, tmp_path, lambda *a: ran.append("x") or (1, False))
    assert out is None and ran == []
    assert time.monotonic() - t0 < 20


def test_failing_at_skips_preparation_when_suites_run_in_containers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """コンテナの suite があれば一時の worktree は届かないため、用意の前に見分けられない（None）とする。"""
    counter = tmp_path / "count"
    repo = repo_with_vendor(tmp_path, {"run": [f"echo x >> {counter}"]})
    sha = git(repo, "rev-parse", "HEAD").strip()
    monkeypatch.setattr(test_triage.container_reach, "container_suites", lambda root: [("app", ())])
    ran: list[str] = []
    out = test_triage.failing_at(str(repo), sha, _strategy(), ["t/a.py::x"], 60, tmp_path, lambda *a: ran.append("x") or (1, False))
    assert out is None and ran == []
    assert not counter.exists()


# --- 包み ---------------------------------------------------------------------


def test_wrapper_finds_main_dir_without_git(tmp_path: Path) -> None:
    repo = repo_with_vendor(tmp_path, None)
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "--detach", str(wt), "HEAD")
    assert worktree_deps.main_dir_of(wt) == repo.resolve()
    assert worktree_deps.main_dir_of(repo) == repo.resolve()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (None, False),
        ('{"version": 1}', False),
        ('{"version": 1, "deps": {}}', False),
        ('{"version": 1, "deps": null}', False),
        ('{"version": 1, "deps": {"run": ["true"]}}', True),
        ('{"version": 1, "deps": "x"}', True),
        ("{broken", True),
    ],
)
def test_wrapper_declared(tmp_path: Path, body: str | None, expected: bool) -> None:
    if body is not None:
        (tmp_path / ".ndf").mkdir()
        (tmp_path / ".ndf" / "worktree.json").write_text(body, encoding="utf-8")
    assert worktree_deps.declared(tmp_path) is expected


def test_wrapper_does_not_prepare_the_main_directory(tmp_path: Path) -> None:
    repo = repo_with_vendor(tmp_path, {"run": ["touch RAN"]})
    assert worktree_deps.prepare(repo).ok
    assert not (repo / "RAN").exists()
