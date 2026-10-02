"""作業用 worktree の親ディレクトリの解決（cross-review と cross-refactoring の共通層）。"""

from __future__ import annotations

import os
import pathlib
import tempfile

ENV = "NDF_WORKTREE_BASE"


def worktree_parent() -> pathlib.Path:
    """worktree の親ディレクトリ。

    1. 環境変数 `NDF_WORKTREE_BASE`（明示指定）。相対パスのまま状態ファイルへ残すと後続の
       パスの比較が壊れるため、絶対パスへ解決して返す
    2. `<システム tmpdir>/ndf-worktrees`（非永続領域。コンテナ再作成で自動消滅）

    かつての /work/worktrees ($HOME/work/worktrees) は共有の永続 volume 上にあり、
    別リポジトリの pr<N> と衝突する・明示削除が必要・volume を消費する問題が
    あったため廃止した。
    """
    env = os.environ.get(ENV)
    if env:
        return pathlib.Path(env).resolve()
    return pathlib.Path(tempfile.gettempdir()) / "ndf-worktrees"
