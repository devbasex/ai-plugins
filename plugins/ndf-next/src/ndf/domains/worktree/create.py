"""作業場所の用意: ブランチの worktree を `<メインディレクトリ>/.worktrees/<ブランチ>` に作る。

起点は `start` を渡せばそれ、無ければスプリントの起点（ブランチ戦略の宣言 `.ndf/project.json` の `branch_strategy` の
`Strategy.sprint_start_ref`。`sprint.start` が `base` なら `branches.base`、`production` なら `branches.production`）である。
宣言が無い・旧いキーが残る・スキーマに落ちるときは既定ブランチへ倒さずに止める（旧いキーは位置と移行のコマンドを出す）。
宣言は書き換えない。課題の worktree はスプリントブランチを `start` に渡して切る。

`state_dir` を渡すと（スプリントの開始）、セクションの全体をその下へ控える（`declaration.write_start_snapshot`）。

- 取得（`git fetch origin`）は作る前に 1 回だけ行う。失敗しても止めず `warnings` に載せる（origin の無い
  リポジトリでもローカルのブランチから切れる）。hook は取得しない（hook の外で通信する決まり）
- 起点は origin に参照があればそれ、無ければローカルのブランチから切る。どちらにも無ければ作らずに止める
- ブランチが既にあれば、そのブランチを checkout した worktree を作る（新しいブランチを切らない）
- 置き場が既にある・置き場が `.gitignore` に無い・ブランチ名として使えないときは作らずに止める
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ndf.adapters.git import Git
from ndf.domains.worktree import declaration, repo


class CreateError(Exception):
    """作れない。`kind` は止まった理由の表の種類（`invalid`・`precondition`・`refused`・`unknown`）。"""

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
    snapshot: Optional[dict[str, Any]] = None


def start_branch(main_dir: Path) -> str:
    """スプリントの起点（ブランチの名前）。決められなければ `CreateError`（precondition）。"""
    try:
        return declaration.sprint_start(main_dir)
    except declaration.DeclarationError as exc:
        raise CreateError("precondition", str(exc)) from exc


def _check_name(git: Git, branch: str) -> None:
    done = git.attempt("check-ref-format", "--branch", branch)
    if done.returncode != 0 or branch.startswith("-"):
        raise CreateError("invalid", f"ブランチ名として使えない: {branch}")


def _check_place(git: Git, main_dir: Path, target: Path) -> None:
    rel = repo.worktrees_dir(main_dir).relative_to(main_dir).as_posix()
    ignored = git.attempt("check-ignore", "-q", f"{rel}/")
    if ignored.returncode != 0:
        raise CreateError("precondition", f"{rel}/ が .gitignore に無い。作る前に登録する")
    if target.exists():
        raise CreateError("refused", f"worktree の置き場が既にある: {target}")


def _fetch(git: Git, remote: str) -> list[str]:
    done = git.attempt("fetch", "-q", "--", remote)
    return [] if done.returncode == 0 else [f"git fetch {remote} が失敗した（取得済みの参照で続けた）: {done.stderr.strip()}"]


def _start_ref(git: Git, base: str, remote: str) -> str:
    if git.has_ref(f"refs/remotes/{remote}/{base}"):
        return f"{remote}/{base}"
    if git.has_ref(f"refs/heads/{base}"):
        return base
    raise CreateError("precondition", f"ベースブランチがリモート {remote} にもローカルにも無い: {base}")


def _snapshot(main_dir: Path, state_dir: Optional[Path]) -> Optional[dict[str, Any]]:
    if state_dir is None:
        return None
    try:
        return declaration.write_start_snapshot(main_dir, state_dir)
    except declaration.DeclarationError as exc:
        raise CreateError("precondition", str(exc)) from exc
    except OSError as exc:
        raise CreateError("unknown", f"スプリントの開始時のスナップショットを書けない: {exc}") from exc


def create(
    main_dir: Path, branch: str, start: Optional[str] = None, git: Optional[Git] = None, state_dir: Optional[Path] = None
) -> Created:
    """worktree を作る。git の失敗は `GitError` のまま返す（呼び手が結果 JSON にする）。"""
    g = git if git is not None else Git(main_dir)
    _check_name(g, branch)
    target = repo.worktree_path(main_dir, branch)
    _check_place(g, main_dir, target)
    try:
        declaration.load(main_dir)  # 旧いキーの残る worktree の宣言では作らない
    except declaration.DeclarationError as exc:
        raise CreateError("precondition", str(exc)) from exc
    base = start_branch(main_dir) if start is None else start
    snap = _snapshot(main_dir, state_dir)
    remote = g.remote()
    warnings = _fetch(g, remote)
    ref = _start_ref(g, base, remote)
    if g.has_ref(f"refs/heads/{branch}"):
        g.run("worktree", "add", "-q", str(target), branch)
        return Created(target, branch, base, branch, False, warnings, snap)
    g.run("worktree", "add", "-q", "--no-track", "-b", branch, str(target), ref)
    return Created(target, branch, base, ref, True, warnings, snap)
