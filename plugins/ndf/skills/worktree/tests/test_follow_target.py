"""主ディレクトリのブランチ追従先の判定を検証する（受け入れ条件 11〜15）。

`wt_follow_target` は git を呼ばない。作業ツリーの一覧と未コミット変更の有無を
引数で受け取り、判定だけを行う（詳細設計 05）。そのため、作業ツリーが
0 個 / 1 個 / 複数個 × 変更あり / なしの 6 通りを網羅できる。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from worktree_helpers import git, run_lib

ONE = "/repo/.worktrees/feature/x\tfeature/x"
TWO = "/repo/.worktrees/feature/x\tfeature/x\n/repo/.worktrees/fix/y\tfix/y"


def follow(listing: str, dirty: str) -> str:
    got = run_lib(f'wt_follow_target "{listing}" "{dirty}"')
    return got.stdout.strip()


@pytest.mark.parametrize(
    ("listing", "expected"),
    [
        ("", "default"),
        (ONE, "detach feature/x"),
        (TWO, "default"),
    ],
)
def test_clean_main_dir(listing: str, expected: str) -> None:
    """未コミット変更が無いときは、作業ツリーの数で追従先が決まる。"""
    assert follow(listing, "0") == expected


@pytest.mark.parametrize("listing", ["", ONE, TWO])
def test_dirty_main_dir_never_follows(listing: str) -> None:
    """未コミット変更があるときは、数によらず追従しない（受け入れ条件 14）。"""
    assert follow(listing, "1") == "skip"


def test_detached_worktree_is_not_followed() -> None:
    """ブランチを持たない作業ツリーは追従先にならない。"""
    assert follow("/repo/.worktrees/tmp\t", "0") == "default"


# --- 一覧の取得（受け入れ条件 15） ------------------------------------------


def test_dev_worktrees_lists_only_worktrees_dir(main_repo: Path, worktree: Path) -> None:
    got = run_lib(f'wt_dev_worktrees "{main_repo}"', cwd=main_repo)
    lines = [ln for ln in got.stdout.splitlines() if ln]
    assert len(lines) == 1, got.stdout
    path, branch = lines[0].split("\t")
    assert Path(path).resolve() == worktree.resolve()
    assert branch == "feature/x"


def test_review_worktree_is_excluded(main_repo: Path, worktree: Path, tmp_path: Path) -> None:
    """レビュー用の作業ツリーは `.worktrees/` の外にあり、追従の対象に入らない。"""
    outside = tmp_path / "review-worktree"
    git(main_repo, "worktree", "add", "-q", "--detach", str(outside))
    got = run_lib(f'wt_dev_worktrees "{main_repo}"', cwd=main_repo)
    lines = [ln for ln in got.stdout.splitlines() if ln]
    assert len(lines) == 1, got.stdout
    assert "review-worktree" not in got.stdout


def test_main_dir_itself_is_excluded(main_repo: Path) -> None:
    """作業ツリーが無いとき、主ディレクトリ自身を数に入れない。"""
    got = run_lib(f'wt_dev_worktrees "{main_repo}"', cwd=main_repo)
    assert got.stdout.strip() == "", got.stdout
