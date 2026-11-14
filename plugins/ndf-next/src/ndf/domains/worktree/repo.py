"""リポジトリの中の位置と worktree の一覧（git は `adapters.git.Git` を通して読む）。

- `locate`: メインディレクトリと、今の位置が worktree の中か。サブモジュールの中は通常のリポジトリとして扱う
- `worktrees`: `git worktree list --porcelain -z` の一覧。`dev_worktrees` は置き場 `.worktrees/` の下だけに絞る
  （置き場の名前は事実の表 `places` の `worktrees` から引く）
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from ndf.adapters.git import Git, GitError
from ndf.contracts import facts

# ブランチの参照の接頭辞（`git worktree list --porcelain` の branch の行）
HEADS = "refs/heads/"


def worktrees_dir(main_dir: Path) -> Path:
    """開発 worktree を置くディレクトリ（メインディレクトリの下）。"""
    return main_dir / facts.value("places", "worktrees", "path")


def worktree_path(main_dir: Path, branch: str) -> Path:
    """ブランチの worktree の置き場 `<メインディレクトリ>/.worktrees/<ブランチ>`。"""
    return worktrees_dir(main_dir) / branch


@dataclass(frozen=True)
class Location:
    """今の位置。`top` は今の worktree（かメインディレクトリ）の根。"""

    main_dir: Path
    top: Path

    @property
    def in_worktree(self) -> bool:
        return self.top != self.main_dir


def locate(git: Git) -> Location:
    """`git.cwd` の位置。リポジトリの外なら `GitError`。"""
    common, top = git.run("rev-parse", "--path-format=absolute", "--git-common-dir", "--show-toplevel").splitlines()[:2]
    common_dir, top_dir = Path(os.path.realpath(common)), Path(os.path.realpath(top))
    # 共通ディレクトリがメインディレクトリの直下の .git でなければ（サブモジュールなど）、今の根をメインとみなす
    main = common_dir.parent if common_dir.name == ".git" else top_dir
    return Location(main, top_dir)


@dataclass(frozen=True)
class Worktree:
    """`git worktree list` の 1 件。`branch` は detached HEAD なら `None`。"""

    path: Path
    head: str
    branch: Optional[str]
    prunable: bool = False
    locked: bool = False


def parse_porcelain(text: str) -> list[Worktree]:
    """`git worktree list --porcelain -z` の出力を読む（項目は NUL、worktree は空の項目で区切られる）。"""
    out: list[Worktree] = []
    for record in text.split("\0\0"):
        fields = dict(_field(f) for f in record.split("\0") if f)
        if "worktree" not in fields:
            continue
        branch = fields.get("branch")
        out.append(
            Worktree(
                Path(fields["worktree"]),
                fields["HEAD"] if "HEAD" in fields else "",
                branch[len(HEADS) :] if branch is not None and branch.startswith(HEADS) else None,
                "prunable" in fields,
                "locked" in fields,
            )
        )
    return out


def _field(item: str) -> tuple[str, str]:
    name, _, value = item.partition(" ")
    return name, value


def worktrees(git: Git) -> list[Worktree]:
    return parse_porcelain(git.run("worktree", "list", "--porcelain", "-z"))


def dev_worktrees(main_dir: Path, items: Iterable[Worktree]) -> list[Worktree]:
    """置き場 `.worktrees/` の下（その下の `.ndf/` の作業用を除く）の worktree。"""
    root = os.path.realpath(worktrees_dir(main_dir))
    scratch = os.path.join(root, facts.value("places", "ndf", "path"))
    picked = []
    for item in items:
        path = os.path.realpath(item.path)
        if path.startswith(root + os.sep) and not (path == scratch or path.startswith(scratch + os.sep)):
            picked.append(item)
    return picked


def current_branch(git: Git) -> Optional[str]:
    """今チェックアウトしているブランチ。detached HEAD なら `None`。"""
    name = git.run("rev-parse", "--abbrev-ref", "HEAD").strip()
    return None if name == "HEAD" else name


def dirty_paths(git: Git, untracked: bool = False) -> list[str]:
    """未コミットの変更の `<状態> <パス>`（既定は追跡しているファイルだけ）。"""
    mode = "--untracked-files=normal" if untracked else "--untracked-files=no"
    return [line for line in git.run("status", "--porcelain", mode).splitlines() if line.strip()]


def has_ref(git: Git, ref: str) -> bool:
    """完全な参照の名前（`refs/heads/<名前>` など）があるか。git の失敗（終了コード 1 以外）は `GitError`。"""
    done = git.runner(["show-ref", "--verify", "--quiet", ref], git.cwd)
    if done.returncode not in (0, 1):
        raise GitError(f"参照を確かめられない（終了コード {done.returncode}）: {done.stderr.strip()}")
    return done.returncode == 0
