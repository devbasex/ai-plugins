"""対象のリポジトリの決め方（#1707）と、依存の欠けの理由（#2447）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ndf.domains.crossreview import cli, repo, store

from ._repos import Repos, run_cli, sh


def test_issue_1707_outside_a_git_repository_the_reason_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """#1707: Skill のディレクトリ（git リポジトリでない）で打つと、cwd が git リポジトリでないことを理由に止まる。"""
    skill_dir = tmp_path / "skills" / "cross-review"
    skill_dir.mkdir(parents=True)
    monkeypatch.chdir(skill_dir)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    code, got = run_cli(capsys, "target-repo")
    assert code != 0
    assert got["cause"]["kind"] == "precondition"
    assert "git リポジトリでない" in got["summary"] and "--repo" in got["summary"]


def test_issue_1707_origin_of_the_target_repository_is_used(repos: Repos, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """#1707: 対象のリポジトリの中で打てば、cwd の origin から決まる。"""
    sh(repos.main, "remote", "set-url", "origin", "git@github.com:acme/app.git")
    monkeypatch.chdir(repos.main)
    code, got = run_cli(capsys, "target-repo")
    assert code == 0
    assert got["items"][0] == {"kind": "repo", "slug": "acme/app", "source": repo.ORIGIN}


def test_arg_and_state_come_before_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    store.write_object(tmp_path / store.STATE_NAME.format(pr=5), {store.ORIGIN_KEY: 5, store.REPO_KEY: "acme/state"})
    code, got = run_cli(capsys, "target-repo", "--state-dir", str(tmp_path), "--pr", "5")
    assert code == 0
    assert got["items"][0]["source"] == repo.STATE
    assert repo.resolve_target_repo("acme/arg", "acme/state", tmp_path) == repo.RepoTarget("acme/arg", repo.ARG)
    with pytest.raises(repo.RepoUnresolved, match="形"):
        repo.resolve_target_repo("acme", None, tmp_path)


def test_origin_that_is_not_github_is_unresolved(repos: Repos) -> None:
    with pytest.raises(repo.RepoUnresolved, match="origin"):
        repo.resolve_target_repo(None, None, repos.main)


def test_issue_2447_broken_dependency_environment_is_reported_as_deps_missing(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """#2447: 依存の仮想環境が欠けていれば、「PR の head を取得できない」でなく依存の欠け（deps_missing）で止まる。"""
    monkeypatch.delitem(sys.modules, cli.GITHUB_MODULE, raising=False)
    monkeypatch.setitem(sys.modules, "githubkit", None)
    code, got = run_cli(capsys, "head", "--pr", "364", "--repo", "acme/app")
    assert code != 0
    assert got["cause"]["kind"] == "deps_missing"
    assert "githubkit" in got["summary"] and "head は読んでいない" in got["summary"]


class _Pull:
    head_sha = "b" * 40
    head_ref = "feat/x"


def test_head_reads_the_pull_request_through_the_adapter(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    class FakeGitHubError(Exception):
        unreached = True

    class FakeHub:
        def __init__(self, slug, api):
            self.slug = slug

        def pull(self, number):
            if number == 2:
                raise FakeGitHubError("届かない")
            return _Pull()

    fake = type(sys)("fake_github")
    fake.GitHub, fake.GitHubError, fake.client = FakeHub, FakeGitHubError, lambda: None
    monkeypatch.setattr(cli, "load_github", lambda: fake)
    code, got = run_cli(capsys, "head", "--pr", "1", "--repo", "acme/app")
    assert code == 0
    assert got["items"][0]["head"] == _Pull.head_sha
    code, got = run_cli(capsys, "head", "--pr", "2", "--repo", "acme/app")
    assert code != 0
    assert got["cause"]["kind"] == "transient"
