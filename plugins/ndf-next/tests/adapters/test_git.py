"""git のアダプター（`ndf.adapters.git`）。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ndf.adapters import git

FULL = "2bb66a6c14a618cbac108b61737ad49cfb1ac48b"


def _fake(returncode: int = 0, stdout: str = "", stderr: str = ""):
    calls: list[list[str]] = []

    def runner(args: list[str], cwd):
        calls.append(args)
        return git.Completed(returncode, stdout, stderr)

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


@pytest.mark.parametrize(
    ("expected", "matches"),
    [(FULL, True), ("2bb66a6c1", True), ("2BB66A6C1", True), ("2bb66a6c2", False), ("deadbeef", False)],
)
def test_sha_matches_accepts_short_and_full(expected: str, matches: bool) -> None:
    # #2041: 同じ先端を指す短い SHA は一致として読む
    assert git.sha_matches(expected, FULL) is matches


@pytest.mark.parametrize(("expected", "actual"), [("2bb66", FULL), ("main", FULL), ("2bb66a6c1", "2bb66a6c1")])
def test_sha_matches_refuses_values_that_are_not_shas(expected: str, actual: str) -> None:
    with pytest.raises(git.GitError):
        git.sha_matches(expected, actual)


def test_rev_parse_returns_full_sha() -> None:
    runner = _fake(stdout=FULL + "\n")
    assert git.Git(runner=runner).rev_parse("2bb66a6c1") == FULL
    assert runner.calls == [["rev-parse", "--verify", "--quiet", "2bb66a6c1^{commit}"]]


def test_run_raises_with_exit_code_and_stderr() -> None:
    with pytest.raises(git.GitError, match="終了コード 128.*bad ref"):
        git.Git(runner=_fake(128, stderr="bad ref")).run("rev-parse", "x")


@pytest.mark.parametrize(("code", "expected"), [(0, True), (1, False)])
def test_is_ancestor_reads_exit_code(code: int, expected: bool) -> None:
    assert git.Git(runner=_fake(code)).is_ancestor("a", "b") is expected


def test_is_ancestor_raises_on_git_failure() -> None:
    with pytest.raises(git.GitError):
        git.Git(runner=_fake(128, stderr="not a commit")).is_ancestor("a", "b")


@pytest.mark.parametrize(
    ("url", "repo"),
    [
        ("https://github.com/devbasex/ai-plugins.git", "devbasex/ai-plugins"),
        ("git@github.com:devbasex/ai-plugins.git", "devbasex/ai-plugins"),
        ("https://github.com/devbasex/ai-plugins", "devbasex/ai-plugins"),
        ("https://gitlab.com/x/y.git", None),
    ],
)
def test_repo_of_url(url: str, repo: str | None) -> None:
    assert git.repo_of_url(url) == repo


def test_remote_repo_refuses_non_github_remote() -> None:
    with pytest.raises(git.GitError, match="GitHub"):
        git.Git(runner=_fake(stdout="https://gitlab.com/x/y.git\n")).remote_repo()


def test_parse_shortstat_sums_insertions_and_deletions() -> None:
    assert git.parse_shortstat(" 7 files changed, 671 insertions(+), 10 deletions(-)") == 681
    assert git.parse_shortstat(" 1 file changed, 3 deletions(-)") == 3
    assert git.parse_shortstat("") == 0


def _git(repo: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    cmd = ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args]
    done = subprocess.run(cmd, capture_output=True, text=True, check=True, env={**os.environ, **env})
    return done.stdout.strip()


def _init_repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    """main に `files` を書いた base のコミットを 1 つ持つリポジトリを作る。リポジトリのパスと base の SHA を返す。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for name, text in files.items():
        (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _merge_branch(repo: Path, branch: str, files: dict[str, str]) -> str:
    """`branch` を作ってファイルを書き、main へ --no-ff で取り込む。取り込みのコミットの SHA を返す。"""
    _git(repo, "checkout", "-q", "-b", branch, "main")
    for name, text in files.items():
        (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", branch)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", f"merge {branch}", branch)
    return _git(repo, "rev-parse", "HEAD")


def test_changed_lines_does_not_subtract_overlapping_release_changes_twice(tmp_path: Path) -> None:
    # #3087: リリースの PR 2 本が同じ版のファイルを順に書き換え、範囲の間に機能の PR（19 行）が入る
    repo, base = _init_repo(tmp_path, {"VERSION": "1.0.0\n"})
    first = _merge_branch(repo, "release-1", {"VERSION": "1.0.1\n"})
    _merge_branch(repo, "feature", {"feature.txt": "".join(f"{i}\n" for i in range(19))})
    second = _merge_branch(repo, "release-2", {"VERSION": "1.0.2\n"})

    g = git.Git(cwd=repo)
    assert g.changed_lines(base, "main", exclude=[first, second[:9]]) == 19
    assert g.changed_lines(base, "main") == 19 + 2 + 2


def test_changed_files_returns_real_names_of_quoted_paths(tmp_path: Path) -> None:
    # 既定の core.quotePath では日本語・タブ・引用符を含むパスが引用されるが、実際のファイル名を返す
    repo, base = _init_repo(tmp_path, {"base.txt": "base\n"})
    names = ["設計.md", "a\tb.txt", 'q"uote.txt']
    _merge_branch(repo, "feature", {name: "x\n" for name in names})

    changed = git.Git(cwd=repo).changed_files(base, "main")
    assert sorted(changed) == sorted(names)
    assert all((repo / name).exists() for name in changed)
