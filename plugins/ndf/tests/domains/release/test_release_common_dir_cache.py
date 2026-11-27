"""`release` の CLI が共通の git ディレクトリを `--root` ごとに使い回す振る舞い（`_root_common_dir` と `_hands`）。"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from ndf.adapters import git as git_mod
from ndf.domains.release import cli as release_cli


class _Calls:
    """`Git.common_dir` の差し替え。呼ばれた作業場所を数え、決めた値か GitError を返す。"""

    def __init__(self, answer: Path | None) -> None:
        self.answer = answer
        self.cwds: list[Path] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def common_dir(git: git_mod.Git) -> Path:
            self.cwds.append(git.cwd)
            if self.answer is None:
                raise git_mod.GitError("not a git repository")
            return self.answer

        monkeypatch.setattr(git_mod.Git, "common_dir", common_dir)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> _Calls:
    got = _Calls(Path("/main/.git"))
    got.install(monkeypatch)
    return got


def _args(root: Path, cached: tuple[Path, Path] | None = None) -> argparse.Namespace:
    args = argparse.Namespace(root=str(root))
    if cached is not None:
        args._common_dir = cached
    return args


def test_root_common_dir_resolves_once_and_caches_per_root(calls: _Calls, tmp_path: Path) -> None:
    args = _args(tmp_path)
    assert release_cli._root_common_dir(args) == Path("/main/.git")
    assert release_cli._root_common_dir(args) == Path("/main/.git")
    assert calls.cwds == [tmp_path]
    assert args._common_dir == (tmp_path, Path("/main/.git"))


def test_root_common_dir_ignores_cache_of_another_root(calls: _Calls, tmp_path: Path) -> None:
    args = _args(tmp_path, (tmp_path / "other", Path("/other/.git")))
    assert release_cli._root_common_dir(args) == Path("/main/.git")
    assert calls.cwds == [tmp_path]
    assert args._common_dir == (tmp_path, Path("/main/.git"))


def test_root_common_dir_outside_git_is_none_and_not_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    got = _Calls(None)
    got.install(monkeypatch)
    args = _args(tmp_path)
    assert release_cli._root_common_dir(args) is None
    assert release_cli._root_common_dir(args) is None
    assert got.cwds == [tmp_path, tmp_path]
    assert not hasattr(args, "_common_dir")


def test_main_dir_is_parent_of_common_or_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert release_cli._main_dir(_args(tmp_path, (tmp_path, Path("/main/.git")))) == Path("/main")
    _Calls(None).install(monkeypatch)
    assert release_cli._main_dir(_args(tmp_path)) == tmp_path


def test_hands_reuses_cached_common_dir_without_git(calls: _Calls, tmp_path: Path) -> None:
    hands = release_cli._hands(_args(tmp_path, (tmp_path, Path("/main/.git"))))
    assert hands.common == Path("/main/.git")
    assert hands.repo_git.cwd == tmp_path
    assert calls.cwds == []


def test_hands_without_cache_leaves_common_unresolved(calls: _Calls, tmp_path: Path) -> None:
    args = _args(tmp_path)
    hands = release_cli._hands(args)
    assert hands.common is None
    assert hands.repo_git.cwd == tmp_path
    assert calls.cwds == []
    assert not hasattr(args, "_common_dir")


def test_hands_ignores_cache_of_another_root(calls: _Calls, tmp_path: Path) -> None:
    hands = release_cli._hands(_args(tmp_path, (tmp_path / "other", Path("/other/.git"))))
    assert hands.common is None
    assert calls.cwds == []


def test_hands_after_root_common_dir_shares_the_resolved_value(calls: _Calls, tmp_path: Path) -> None:
    args = _args(tmp_path)
    release_cli._root_common_dir(args)
    assert release_cli._hands(args).common == Path("/main/.git")
    assert calls.cwds == [tmp_path]
