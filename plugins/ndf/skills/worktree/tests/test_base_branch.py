"""開発の起点ブランチの解決を検証する（issue #202）。

既定ブランチと開発の起点は別物である。宣言に起点が書かれていればそれを使い、
無ければ既定ブランチへ落とす。書かれた名前が実在しないときは既定へ落とさない。
落とすと、開発版のつもりの変更が正式版から分岐したまま進む。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from worktree_helpers import git, run_lib, write_declaration


def add_origin(main_repo: Path) -> Path:
    """主ディレクトリへ origin を足し、既定ブランチを送る。"""
    remote = main_repo.parent / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    git(main_repo, "remote", "add", "origin", str(remote))
    git(main_repo, "push", "-q", "origin", "main")
    git(main_repo, "remote", "set-head", "origin", "main")
    return remote


def push_branch(main_repo: Path, name: str, *, keep_local: bool = False) -> None:
    """origin にだけ存在するブランチを作る。"""
    git(main_repo, "branch", name)
    git(main_repo, "push", "-q", "origin", name)
    if not keep_local:
        git(main_repo, "branch", "-D", name)


def resolve(main_repo: Path) -> tuple[str, str, int]:
    got = run_lib(f'wt_base_branch "{main_repo}"; echo rc=$?', cwd=main_repo)
    lines = got.stdout.splitlines()
    rc = int(lines.pop().removeprefix("rc="))
    return "\n".join(lines), got.stderr, rc


def test_without_declaration_uses_default_branch(main_repo: Path) -> None:
    add_origin(main_repo)
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "main"


def test_without_base_branch_uses_default_branch(main_repo: Path) -> None:
    add_origin(main_repo)
    write_declaration(main_repo, json.dumps({"version": 1}))
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "main"


def test_remote_branch_is_used(main_repo: Path) -> None:
    add_origin(main_repo)
    push_branch(main_repo, "develop")
    write_declaration(main_repo, json.dumps({"version": 1, "base_branch": "develop"}))
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "develop"


def test_local_only_branch_is_used(main_repo: Path) -> None:
    """origin へ送る前でも、ローカルに同名のブランチがあれば起点として使える。"""
    add_origin(main_repo)
    git(main_repo, "branch", "develop")
    write_declaration(main_repo, json.dumps({"version": 1, "base_branch": "develop"}))
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "develop"


def test_missing_branch_does_not_fall_back(main_repo: Path) -> None:
    add_origin(main_repo)
    write_declaration(main_repo, json.dumps({"version": 1, "base_branch": "develop"}))
    out, err, rc = resolve(main_repo)
    assert rc == 1
    assert out == ""
    assert "develop" in err
    assert "base_branch" in err


@pytest.mark.parametrize("value", [1, ["develop"], None, "", {"name": "develop"}])
def test_non_string_value_falls_back(main_repo: Path, value: object) -> None:
    add_origin(main_repo)
    push_branch(main_repo, "develop")
    write_declaration(main_repo, json.dumps({"version": 1, "base_branch": value}))
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "main"


def test_unsupported_version_falls_back(main_repo: Path) -> None:
    add_origin(main_repo)
    push_branch(main_repo, "develop")
    write_declaration(main_repo, json.dumps({"version": 99, "base_branch": "develop"}))
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "main"


def test_without_origin_head_falls_back_to_local_default(main_repo: Path) -> None:
    """origin を持たないリポジトリでも、既定ブランチの解決はこれまでどおり働く。"""
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "main"
