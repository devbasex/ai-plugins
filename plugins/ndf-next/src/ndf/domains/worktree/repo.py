"""リポジトリの中の位置と worktree の一覧（git は `adapters.git.Git` を通して読む）。

- `locate`: メインディレクトリと、今の位置が worktree の中か。サブモジュールの中は通常のリポジトリとして扱う
- `worktrees`: `git worktree list` の一覧（読み方は `adapters.git.Git.worktrees`）。`dev_worktrees` は置き場 `.worktrees/` の下だけに絞る
  （置き場の名前は事実の表 `places` の `worktrees` から引く）
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from ndf.adapters.git import Git, GitError, WorktreeEntry, parse_worktree_list
from ndf.contracts import facts
from ndf.domains.worktree import declaration

# 取得するリモート
REMOTE = "origin"
# マージ済みと認められなかったブランチを残すときに添える文（`-D` は NDF が打たず、人が確かめて打つ。C4）
MANUAL_FORCE = "消すなら人が確かめて -D で消す"


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


# `git worktree list` の 1 件と、その読み手（読み方は git のアダプターが持つ）
Worktree = WorktreeEntry
parse_porcelain = parse_worktree_list


def worktrees(git: Git) -> list[Worktree]:
    return git.worktrees()


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


def default_branch(git: Git) -> Optional[str]:
    """origin の既定ブランチ（取得済みの `refs/remotes/origin/HEAD`）。無ければ `None`。"""
    done = git.attempt("symbolic-ref", "--quiet", "--short", f"refs/remotes/{REMOTE}/HEAD")
    name = done.stdout.strip() if done.returncode == 0 else ""
    return name[len(REMOTE) + 1 :] if name.startswith(REMOTE + "/") else None


def base_branch(git: Git, decl: Optional[declaration.Declaration]) -> Optional[str]:
    """ベースブランチ: 宣言の `base_branch`、無ければ origin の既定ブランチ。"""
    return decl.base_branch if decl is not None and decl.base_branch else default_branch(git)


def current_branch(git: Git) -> Optional[str]:
    """今チェックアウトしているブランチ。detached HEAD なら `None`。"""
    name = git.run("rev-parse", "--abbrev-ref", "HEAD").strip()
    return None if name == "HEAD" else name


def dirty_paths(git: Git, untracked: bool = False) -> list[str]:
    """未コミットの変更の `<状態> <パス>`（既定は追跡しているファイルだけ）。"""
    mode = "--untracked-files=normal" if untracked else "--untracked-files=no"
    return [line for line in git.run("status", "--porcelain", mode).splitlines() if line.strip()]


def delete_merged_branch(git: Git, branch: str) -> Optional[str]:
    """`git branch -d` でブランチを消す（`-D` は使わない）。消せたら `None`、git が断ったらその標準エラー。"""
    done = git.attempt("branch", "-d", branch)
    return None if done.returncode == 0 else done.stderr.strip()


def has_ref(git: Git, ref: str) -> bool:
    """完全な参照の名前（`refs/heads/<名前>` など）があるか。git の失敗（終了コード 1 以外）は `GitError`。"""
    done = git.attempt("show-ref", "--verify", "--quiet", ref)
    if done.returncode not in (0, 1):
        raise GitError(f"参照を確かめられない（終了コード {done.returncode}）: {done.stderr.strip()}")
    return done.returncode == 0
