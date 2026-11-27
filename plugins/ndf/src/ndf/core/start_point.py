"""起点の解決（ベースブランチとリモート名から起点の参照と SHA を決める。#2912 でドキュメント再構成から移した）。

ドキュメント再構成（`domains.docrebuild.drive`）と設計の検査（`domains.records.design_check`）が呼ぶ。`domains` を読まない。
"""

from __future__ import annotations

from typing import Optional

from ndf.adapters import git

WORKTREE_REMOTE = f"{git.WORKTREE_NDF_SETTINGS} の remote"


class StartPointError(Exception):
    """起点を決められない。`detail` は利用者に見せる理由の文。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _commit(g: git.Git, ref: str) -> Optional[str]:
    """参照のコミットの SHA。読めなければ None、git の失敗は `StartPointError`。"""
    try:
        return g.commit_or_none(ref)
    except git.GitError as exc:
        raise StartPointError(f"{ref} を読めない: {exc}") from None


def resolve(g: git.Git, base: str, remote: str) -> tuple[str, str]:
    """起点の参照の名前と SHA。

    リモート追跡ブランチが読めればそれを使う。読めないときは、求めた名前のリモートが登録済みか、リモートが 1 つも
    無いときだけ手元の参照を使う。ほかの名前のリモートだけがあるとき・どちらも読めないときは `StartPointError`。
    """
    tracking = f"refs/remotes/{remote}/{base}"
    sha = _commit(g, tracking)
    if sha is not None:
        return tracking, sha
    try:
        names = g.run("remote").split()
    except git.GitError as exc:
        raise StartPointError(f"登録済みのリモートを読めない: {exc}") from None
    if names and remote not in names:
        raise StartPointError(f"リモート {remote} が登録されていない（登録済み: {', '.join(names)}）。{WORKTREE_REMOTE} を直す")
    local = f"refs/heads/{base}"
    sha = _commit(g, local)
    if sha is None:
        raise StartPointError(f"起点の参照 {tracking} と {local} をどちらも読めない")
    return local, sha
