"""修正の push（#2440）。

強制の push はしない。拒まれたら（non-fast-forward・fetch first）origin の先頭を取り込んで自分の未送信のコミットを
載せ直し、1 回だけ送り直す。載せ直しで衝突したら取り込みを取り消して `diverged` と衝突したファイルを返す。
送り直しも拒まれたら `rejected_twice` を返す（worktree は取り込んだ後のまま。次の流し直しの同期が合わせる）。
pre-push の hook などほかの理由の失敗は `git.GitError` のまま呼び手へ返す。
"""

from __future__ import annotations

import re
from typing import NamedTuple, Optional

from ndf.adapters import git
from ndf.domains.crossreview import worktree

# 先に別の push が入ったときの git の拒否の印
_REJECTED = re.compile(r"\[rejected\]|non-fast-forward|fetch first|\(stale info\)")
# 理由
NOT_ANCESTOR = "not_ancestor"
DIVERGED = "diverged"
REJECTED_TWICE = "rejected_twice"


class PushResult(NamedTuple):
    """push の結果。`rebased` は取り込んだか、`origin_head` は取り込んだ origin の先頭、`head` は送った（送ろうとした）HEAD。"""

    ok: bool
    rebased: bool
    origin_head: Optional[str]
    head: Optional[str]
    reason: Optional[str]
    conflicts: tuple[str, ...]


def _push(g: git.Git, head_branch: str) -> git.Completed:
    return g.runner(["push", "origin", f"HEAD:refs/heads/{head_branch}"], g.cwd)


def _failed(done: git.Completed) -> git.GitError:
    return git.GitError(f"git push が失敗（終了コード {done.returncode}）: {done.stderr.strip()}")


def _rebase_onto_origin(g: git.Git, head_branch: str) -> tuple[str, tuple[str, ...]]:
    """origin の先頭を取り込み、自分のコミットを載せ直す。衝突したら取り消して衝突したファイルを返す。"""
    origin_head = worktree.fetch_head(g, head_branch)
    done = g.runner(["rebase", origin_head], g.cwd)
    if done.returncode == 0:
        return origin_head, ()
    conflicts = tuple(p for p in g.run("diff", "--name-only", "-z", "--diff-filter=U").split("\0") if p)
    g.run("rebase", "--abort")
    return origin_head, conflicts


def push_fix(g: git.Git, head_branch: str, fix_commit: Optional[str]) -> PushResult:
    """修正を PR のブランチへ送る。`fix_commit`（修正担当が報告したコミット）は HEAD の祖先でなければ送らない。"""
    if fix_commit and not g.is_ancestor(fix_commit, "HEAD"):
        return PushResult(False, False, None, None, NOT_ANCESTOR, ())
    head = g.rev_parse("HEAD")
    first = _push(g, head_branch)
    if first.returncode == 0:
        return PushResult(True, False, None, head, None, ())
    if not _REJECTED.search(first.stderr):
        raise _failed(first)
    origin_head, conflicts = _rebase_onto_origin(g, head_branch)
    if conflicts:
        return PushResult(False, False, origin_head, head, DIVERGED, conflicts)
    head = g.rev_parse("HEAD")
    again = _push(g, head_branch)
    if again.returncode == 0:
        return PushResult(True, True, origin_head, head, None, ())
    if not _REJECTED.search(again.stderr):
        raise _failed(again)
    return PushResult(False, True, origin_head, head, REJECTED_TWICE, ())
