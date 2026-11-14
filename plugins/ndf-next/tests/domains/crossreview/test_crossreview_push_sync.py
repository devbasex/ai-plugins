"""修正の push の取り込みと、流し直しの初めの origin への同期（#2440）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.adapters import git
from ndf.core.converge import worktree
from ndf.domains.crossreview import push

from ._repos import BRANCH, Repos, commit, run_cli, sh

SHA = "a" * 40


def _origin_head(repos: Repos) -> str:
    return sh(repos.origin, "rev-parse", f"refs/heads/{BRANCH}").strip()


def _review_worktree(repos: Repos, tmp_path: Path) -> Path:
    return repos.add_worktree(tmp_path / "pr7")


def test_push_without_a_competing_push_sends_as_is(repos: Repos, tmp_path: Path) -> None:
    wt = _review_worktree(repos, tmp_path)
    fix = commit(wt, "fix.txt", "fix\n", "修正")
    got = push.push_fix(git.Git(wt), BRANCH, fix)
    assert got.ok and not got.rebased and got.head == fix
    assert _origin_head(repos) == fix


def test_issue_2440_push_after_another_push_takes_it_in_and_sends_once_more(repos: Repos, tmp_path: Path) -> None:
    """#2440: 修正の間に別の push が入っても止まらず、取り込んで 1 回だけ送り直す（強制の push はしない）。"""
    wt = _review_worktree(repos, tmp_path)
    fix = commit(wt, "fix.txt", "fix\n", "修正")
    theirs = commit(repos.other, "other.txt", "other\n", "別の push")
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    got = push.push_fix(git.Git(wt), BRANCH, fix)
    assert got.ok and got.rebased and got.origin_head == theirs
    head = _origin_head(repos)
    assert head == got.head
    assert sh(wt, "merge-base", "--is-ancestor", theirs, head) == ""
    assert (wt / "fix.txt").read_text(encoding="utf-8") == "fix\n"


def test_issue_2440_conflicting_push_is_undone_and_reported_as_diverged(
    repos: Repos, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#2440: 同じ行を変えた push の後は取り込みを取り消し、diverged と衝突したファイル・同期の 1 行を返す。"""
    wt = _review_worktree(repos, tmp_path)
    fix = commit(wt, "doc.md", "# 設計\n\n## 節 A\n\n直した本文\n", "修正")
    commit(repos.other, "doc.md", "# 設計\n\n## 節 A\n\n別の本文\n", "別の push")
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    code, got = run_cli(capsys, "push-fix", str(wt), "--branch", BRANCH, "--commit", fix)
    assert code != 0
    assert got["items"][0]["reason"] == push.DIVERGED and got["items"][0]["conflicts"] == ["doc.md"]
    assert got["next"]["argv"][1:4] == ["cross-review", "sync", str(wt)]
    assert sh(wt, "rev-parse", "HEAD").strip() == fix
    assert not (Path(sh(wt, "rev-parse", "--absolute-git-dir").strip()) / "rebase-merge").exists()


class _Script:
    """git の起動を順の答えで返す差し替え。"""

    def __init__(self, answers: list[tuple[str, git.Completed]]):
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], cwd):
        self.calls.append(args)
        want, done = self.answers.pop(0)
        assert args[0] == want, (args, want)
        return done


def _ok(out: str = "") -> git.Completed:
    return git.Completed(0, out, "")


def test_issue_2440_push_rejected_again_after_taking_in_stops_with_rejected_twice() -> None:
    """#2440: 取り込みの間にさらに別の push が入れば、再試行は 1 回だけで rejected_twice を返す。"""
    rejected = git.Completed(1, "", " ! [rejected]        HEAD -> feat/x (fetch first)")
    script = _Script(
        [
            ("rev-parse", _ok(SHA)),
            ("push", rejected),
            ("fetch", _ok()),
            ("rev-parse", _ok(SHA)),
            ("rebase", _ok()),
            ("rev-parse", _ok(SHA)),
            ("push", rejected),
        ]
    )
    got = push.push_fix(git.Git(Path("."), script), BRANCH, None)
    assert (got.ok, got.rebased, got.reason) == (False, True, push.REJECTED_TWICE)
    assert script.answers == []
    assert not any("--force" in c or "-f" in c for c in script.calls)


def test_push_refused_by_a_hook_is_not_taken_in() -> None:
    script = _Script([("rev-parse", _ok(SHA)), ("push", git.Completed(1, "", "pre-push hook declined"))])
    with pytest.raises(git.GitError, match="pre-push"):
        push.push_fix(git.Git(Path("."), script), BRANCH, None)


def test_push_refuses_a_reported_commit_that_is_not_in_head(repos: Repos, tmp_path: Path) -> None:
    wt = _review_worktree(repos, tmp_path)
    stray = commit(repos.other, "x.txt", "x\n", "よそ")
    sh(wt, "fetch", "-q", str(repos.other), "HEAD")
    assert push.push_fix(git.Git(wt), BRANCH, stray).reason == push.NOT_ANCESTOR


def test_issue_2440_sync_replays_only_own_unsent_commits_onto_origin(repos: Repos, tmp_path: Path) -> None:
    """#2440: 流し直しの初めに、origin が 5 コミット進んだ worktree を origin の先頭へ合わせ、未送信の自分のコミットだけを載せ直す。"""
    wt = _review_worktree(repos, tmp_path)
    before = commit(wt, "fix.txt", "fix\n", "修正（未送信）")
    for i in range(5):
        commit(repos.other, f"o{i}.txt", f"{i}\n", f"別の push {i}")
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    got = worktree.sync_worktree(git.Git(wt), BRANCH)
    assert (got.before, got.replayed, got.conflicts) == (before, 1, ())
    assert sh(wt, "rev-parse", "HEAD~1").strip() == _origin_head(repos)
    assert push.push_fix(git.Git(wt), BRANCH, None).ok


def test_sync_without_own_commits_only_moves_to_origin(repos: Repos, tmp_path: Path) -> None:
    wt = _review_worktree(repos, tmp_path)
    picked = commit(wt, "fix.txt", "fix\n", "修正")
    sh(repos.other, "fetch", "-q", str(wt), "HEAD")
    sh(repos.other, "cherry-pick", picked)  # 人が修正を手で PR のブランチへ写した
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    got = worktree.sync_worktree(git.Git(wt), BRANCH)
    assert got.replayed == 0 and got.after == _origin_head(repos)


def test_sync_conflict_returns_to_the_previous_head(repos: Repos, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """#2440: 載せ直しで衝突したら前の HEAD に戻し（コミットを失わない）、衝突したファイルを返して止まる。"""
    wt = _review_worktree(repos, tmp_path)
    before = commit(wt, "doc.md", "# 設計\n\n## 節 A\n\n直した\n", "修正")
    commit(repos.other, "doc.md", "# 設計\n\n## 節 A\n\n別\n", "別の push")
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    code, got = run_cli(capsys, "sync", str(wt), "--branch", BRANCH)
    assert code != 0 and got["items"][0]["conflicts"] == ["doc.md"] and before in got["summary"]
    assert sh(wt, "rev-parse", "HEAD").strip() == before
    assert sh(wt, "status", "--porcelain") == ""


def test_sync_refuses_uncommitted_changes(repos: Repos, tmp_path: Path) -> None:
    wt = _review_worktree(repos, tmp_path)
    (wt / "doc.md").write_text("書きかけ\n", encoding="utf-8")
    with pytest.raises(worktree.SyncRefused):
        worktree.sync_worktree(git.Git(wt), BRANCH)
