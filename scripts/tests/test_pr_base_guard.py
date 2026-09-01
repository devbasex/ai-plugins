"""既定ブランチ宛の Pull Request の分岐元を検査する（issue #202）。

配布のチャネルを分けるリポジトリでは、正式版のブランチへ直に Pull Request を出さない。
判定は宣言に起点が書かれていて、そのブランチが origin にあるときだけ働く。書く前・作る前は
成功で通す。時期を人の手で合わせずに済ませるためである。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "scripts" / "check-pr-base.sh"

pytestmark = pytest.mark.skipif(
    any(shutil.which(name) is None for name in ("bash", "jq", "git")),
    reason="bash / jq / git が要る",
)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """origin を持つリポジトリ。origin に develop は無い。"""
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    git(main, "config", "user.email", "test@example.com")
    git(main, "config", "user.name", "test")
    git(main, "config", "commit.gpgsign", "false")
    (main / "README.md").write_text("# test\n", encoding="utf-8")
    git(main, "add", "README.md")
    git(main, "commit", "-q", "-m", "init")

    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    git(main, "remote", "add", "origin", str(remote))
    git(main, "push", "-q", "origin", "main")
    return main


def declare(repo: Path, body: dict) -> None:
    ndf = repo / ".ndf"
    ndf.mkdir(exist_ok=True)
    (ndf / "worktree.json").write_text(json.dumps(body), encoding="utf-8")


def create_develop(repo: Path) -> None:
    git(repo, "branch", "develop")
    git(repo, "push", "-q", "origin", "develop")
    git(repo, "branch", "-D", "develop")


def guard(repo: Path, head_ref: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(GUARD), head_ref], cwd=str(repo), capture_output=True, text=True
    )


def test_without_declaration_passes(repo: Path) -> None:
    """宣言が無いリポジトリでは検査しない。"""
    assert guard(repo, "feature/x").returncode == 0


def test_without_base_branch_passes(repo: Path) -> None:
    """チャネルを分けていないリポジトリでは検査しない。"""
    declare(repo, {"version": 1})
    assert guard(repo, "feature/x").returncode == 0


def test_unsupported_version_passes(repo: Path) -> None:
    declare(repo, {"version": 99, "base_branch": "develop"})
    create_develop(repo)
    assert guard(repo, "feature/x").returncode == 0


def test_absent_base_branch_passes(repo: Path) -> None:
    """起点ブランチをまだ作っていない間は、すべての Pull Request を通す。"""
    declare(repo, {"version": 1, "base_branch": "develop"})
    assert guard(repo, "feature/x").returncode == 0


def test_from_base_branch_passes(repo: Path) -> None:
    declare(repo, {"version": 1, "base_branch": "develop"})
    create_develop(repo)
    assert guard(repo, "develop").returncode == 0


def test_from_other_branch_fails(repo: Path) -> None:
    declare(repo, {"version": 1, "base_branch": "develop"})
    create_develop(repo)
    got = guard(repo, "feature/x")
    assert got.returncode != 0
    message = got.stdout + got.stderr
    assert "develop" in message
    assert "--base" in message


def test_missing_argument_fails(repo: Path) -> None:
    """分岐元を渡し忘れたときは、通さずに使い方を出す。"""
    got = subprocess.run(
        ["bash", str(GUARD)], cwd=str(repo), capture_output=True, text=True
    )
    assert got.returncode == 2
    assert "使い方" in got.stdout + got.stderr
