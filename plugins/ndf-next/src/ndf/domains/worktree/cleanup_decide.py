"""作業場所の片付けの判断（純粋な関数）: 開発 worktree を消すか残すかと、その理由。

消すのは、ブランチの PR がマージ済みで、開いた PR が無く、未コミットの変更（追跡していないファイルを含む）が無く、
今のセッションの位置でも鎖されてもいない worktree だけである。それ以外は理由を添えて残す。

**消すのは worktree とマージ済みのブランチだけで、戻せない削除をしない。** ブランチは `git branch -d`
（マージ済みと git が認めたときだけ消える）で消し、squash マージなどで認められなければ残して理由を返す
（`-D` は戻せない操作で、人の承認が要る。C4）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

# 消す
REMOVE = "remove"
# 残す理由
KEEP_DETACHED = "detached"
KEEP_LOCKED = "worktree_locked"
KEEP_CURRENT = "current"
KEEP_DIRTY = "uncommitted"
KEEP_NO_PR = "no_pr"
KEEP_OPEN = "open_pr"
KEEP_UNMERGED = "closed_unmerged"
# 理由の説明（結果の items に載せる）
REASONS = {
    REMOVE: "PR がマージ済みで、変更が残っていない",
    KEEP_DETACHED: "ブランチを checkout していない（detached HEAD）",
    KEEP_LOCKED: "git worktree lock で鎖されている",
    KEEP_CURRENT: "今のセッションがこの worktree の中にいる",
    KEEP_DIRTY: "未コミットの変更か、追跡していないファイルがある",
    KEEP_NO_PR: "ブランチの PR が無い",
    KEEP_OPEN: "開いた PR がある",
    KEEP_UNMERGED: "PR がマージされずに閉じた（消すかは人が決める）",
}


@dataclass(frozen=True)
class PullState:
    """判断に要る PR の状態（`adapters.github.Pull` の `state`・`merged` と同じ語）。"""

    state: str
    merged: bool


@dataclass(frozen=True)
class Facts:
    """1 つの worktree について読んだ事実。"""

    branch: Optional[str]
    locked: bool
    current: bool
    dirty: bool
    pulls: tuple[PullState, ...]


def _pr_verdict(pulls: Iterable[PullState]) -> str:
    states = list(pulls)
    if not states:
        return KEEP_NO_PR
    if any(p.state == "open" for p in states):
        return KEEP_OPEN
    if any(p.merged for p in states):
        return REMOVE
    return KEEP_UNMERGED


def decide(facts: Facts) -> str:
    """`REMOVE` か残す理由の 1 語。安い事実（git だけで分かるもの）から順に見る。"""
    if facts.branch is None:
        return KEEP_DETACHED
    if facts.locked:
        return KEEP_LOCKED
    if facts.current:
        return KEEP_CURRENT
    if facts.dirty:
        return KEEP_DIRTY
    return _pr_verdict(facts.pulls)
