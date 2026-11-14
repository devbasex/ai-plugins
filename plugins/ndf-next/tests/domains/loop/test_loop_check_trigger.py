"""検査のトリガーの行数の判定（#3087）。範囲は本物の git のリポジトリで作る。"""

from __future__ import annotations

from pathlib import Path

import git_sandbox
import pytest
from git_sandbox import sh

from ndf.adapters.git import Git
from ndf.domains.loop import check_trigger as ct


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    root = tmp_path / "repo"
    root.mkdir()
    sh(root, "init", "-q", "-b", "develop")
    (root / "VERSION").write_text("1.0.0\n")
    sh(root, "add", "-A")
    sh(root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base")
    return root


def _rev(repo: Path, ref: str = "HEAD") -> str:
    return sh(repo, "rev-parse", ref, strip=True)


def _merge(repo: Path, branch: str, files: dict[str, str]) -> str:
    """`branch` でファイルを書き、develop へ --no-ff で取り込む。取り込みのコミットの SHA を返す。"""
    sh(repo, "checkout", "-q", "-b", branch, "develop")
    for name, text in files.items():
        (repo / name).write_text(text)
    sh(repo, "add", "-A")
    sh(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", branch)
    sh(repo, "checkout", "-q", "develop")
    sh(repo, "-c", "commit.gpgsign=false", "merge", "-q", "--no-ff", "-m", f"merge {branch}", branch)
    return _rev(repo)


def _changelog(version: str) -> str:
    return "".join(f"{version} の変更 {i}\n" for i in range(30))


def _release_around_feature(repo: Path) -> tuple[str, list[str]]:
    """リリースの PR 2 本（版と CHANGELOG を順に書き換える）の間に、検査されていない 19 行の PR を挟む。"""
    base = _rev(repo)
    first = _merge(repo, "release/v1.0.1", {"VERSION": "1.0.1\n", "CHANGELOG.md": _changelog("1.0.1")})
    _merge(repo, "feat/issue-1", {"feature.txt": "".join(f"{i}\n" for i in range(19))})
    second = _merge(repo, "release/v1.0.2", {"VERSION": "1.0.2\n", "CHANGELOG.md": _changelog("1.0.2")})
    return base, [first, second]


def test_issue_3087_lines_count_only_the_unchecked_pr_between_releases(repo: Path) -> None:
    base, releases = _release_around_feature(repo)
    got = ct.judge_lines(Git(cwd=repo), base, "develop", releases, threshold=18)
    assert got.lines == 19
    assert got.fired == ct.Fired(ct.LINES, 19, 18)


def test_issue_3087_overlapping_excluded_prs_do_not_collapse_lines_to_zero(repo: Path) -> None:
    # 範囲全体の差分（51 行）から除く PR の差分（32 + 62 行）を引く数え方なら 0 へ潰れる
    base, releases = _release_around_feature(repo)
    got = ct.judge_lines(Git(cwd=repo), base, "develop", releases, threshold=3000)
    assert got.lines == 19
    assert got.fired is None


def test_lines_trigger_fires_only_above_the_threshold() -> None:
    assert ct.lines_fired(19, 19) is None
    assert ct.lines_fired(20, 19) == ct.Fired(ct.LINES, 20, 19)
