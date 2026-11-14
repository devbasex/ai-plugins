"""セッションを始めたときに、メインディレクトリの逸脱（追跡しているファイルの未コミットの変更）を伝える判断（純粋な関数）。

worktree の中で始めたセッションと、宣言の無いリポジトリでは何も伝えない。逸脱はメインディレクトリの話である。
変更が多いときは先頭の `MAX_LISTED` 件だけを並べ、残りは件数へ丸める（文脈を埋めない）。
"""

from __future__ import annotations

from typing import Optional

# 並べる変更の上限
MAX_LISTED = 20
DIRTY = """メインディレクトリに追跡しているファイルの未コミットの変更が {count} 件あります。

{paths}

開発の変更であれば、worktree へ移してください（`ndf worktree create <ブランチ名>` で作った worktree へ移す）。"""
FOLLOW_NOTE = "メインディレクトリのブランチは、変更がある間は稼働中の worktree へ追従しません。"


def deviation(dirty: list[str], in_worktree: bool, declared: bool, follow_branch: bool = False) -> Optional[str]:
    """伝える文。伝えることが無ければ `None`。"""
    if in_worktree or not declared or not dirty:
        return None
    listed = [f"  {line}" for line in dirty[:MAX_LISTED]]
    if len(dirty) > MAX_LISTED:
        listed.append(f"  ... ほか {len(dirty) - MAX_LISTED} 件")
    text = DIRTY.format(count=len(dirty), paths="\n".join(listed))
    return f"{text}\n{FOLLOW_NOTE}" if follow_branch else text


# ブランチの追従の判断の結果
FOLLOW_SKIP = "skip"
FOLLOW_DETACH = "detach"
FOLLOW_BASE = "to_base"


def follow_target(dirty: bool, branches: list[Optional[str]]) -> tuple[str, Optional[str]]:
    """メインディレクトリをどこへ合わせるか（宣言の `follow_branch: true` のときだけ判断する）。

    未コミットの変更があれば動かさない（`skip`）。ブランチを持つ開発 worktree がちょうど 1 つならそのブランチの
    コミットへ detached HEAD で合わせ（`detach`）、0 か 2 つ以上ならベースブランチへ合わせる（`base`）。
    """
    if dirty:
        return FOLLOW_SKIP, None
    named = [b for b in branches if b]
    if len(named) == 1:
        return FOLLOW_DETACH, named[0]
    return FOLLOW_BASE, None
