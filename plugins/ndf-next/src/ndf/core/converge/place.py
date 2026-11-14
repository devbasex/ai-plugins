"""cross 系の作業場所の置き場（#3106）。

既定の置き場はリポジトリのメインディレクトリの `.worktrees/.ndf`。`/tmp` は別のコンテナ（マウントの名前空間）と共有されず、
そちらで打った `git worktree prune` に作業場所が無いと見なされて登録が消えるため、リポジトリと同じ所に置く。
`.` で始まる名前はブランチ名に使えず、開発の worktree（`.worktrees/<ブランチ>`）と重ならない。
環境変数 `NDF_WORKTREE_BASE` があればその置き場を使う。
"""

from __future__ import annotations

import os
from pathlib import Path

from ndf import identity
from ndf.adapters import git
from ndf.core.route import WORKTREES

BASE_ENV = "NDF_WORKTREE_BASE"


def default_base(main: Path) -> Path:
    """作業場所の親ディレクトリ。`main` はリポジトリのメインディレクトリ。"""
    declared = os.environ.get(BASE_ENV, "")
    if declared:
        return Path(declared)
    return main / WORKTREES / identity.FIXED_WORDS["declaration_dir"]


def main_dir(g: git.Git) -> Path:
    """`g` の作業場所（linked worktree でもよい）が属するリポジトリのメインディレクトリ。"""
    return g.common_dir().parent


def work_root(g: git.Git, tool_dir: str) -> Path:
    """1 回の作業の作業場所の根（`<置き場>/<tool_dir>`。例: `rf3101`）。"""
    return default_base(main_dir(g)) / tool_dir
