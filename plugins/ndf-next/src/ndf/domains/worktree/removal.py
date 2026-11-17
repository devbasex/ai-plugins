"""worktree を外す共通の手順（`merged` と `cleanup` の両方が使う）。

テスト環境の公開を閉じる → 無視したファイルを退避する → `git worktree remove` → 外せなければ退避したファイルを戻す。
利用者へ出す文は呼び手が組む（ここは何が起きたかだけを返す）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ndf.adapters.git import Git
from ndf.domains.worktree import repo, testenv_sweep, trash


@dataclass(frozen=True)
class Removal:
    """外した結果。`removed` が偽なら、止まった段階の理由が `closed`・`trash_error`・`refused` のどれか 1 つに入る。

    `got` は退避の結果（退避しなければ None）、`back` は外せなかったときに退避したファイルを戻した結果の説明。
    """

    removed: bool
    got: Optional[trash.Evacuated] = None
    closed: Optional[str] = None
    trash_error: Optional[str] = None
    refused: Optional[str] = None
    back: str = ""


def restore_note(worktree: Path, got: Optional[trash.Evacuated]) -> str:
    """外せなかった worktree へ退避したファイルを戻し、その結果の説明（退避していなければ空）。"""
    if got is None or got.dest is None:
        return ""
    failed = trash.restore(worktree, got)
    return f"退避したファイルを戻せなかった: {failed}" if failed is not None else "退避したファイルを worktree へ戻した"


def remove_evacuating(g: Git, item: repo.Worktree, merge: str, remove: Callable[[], Optional[str]]) -> Removal:
    """worktree を外す。`remove` は `git worktree remove` を打ち、断られた理由（外せたら None）を返す。

    `merge` は PR のマージコミット（退避のジャーナルへ書く）。
    """
    closed = testenv_sweep.close_for_removal(item.path)
    if closed is not None:
        return Removal(False, closed=closed)
    got: Optional[trash.Evacuated] = None
    if item.path.is_dir() and trash.leftovers(item.path):  # 無視したファイルは git worktree remove が黙って消す
        try:
            got = trash.evacuate(g, item.path, str(item.branch), item.head, merge)
        except trash.TrashError as exc:
            return Removal(False, trash_error=str(exc))
    refused = remove()
    if refused is not None:
        return Removal(False, got, refused=refused, back=restore_note(item.path, got))
    return Removal(True, got)
