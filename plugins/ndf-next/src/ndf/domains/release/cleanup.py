"""マージの後の後片付けで、リリースのブランチを取り出した worktree を外す（#3106 の残り）。

worktree を外す関数は呼び手から受け取る。レビューの worktree には NDF の lock が掛かっていることがあり、
外す前に NDF の lock だけを外す関数（`ndf.core.converge` の release）を、それを持つ側（M5 か上位の子）が渡す。
このモジュールは lock の持ち主を import しない（M2 の先に要る子に入っていないため）。既定の関数は
`git worktree remove`（lock の掛かった worktree は git が断り、残ったことを結果に出す）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

from ndf.adapters import git as git_mod


class Swept(NamedTuple):
    """外した worktree と、外せなかった worktree とその理由。"""

    removed: tuple[Path, ...]
    kept: tuple[tuple[Path, str], ...]


def targets(entries: Sequence[git_mod.WorktreeEntry], branches: Sequence[str]) -> list[Path]:
    """`branches` のどれかを取り出している worktree（メインディレクトリの先頭の 1 件を除く）。"""
    wanted = set(branches)
    return [e.path for e in entries[1:] if e.branch in wanted]


def git_remover(repo_git: git_mod.Git) -> Callable[[Path], None]:
    """既定の外し方（`git worktree remove`）。"""

    def remove(path: Path) -> None:
        repo_git.run("worktree", "remove", "--", str(path))

    return remove


def sweep(entries: Sequence[git_mod.WorktreeEntry], branches: Sequence[str], remove: Callable[[Path], None]) -> Swept:
    """マージしたブランチの worktree を `remove` で外す。外せなかったものは理由と一緒に残す（黙って捨てない）。"""
    removed, kept = [], []
    for path in targets(entries, branches):
        try:
            remove(path)
        except (git_mod.GitError, OSError) as exc:
            kept.append((path, str(exc)))
            continue
        removed.append(path)
    return Swept(tuple(removed), tuple(kept))
