"""レビュー worktree の依存の用意（#1337）。作った worktree と使い回す worktree で宣言の依存物を用意する。"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest
import review_lib.workspace as workspace


def _git(*args: str, cwd: pathlib.Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture()
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    main = tmp_path / "main"
    _git("clone", "-q", str(origin), str(main), cwd=tmp_path)
    _git("-c", "user.email=t@e.st", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init", cwd=main)
    _git("push", "-q", "origin", "HEAD:feat/x", cwd=main)
    (main / ".git" / "info" / "exclude").write_text("vendor/\n.ndf/\n", encoding="utf-8")
    pint = main / "vendor" / "bin" / "pint"
    pint.parent.mkdir(parents=True)
    pint.write_text("#!/bin/sh\necho pint-ok\n")
    pint.chmod(0o755)
    monkeypatch.chdir(main)
    return main


def _declare(main: pathlib.Path, deps: dict) -> None:
    (main / ".ndf").mkdir(exist_ok=True)
    (main / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "deps": deps}))


def test_review_worktree_gets_dependencies(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    _declare(repo, {"copy_from_main": ["vendor"]})
    wt = tmp_path / "ndf-worktrees" / "pr1"

    workspace._create_worktree(str(wt), 1, "feat/x")
    workspace._prepare_review_deps(str(wt))

    out = subprocess.run([str(wt / "vendor" / "bin" / "pint")], capture_output=True, text=True)
    assert out.stdout.strip() == "pint-ok"


def test_reused_review_worktree_is_not_prepared_again(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    counter = tmp_path / "count"
    _declare(repo, {"run": [f"echo x >> {counter}"]})
    wt = tmp_path / "ndf-worktrees" / "pr1"
    workspace._create_worktree(str(wt), 1, "feat/x")
    workspace._prepare_review_deps(str(wt))

    workspace._prepare_review_deps(str(wt), if_unprepared=True)

    assert counter.read_text().count("x") == 1


def test_review_worktree_failure_stops_init_and_keeps_worktree(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    _declare(repo, {"run": ["false"]})
    wt = tmp_path / "ndf-worktrees" / "pr1"
    workspace._create_worktree(str(wt), 1, "feat/x")

    with pytest.raises(SystemExit) as e:
        workspace._prepare_review_deps(str(wt))

    assert e.value.code == 1
    assert wt.is_dir()


def test_fork_review_worktree_does_not_run_declared_commands(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    counter = tmp_path / "count"
    _declare(repo, {"run": [f"echo x >> {counter}"]})
    wt = tmp_path / "ndf-worktrees" / "pr1"
    workspace._create_worktree(str(wt), 1, "feat/x")

    workspace._prepare_review_deps(str(wt), is_fork=True)

    assert not counter.exists()
