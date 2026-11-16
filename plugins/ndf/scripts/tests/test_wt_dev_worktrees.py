"""`lib/worktree-branch.sh` の `wt_dev_worktrees` の現状固定テスト（I-019）。

`worktree-common.sh` を source した状態（`WT_WORKTREE_DIR=.worktrees`）で、主ディレクトリの
`git worktree list --porcelain` から `.worktrees/` の下（`.worktrees/.ndf/` の下を除く）の作業ツリーを
`<パス><タブ><ブランチ名>` で 1 行 1 件出すことを固定する。
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

COMMON = pathlib.Path(__file__).resolve().parents[1] / "lib" / "worktree-common.sh"


def _git(cwd: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True, text=True)


def _call(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f'. "{COMMON}" || exit 9\n{script}'], capture_output=True, text=True, timeout=60)


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """`.worktrees/` の下・`.worktrees/.ndf/` の下・外・似た名前の置き場に作業ツリーを持つ主ディレクトリ。"""
    main = (tmp_path / "main").resolve()
    subprocess.run(["git", "init", "-q", "-b", "main", str(main)], check=True)
    _git(main, "commit", "-q", "--allow-empty", "-m", "init")
    _git(main, "worktree", "add", "-q", "-b", "feat/a", str(main / ".worktrees" / "feat-a"))
    _git(main, "worktree", "add", "-q", "-b", "feat/b", str(main / ".worktrees" / "sub" / "feat-b"))
    _git(main, "worktree", "add", "-q", "-b", "rev", str(main / ".worktrees" / ".ndf" / "rev"))
    _git(main, "worktree", "add", "-q", "--detach", str(main / ".worktrees" / "det"))
    _git(main, "worktree", "add", "-q", "-b", "out", str(tmp_path / "outside"))
    _git(main, "worktree", "add", "-q", "-b", "near", str(main / ".worktreesX"))
    return main


def test_characterize_lists_dev_worktrees_with_branch(repo: pathlib.Path) -> None:
    got = _call(f'wt_dev_worktrees "{repo}"')
    assert got.returncode == 0, got.stderr
    assert sorted(got.stdout.splitlines()) == [
        f"{repo}/.worktrees/det\t",  # detached HEAD はブランチ名が空
        f"{repo}/.worktrees/feat-a\tfeat/a",
        f"{repo}/.worktrees/sub/feat-b\tfeat/b",
    ]
    assert got.stdout.endswith("\n")


def test_characterize_does_not_leave_the_helper_defined(repo: pathlib.Path) -> None:
    got = _call(f'wt_dev_worktrees "{repo}" >/dev/null; declare -F _wt_emit_worktree; echo "rc=$?"')
    assert got.stdout.strip() == "rc=1", got.stdout


def test_characterize_without_argument_returns_1() -> None:
    got = _call('wt_dev_worktrees; echo "rc=$?"')
    assert got.stdout == "rc=1\n", got.stdout


def test_characterize_outside_git_prints_nothing(tmp_path: pathlib.Path) -> None:
    got = _call(f'wt_dev_worktrees "{tmp_path / "nope"}"; echo "rc=$?"')
    assert got.stdout == "rc=0\n", got.stdout


def test_characterize_from_a_linked_worktree_prints_nothing(repo: pathlib.Path) -> None:
    """接頭辞は渡したディレクトリから作るため、作業ツリーのパスを渡すと 1 件も出ない。"""
    got = _call(f'wt_dev_worktrees "{repo}/.worktrees/feat-a"; echo "rc=$?"')
    assert got.stdout == "rc=0\n", got.stdout


def test_characterize_without_dev_worktrees_prints_nothing(tmp_path: pathlib.Path) -> None:
    main = tmp_path / "solo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(main)], check=True)
    _git(main, "commit", "-q", "--allow-empty", "-m", "init")
    got = _call(f'wt_dev_worktrees "{main}"; echo "rc=$?"')
    assert got.stdout == "rc=0\n", got.stdout
