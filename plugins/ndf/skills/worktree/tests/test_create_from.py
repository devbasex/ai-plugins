"""`worktree-setup.sh create` の起点の指定を検証する（issue #1005）。

ミッションのブランチ（`mission/<名前>`）は宣言の base_branch（develop）から切り、課題の
作業ツリーは `--from` にミッションのブランチを渡して切る。宣言の base_branch は変えない。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from worktree_helpers import SCRIPTS_DIR, add_origin, git, push_branch, write_declaration

SETUP = SCRIPTS_DIR / "worktree-setup.sh"


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    return subprocess.run(
        ["bash", str(SETUP), *args], cwd=str(cwd), env=env, capture_output=True, text=True,
    )


def prepare(main_repo: Path) -> None:
    (main_repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    git(main_repo, "add", ".gitignore")
    git(main_repo, "commit", "-q", "-m", "ignore")
    add_origin(main_repo)
    git(main_repo, "checkout", "-q", "-b", "develop")
    (main_repo / "dev.txt").write_text("dev\n", encoding="utf-8")
    git(main_repo, "add", "dev.txt")
    git(main_repo, "commit", "-q", "-m", "dev")
    git(main_repo, "push", "-q", "origin", "develop")
    git(main_repo, "checkout", "-q", "main")
    git(main_repo, "branch", "-q", "-D", "develop")
    write_declaration(main_repo, json.dumps({"version": 1, "base_branch": "develop"}))


def head(repo: Path, ref: str = "HEAD") -> str:
    return git(repo, "rev-parse", ref).stdout.strip()


def test_without_from_starts_at_base_branch(main_repo: Path) -> None:
    prepare(main_repo)
    got = run(["create", "mission/m1"], cwd=main_repo)
    assert got.returncode == 0, got.stderr
    target = main_repo / ".worktrees" / "mission" / "m1"
    assert head(target) == head(main_repo, "origin/develop")
    assert "起点: develop" in got.stdout


def test_from_starts_at_the_mission_branch(main_repo: Path) -> None:
    prepare(main_repo)
    assert run(["create", "mission/m1"], cwd=main_repo).returncode == 0
    mission = main_repo / ".worktrees" / "mission" / "m1"
    (mission / "m.txt").write_text("m\n", encoding="utf-8")
    git(mission, "add", "m.txt")
    git(mission, "commit", "-q", "-m", "mission")

    got = run(["create", "feat/issue-1", "--from", "mission/m1"], cwd=main_repo)
    assert got.returncode == 0, got.stderr
    issue = main_repo / ".worktrees" / "feat" / "issue-1"
    assert head(issue) == head(mission)
    assert "起点: mission/m1" in got.stdout
    # 宣言の base_branch は変えない
    decl = json.loads((main_repo / ".ndf" / "worktree.json").read_text(encoding="utf-8"))
    assert decl["base_branch"] == "develop"


def test_from_uses_the_remote_mission_branch(main_repo: Path) -> None:
    prepare(main_repo)
    push_branch(main_repo, "mission/m2")
    got = run(["create", "feat/issue-2", "--from", "mission/m2"], cwd=main_repo)
    assert got.returncode == 0, got.stderr
    assert head(main_repo / ".worktrees" / "feat" / "issue-2") == head(main_repo, "origin/mission/m2")


def test_missing_start_branch_fails(main_repo: Path) -> None:
    prepare(main_repo)
    got = run(["create", "feat/issue-3", "--from", "mission/none"], cwd=main_repo)
    assert got.returncode == 1
    assert not (main_repo / ".worktrees" / "feat" / "issue-3").exists()


def test_from_is_only_for_create(main_repo: Path) -> None:
    prepare(main_repo)
    assert run(["status", "--from", "mission/m1"], cwd=main_repo).returncode == 1


def test_unregistered_worktree_dir_fails(main_repo: Path) -> None:
    add_origin(main_repo)
    got = run(["create", "feat/issue-4"], cwd=main_repo)
    assert got.returncode == 1
    assert not (main_repo / ".worktrees").exists()
