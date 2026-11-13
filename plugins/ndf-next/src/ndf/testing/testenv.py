"""テスト環境の割り当ての回収の判定（#2798・#2964）。

割り当ての行の worktree が消えたとき、その置き場を回収を打った位置から確かめられるか（`location_seen`）と、
環境のコンテナの持ち主（`owner_of`）を決める。どちらもファイルシステムの属性を読むだけで、docker と台帳を呼ばない。
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

NONE = "none"
OWN = "own"
FOREIGN = "foreign"


def within(path: str, base: str) -> bool:
    """path が base そのものか base の下か。"""
    return path == base or path.startswith(base.rstrip("/") + "/")


def location_seen(worktree: Path, main_dir: Path) -> bool:
    """消えた worktree の置き場が、回収を打った位置から確かめられるか。

    メインディレクトリの下なら真。そうでなければ最も近い残っている祖先（親ごと消えていればさらに上）が
    メインディレクトリと同じファイルシステムなら真。残る祖先がファイルシステムの根だけか、別のファイルシステムなら
    偽（別のコンテナの `/tmp` などを、こちらから見えないだけの割り当てと取り違えない）。
    """
    if within(os.path.abspath(worktree), os.path.abspath(main_dir)):
        return True
    ancestor = Path(os.path.abspath(worktree)).parent
    while not ancestor.is_dir():
        ancestor = ancestor.parent
    if ancestor == ancestor.parent:
        return False
    try:
        return ancestor.stat().st_dev == main_dir.stat().st_dev
    except OSError:
        return False


def owner_of(container_workdirs: Iterable[str], row_worktrees: Iterable[str]) -> tuple[str, str]:
    """環境のコンテナの作業ディレクトリと行の worktree の一覧から、持ち主を決める。

    コンテナが無ければ `none`、すべての作業ディレクトリが行の worktree のどれかの下なら `own`、
    1 つでも外にあれば `foreign` と理由を返す。
    """
    workdirs = list(container_workdirs)
    if not workdirs:
        return NONE, ""
    worktrees = [w for w in row_worktrees if w]
    for workdir in workdirs:
        if not any(within(workdir, w) for w in worktrees):
            return FOREIGN, f"別の位置から起動されたコンテナがある: {workdir or '（作業ディレクトリの値が空）'}"
    return OWN, ""
