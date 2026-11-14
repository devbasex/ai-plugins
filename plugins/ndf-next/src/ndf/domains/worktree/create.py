"""作業場所の用意: ブランチの worktree を `<メインディレクトリ>/.worktrees/<ブランチ>` に作る。

起点は `start` を渡せばそれ、無ければスプリントの起点（宣言の `branch_flow.start` が指すチャネル。`base` は
`base_branch`、`production` は `production_branch`。宣言が無い・値が無ければ origin の既定ブランチ）である。
宣言は書き換えない。課題の worktree はスプリントブランチを `start` に渡して切る。

- 取得（`git fetch origin`）は作る前に 1 回だけ行う。失敗しても止めず `warnings` に載せる（origin の無い
  リポジトリでもローカルのブランチから切れる）。hook は取得しない（hook の外で通信する決まり）
- 起点は origin に参照があればそれ、無ければローカルのブランチから切る。どちらにも無ければ作らずに止める
- ブランチが既にあれば、そのブランチを checkout した worktree を作る（新しいブランチを切らない）
- 置き場が既にある・置き場が `.gitignore` に無い・ブランチ名として使えないときは作らずに止める
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ndf.adapters.git import Git
from ndf.domains.worktree import declaration, repo

# 取得するリモート
REMOTE = "origin"


class CreateError(Exception):
    """作れない。`kind` は止まった理由の表の種類（`invalid`・`precondition`・`refused`）。"""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind


@dataclass(frozen=True)
class Created:
    path: Path
    branch: str
    base: str
    start_ref: str
    new_branch: bool
    warnings: list[str] = field(default_factory=list)


def default_branch(git: Git) -> Optional[str]:
    """origin の既定ブランチ（取得済みの `refs/remotes/origin/HEAD`）。無ければ `None`。"""
    done = git.runner(["symbolic-ref", "--quiet", "--short", f"refs/remotes/{REMOTE}/HEAD"], git.cwd)
    name = done.stdout.strip() if done.returncode == 0 else ""
    return name[len(REMOTE) + 1 :] if name.startswith(REMOTE + "/") else None


def start_branch(git: Git, decl: Optional[declaration.Declaration]) -> str:
    """スプリントのベースブランチ。決められなければ `CreateError`（precondition）。"""
    name: Optional[str] = None
    if decl is not None:
        try:
            channel = decl.start_channel
        except declaration.DeclarationError as exc:
            raise CreateError("precondition", str(exc)) from exc
        name = decl.production_branch if channel == "production" else decl.base_branch
    if name is None:
        name = default_branch(git)
    if name is None:
        raise CreateError("precondition", "ベースブランチを決められない（宣言の base_branch も origin の既定ブランチも無い）")
    return name


def _check_name(git: Git, branch: str) -> None:
    done = git.runner(["check-ref-format", "--branch", branch], git.cwd)
    if done.returncode != 0 or branch.startswith("-"):
        raise CreateError("invalid", f"ブランチ名として使えない: {branch}")


def _check_place(git: Git, main_dir: Path, target: Path) -> None:
    rel = repo.worktrees_dir(main_dir).relative_to(main_dir).as_posix()
    ignored = git.runner(["check-ignore", "-q", f"{rel}/"], git.cwd)
    if ignored.returncode != 0:
        raise CreateError("precondition", f"{rel}/ が .gitignore に無い。作る前に登録する")
    if target.exists():
        raise CreateError("refused", f"worktree の置き場が既にある: {target}")


def _fetch(git: Git) -> list[str]:
    done = git.runner(["fetch", "-q", REMOTE], git.cwd)
    return [] if done.returncode == 0 else [f"git fetch {REMOTE} が失敗した（取得済みの参照で続けた）: {done.stderr.strip()}"]


def _start_ref(git: Git, base: str) -> str:
    if repo.has_ref(git, f"refs/remotes/{REMOTE}/{base}"):
        return f"{REMOTE}/{base}"
    if repo.has_ref(git, f"refs/heads/{base}"):
        return base
    raise CreateError("precondition", f"ベースブランチが origin にもローカルにも無い: {base}")


def create(main_dir: Path, branch: str, start: Optional[str] = None, git: Optional[Git] = None) -> Created:
    """worktree を作る。git の失敗は `GitError` のまま返す（呼び手が結果 JSON にする）。"""
    g = git if git is not None else Git(main_dir)
    _check_name(g, branch)
    target = repo.worktree_path(main_dir, branch)
    _check_place(g, main_dir, target)
    warnings = _fetch(g)
    if start is None:
        try:
            decl = declaration.load(main_dir)
        except declaration.DeclarationError as exc:
            raise CreateError("precondition", str(exc)) from exc
        base = start_branch(g, decl)
    else:
        base = start
    ref = _start_ref(g, base)
    if repo.has_ref(g, f"refs/heads/{branch}"):
        g.run("worktree", "add", "-q", str(target), branch)
        return Created(target, branch, base, branch, False, warnings)
    g.run("worktree", "add", "-q", "--no-track", "-b", branch, str(target), ref)
    return Created(target, branch, base, ref, True, warnings)
