"""レビュー worktree の lock・登録の確かめ・片付け（#3106・#3109）。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ndf.adapters import git
from ndf.core.converge import worktree
from ndf.domains.crossreview import cli

from ._repos import BRANCH, Repos, last_result, sh


def _listed(repos: Repos, path: Path) -> bool:
    return f"worktree {path}" in sh(repos.main, "worktree", "list", "--porcelain")


def test_issue_3106_locked_registration_survives_prune_from_an_environment_without_the_directory(repos: Repos, tmp_path: Path) -> None:
    """#3106: /tmp を共有しない別の環境の prune（worktree のディレクトリが見えない）でも、lock した登録は消えない。"""
    locked = repos.add_worktree(tmp_path / "pr1")
    plain = repos.add_worktree(tmp_path / "pr2")
    reason = worktree.lock_review_worktree(git.Git(locked), locked, "cross-review", 1)
    assert reason == worktree.lock_reason("cross-review", 1)
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    for wt in (locked, plain):
        shutil.move(str(wt), str(hidden / wt.name))
    sh(repos.main, "worktree", "prune")
    for wt in (locked, plain):
        shutil.move(str(hidden / wt.name), str(wt))
    assert _listed(repos, locked)
    assert worktree.registration(locked).worktree == locked
    assert not _listed(repos, plain)
    with pytest.raises(worktree.WorktreeUnregistered):
        worktree.registration(plain)


def _runner(codes: dict[str, int]):
    calls: list[list[str]] = []

    def run(args: list[str], cwd):
        calls.append(args)
        return git.Completed(codes.get(args[1], 0), "", "refused")

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_lock_failure_removes_the_new_worktree(tmp_path: Path) -> None:
    """#3106: lock を掛けられなければ、lock の無いまま作業を始めないよう worktree を外して止める。"""
    run = _runner({"lock": 128})
    with pytest.raises(worktree.LockFailed, match="外した"):
        worktree.lock_review_worktree(git.Git(tmp_path, run), tmp_path / "pr3", "cross-review", 3)
    assert [c[:2] for c in run.calls] == [["worktree", "lock"], ["worktree", "remove"]]


def test_lock_failure_reports_when_the_worktree_cannot_be_removed_either(repos: Repos, tmp_path: Path) -> None:
    wt = repos.add_worktree(tmp_path / "pr3")
    sh(repos.main, "worktree", "lock", "--reason", "x", str(wt))  # lock 済みは lock も remove --force も断られる
    with pytest.raises(worktree.LockFailed, match="外せない"):
        worktree.lock_review_worktree(git.Git(repos.main), wt, "cross-review", 3)
    assert wt.exists()


def test_issue_3109_lost_admin_files_are_reported_as_unregistered_not_as_an_unknown_repo(
    repos: Repos, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#3109: 管理情報（HEAD・gitdir）が消えて ndf/state だけが残った worktree は、「リポジトリを決められない」でなく
    管理情報が無いことと .git が指す先を理由に止まる。"""
    wt = repos.add_worktree(tmp_path / "pr4")
    gitdir = Path(sh(wt, "rev-parse", "--absolute-git-dir").strip())
    for name in ("HEAD", "commondir", "gitdir"):
        (gitdir / name).unlink()
    (gitdir / "ndf" / "state" / "dbos").mkdir(parents=True)
    for argv in (["push-fix", str(wt), "--branch", BRANCH], ["sync", str(wt), "--branch", BRANCH], ["check-worktree", str(wt)]):
        code = cli.main(argv)
        got = last_result(capsys.readouterr().out)
        assert code != 0
        assert got["cause"]["kind"] == "precondition"
        assert "管理情報が無い" in got["summary"]
        assert str(gitdir) in got["summary"]
        assert "リポジトリを決められない" not in got["summary"]


def test_registration_accepts_the_main_checkout_and_rejects_a_missing_dotgit(repos: Repos, tmp_path: Path) -> None:
    assert worktree.registration(repos.main).gitdir == repos.main / ".git"
    bare_dir = tmp_path / "plain"
    bare_dir.mkdir()
    with pytest.raises(worktree.WorktreeUnregistered, match="読めない"):
        worktree.registration(bare_dir)


def test_release_unlocks_only_ndf_locks(repos: Repos, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """#3106: 片付けは NDF の理由の lock だけを外してから worktree を外す。利用者の lock は外さず理由を返す。"""
    ours = repos.add_worktree(tmp_path / "pr5")
    worktree.lock_review_worktree(git.Git(ours), ours, "cross-review", 5)
    assert cli.main(["release", str(ours), "--repo-dir", str(repos.main)]) == 0
    assert not ours.exists()
    sh(repos.main, "worktree", "add", "-q", "-b", "user", str(tmp_path / "user"), "HEAD")
    theirs = tmp_path / "user"
    sh(repos.main, "worktree", "lock", "--reason", "keep me", str(theirs))
    code = cli.main(["release", str(theirs), "--repo-dir", str(repos.main)])
    got = last_result(capsys.readouterr().out)
    assert code != 0 and got["cause"]["kind"] == "refused" and "keep me" in got["summary"]
    assert theirs.exists()
    plain = tmp_path / "plain"
    sh(repos.main, "worktree", "add", "-q", "-b", "plain", str(plain), "HEAD")
    assert worktree.release(git.Git(repos.main), plain) == worktree.Release(True, None)


def test_cli_lock_reports_the_reason(repos: Repos, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wt = repos.add_worktree(tmp_path / "pr6")
    assert cli.main(["lock", str(wt), "--pr", "6"]) == 0
    assert last_result(capsys.readouterr().out)["items"][0]["locked"] == worktree.lock_reason(cli.CROSS_REVIEW, 6)
    assert worktree.lock_of(git.Git(repos.main), wt) == worktree.lock_reason(cli.CROSS_REVIEW, 6)
