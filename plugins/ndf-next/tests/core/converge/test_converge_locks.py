"""cross 系の作業場所の lock の持ち主・持ち主のいない lock の片付け・再開の前の確かめ・既定の置き場（#3106 の残り）。

#3106 の観測: `/tmp` を共有しない別のコンテナの `git worktree prune` で、作業中の登録が消えた。lock は prune から守るが、
lock を掛けた作業場所が消えると lock が残り続け、誰が掛けたかも読めなかった。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import git_sandbox
import pytest
from git_sandbox import sh as _sh

from ndf import identity
from ndf.adapters import git
from ndf.core.converge import place, worktree


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    main = tmp_path / "main"
    main.mkdir()
    _sh(main, "init", "-q", "-b", "main")
    _sh(main, "commit", "-q", "--allow-empty", "-m", "初め")
    return main


def _locked(repo: Path, path: Path, reason: str) -> Path:
    _sh(repo, "worktree", "add", "-q", "--detach", str(path))
    _sh(repo, "worktree", "lock", "--reason", reason, str(path))
    return path


def test_lock_carries_the_host_and_time_and_the_key_stays_readable(repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "rf1"
    _sh(repo, "worktree", "add", "-q", "--detach", str(wt))
    key = worktree.lock_review_worktree(git.Git(repo), wt, "cross-refactoring", 1)
    assert key == worktree.lock_reason("cross-refactoring", 1)
    assert worktree.lock_of(git.Git(repo), wt) == key
    held = worktree.holder_of(git.Git(repo), wt)
    assert held is not None and held.key == key and held.host and held.at


def test_old_lock_without_a_holder_is_read_as_the_whole_key() -> None:
    assert worktree.parse_holder("ndf cross-review pr3") == worktree.Holder("ndf cross-review pr3", None, None)
    text = worktree.held_reason("cross-review", 3, host="h1", at="2026-11-14T00:00:00+00:00")
    assert worktree.parse_holder(text) == worktree.Holder("ndf cross-review pr3", "h1", "2026-11-14T00:00:00+00:00")


def test_sweep_removes_only_this_hosts_ndf_locks_whose_directory_is_gone(repo: Path, tmp_path: Path) -> None:
    mine = _locked(repo, tmp_path / "mine", worktree.held_reason("cross-refactoring", 1, host="here"))
    other_host = _locked(repo, tmp_path / "other", worktree.held_reason("cross-refactoring", 2, host="there"))
    user = _locked(repo, tmp_path / "user", "利用者の lock")
    alive = _locked(repo, tmp_path / "alive", worktree.held_reason("cross-refactoring", 4, host="here"))
    for gone in (mine, other_host, user):
        shutil.rmtree(gone)
    swept = worktree.sweep_orphans(git.Git(repo), host="here")
    assert [p.resolve() for p in swept] == [mine.resolve()]
    listed = _sh(repo, "worktree", "list", "--porcelain")
    assert str(mine) not in listed
    assert str(other_host) in listed and str(user) in listed and str(alive) in listed


def test_resume_recreates_a_missing_directory_and_stops_on_a_lost_registration(repo: Path, tmp_path: Path) -> None:
    assert worktree.resumable(tmp_path / "never") is False
    wt = tmp_path / "rf9"
    _sh(repo, "worktree", "add", "-q", "--detach", str(wt))
    assert worktree.resumable(wt) is True
    # 別の環境の prune で登録だけが消えた形（作業場所のディレクトリと .git は残る）
    admin = Path(_sh(wt, "rev-parse", "--absolute-git-dir").strip())
    (admin / "HEAD").unlink()
    with pytest.raises(worktree.WorktreeUnregistered):
        worktree.resumable(wt)


def test_default_place_is_inside_the_repository_not_tmp(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(place.BASE_ENV, raising=False)
    got = place.work_root(git.Git(repo), "rf5")
    assert got == repo.resolve() / ".worktrees" / identity.FIXED_WORDS["declaration_dir"] / "rf5"
    monkeypatch.setenv(place.BASE_ENV, str(tmp_path / "shared"))
    assert place.work_root(git.Git(repo), "rf5") == tmp_path / "shared" / "rf5"
