"""現在地の判定を検証する（受け入れ条件 1〜3）。

作業ツリーの中では `git rev-parse --show-toplevel` が作業ツリー自身を返すため、
主ディレクトリを指すには共通の git ディレクトリの親を使う。サブモジュールの中でも
作業ディレクトリ固有の git ディレクトリと共通の git ディレクトリは異なるため、
サブモジュールを作業ツリーと取り違えないことを併せて確かめる。
"""
from __future__ import annotations

from pathlib import Path

from conftest import git, init_repo, run_lib


def test_main_dir_is_not_worktree(main_repo: Path) -> None:
    got = run_lib("wt_in_worktree; echo $?", cwd=main_repo)
    assert got.stdout.strip() == "1", got.stderr


def test_main_dir_resolves_to_itself(main_repo: Path) -> None:
    got = run_lib("wt_main_dir", cwd=main_repo)
    assert Path(got.stdout.strip()).resolve() == main_repo.resolve(), got.stderr


def test_worktree_is_detected(worktree: Path) -> None:
    got = run_lib("wt_in_worktree; echo $?", cwd=worktree)
    assert got.stdout.strip() == "0", got.stderr


def test_worktree_resolves_to_main_dir(worktree: Path, main_repo: Path) -> None:
    got = run_lib("wt_main_dir", cwd=worktree)
    assert Path(got.stdout.strip()).resolve() == main_repo.resolve(), got.stderr


def test_nested_directory_inside_worktree(worktree: Path, main_repo: Path) -> None:
    """作業ツリー直下でなく、その内側の階層から呼んでも判定は変わらない。"""
    nested = worktree / "a" / "b"
    nested.mkdir(parents=True)
    got = run_lib("wt_in_worktree; echo $?", cwd=nested)
    assert got.stdout.strip() == "0", got.stderr
    got = run_lib("wt_main_dir", cwd=nested)
    assert Path(got.stdout.strip()).resolve() == main_repo.resolve()


def test_submodule_is_not_worktree(tmp_path: Path) -> None:
    """サブモジュールの中は作業ツリーとして扱わない。"""
    inner = init_repo(tmp_path / "inner")
    outer = init_repo(tmp_path / "outer")
    git(outer, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(inner), "sub")
    git(outer, "commit", "-q", "-m", "add submodule")
    sub = outer / "sub"

    got = run_lib("wt_in_worktree; echo $?", cwd=sub)
    assert got.stdout.strip() == "1", got.stderr

    got = run_lib("wt_main_dir", cwd=sub)
    assert Path(got.stdout.strip()).resolve() == sub.resolve(), got.stderr


def test_outside_repository_fails(tmp_path: Path) -> None:
    """リポジトリの外では解決に失敗し、何も出力しない。"""
    outside = tmp_path / "plain"
    outside.mkdir()
    got = run_lib("wt_main_dir; echo rc=$?", cwd=outside)
    assert got.stdout.strip() == "rc=1", got.stdout
