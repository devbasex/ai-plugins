"""#3309: `wt_default_branch` は既定ブランチの候補を自分で並べず、`branch_flow.DEFAULT_BRANCH_CANDIDATES` から読む。

origin の HEAD を解けない一時リポジトリで、候補の順に存在するローカルのブランチが選ばれることを確かめる。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
COMMON = LIB / "worktree-common.sh"
sys.path.insert(0, str(LIB))
from branch_flow import DEFAULT_BRANCH_CANDIDATES  # noqa: E402


def make_repo(tmp_path: Path, branches: list[str]) -> Path:
    """origin を持たないリポジトリ。`branches` の先頭で初期化し、残りを枝として作る。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)  # noqa: E731
    run("init", "-q", "-b", branches[0])
    run("-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "base")
    for name in branches[1:]:
        run("branch", name)
    return repo


def default_branch(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'. "{COMMON}" || exit 9; wt_default_branch "{repo}"'],
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("present", [list(DEFAULT_BRANCH_CANDIDATES), list(reversed(DEFAULT_BRANCH_CANDIDATES))])
def test_first_candidate_wins_when_all_exist(tmp_path: Path, present: list[str]) -> None:
    got = default_branch(make_repo(tmp_path, present))
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == DEFAULT_BRANCH_CANDIDATES[0]


def test_later_candidate_is_used_when_the_first_is_missing(tmp_path: Path) -> None:
    got = default_branch(make_repo(tmp_path, [DEFAULT_BRANCH_CANDIDATES[-1], "feature"]))
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == DEFAULT_BRANCH_CANDIDATES[-1]


def test_no_candidate_returns_1(tmp_path: Path) -> None:
    got = default_branch(make_repo(tmp_path, ["trunk"]))
    assert got.returncode == 1
    assert got.stdout == ""


def test_candidates_come_from_branch_flow() -> None:
    got = subprocess.run(
        [sys.executable, "-I", str(LIB / "default_branch_candidates.py")], capture_output=True, text=True, check=True
    )
    assert tuple(got.stdout.split()) == DEFAULT_BRANCH_CANDIDATES
