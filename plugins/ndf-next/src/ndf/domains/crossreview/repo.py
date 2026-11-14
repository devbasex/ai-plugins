"""レビューの対象のリポジトリを決める（#1707）。

順は `--repo` → 状態ファイルの `repo` → cwd の origin の 1 つだけである。どれでも決まらなければ `RepoUnresolved` を
投げ、cwd が git リポジトリでないか・origin が GitHub でないかを理由に書く。`gh repo view` のような、cwd の外の
既定へ落ちる道は持たない（Skill のディレクトリで打つと別のリポジトリを指しうる）。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple, Optional

from ndf.adapters import git

# 決めた出所
ARG = "arg"
STATE = "state_file"
ORIGIN = "origin"


class RepoUnresolved(Exception):
    """対象のリポジトリを決められない。"""


class RepoTarget(NamedTuple):
    """対象のリポジトリ（`<所有者>/<リポジトリ>`）と、決めた出所（`ARG`・`STATE`・`ORIGIN`）。"""

    slug: str
    source: str


def _valid(slug: str) -> bool:
    owner, _, name = slug.partition("/")
    return bool(owner) and bool(name) and "/" not in name


def _from_origin(cwd: Path, make: Callable[[Path], git.Git]) -> RepoTarget:
    g = make(cwd)
    try:
        inside = g.run("rev-parse", "--is-inside-work-tree").strip() == "true"
    except git.GitError as exc:
        raise RepoUnresolved(f"cwd（{cwd}）が git リポジトリでない。対象のリポジトリで打つか --repo を渡す: {exc}") from None
    if not inside:
        raise RepoUnresolved(f"cwd（{cwd}）が worktree の中でない。対象のリポジトリで打つか --repo を渡す")
    try:
        return RepoTarget(g.remote_repo(), ORIGIN)
    except git.GitError as exc:
        raise RepoUnresolved(f"cwd（{cwd}）の origin から GitHub のリポジトリを決められない。--repo を渡す: {exc}") from None


def resolve_target_repo(
    repo_arg: Optional[str], state_repo: Optional[str], cwd: Path, make: Callable[[Path], git.Git] = git.Git
) -> RepoTarget:
    """対象のリポジトリを `--repo`・状態ファイル・cwd の origin の順で決める。形の誤った値は `RepoUnresolved`。"""
    for value, source in ((repo_arg, ARG), (state_repo, STATE)):
        if not value:
            continue
        if not _valid(value):
            raise RepoUnresolved(f"{source} のリポジトリの形が <所有者>/<リポジトリ> でない: {value!r}")
        return RepoTarget(value, source)
    return _from_origin(cwd, make)
