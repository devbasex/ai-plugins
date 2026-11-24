"""修正の push（#2440）。

強制の push はしない。拒まれたら（non-fast-forward・fetch first）origin の先頭をマージして自分の未送信のコミットを
載せ直し、1 回だけ送り直す。載せ直しで衝突したらマージを取り消して `diverged` と衝突したファイルを返す。
送り直しも拒まれたら `rejected_twice` を返す（worktree はマージした後のまま。次の流し直しの同期が合わせる）。
pre-push の hook などほかの理由の失敗は `git.GitError` のまま呼び手へ返す（載せ直した後なら `ResendFailed`）。
由来を確かめた範囲（`Verified`）を渡されたら、その先頭のコミットだけを、確かめたときの PR の先頭から進める形でだけ送る。
"""

from __future__ import annotations

import re
from typing import NamedTuple, Optional

from ndf.adapters import git
from ndf.core.converge import worktree

# 先に別の push が入ったときの git の拒否のマーカー
_REJECTED = re.compile(r"\[rejected\]|non-fast-forward|fetch first|\(stale info\)")
# 理由
NOT_ANCESTOR = "not_ancestor"
DIVERGED = "diverged"
REJECTED_TWICE = "rejected_twice"
HEAD_MOVED = "head_moved"
BASE_LOST = "base_lost"


class Verified(NamedTuple):
    """由来を確かめた送る範囲。`head` は送るコミット、`base` は確かめたときの PR のブランチの先頭。"""

    head: str
    base: str


class ResendFailed(git.GitError):
    """載せ直した後の push が、拒否でない理由（認証・通信・hook）で失敗した。`moved` は載せ直した後の範囲。"""

    def __init__(self, message: str, moved: Verified):
        super().__init__(message)
        self.moved = moved


class PushResult(NamedTuple):
    """push の結果。`rebased` はマージしたか、`origin_head` はマージした origin の先頭、`head` は送った（送ろうとした）HEAD。"""

    ok: bool
    rebased: bool
    origin_head: Optional[str]
    head: Optional[str]
    reason: Optional[str]
    conflicts: tuple[str, ...]


def _push(g: git.Git, head_branch: str, remote: str, head: str, base: Optional[str]) -> git.Completed:
    """`head` を送る。`base` があれば、PR のブランチの先頭が `base` のときだけ受け付けさせる。この条件は fast-forward でない push も
    通すので、呼び手は `base` が `head` の祖先と確かめてから渡す。"""
    lease = [] if base is None else [f"--force-with-lease=refs/heads/{head_branch}:{base}"]
    return g.runner(["push", *lease, "--", remote, f"{head}:refs/heads/{head_branch}"], g.cwd)


def _failed(done: git.Completed) -> git.GitError:
    return git.GitError(f"git push が失敗（終了コード {done.returncode}）: {done.stderr.strip()}")


def _rejected_or_raise(done: git.Completed) -> None:
    """失敗した push が先に入った別の push による拒否でなければ（hook などほかの理由）`git.GitError` を投げる。"""
    if not _REJECTED.search(done.stderr):
        raise _failed(done)


def _rebase_onto(g: git.Git, origin_head: str) -> tuple[str, ...]:
    """自分のコミットを `origin_head` へ載せ直す。衝突したら取り消して衝突したファイルを返す。"""
    done = g.runner(["rebase", origin_head], g.cwd)
    if done.returncode == 0:
        return ()
    conflicts = worktree.conflicted_files(g)
    g.run("rebase", "--abort")
    if not conflicts:
        # 載せ直せていないまま送り直さない
        raise git.GitError(f"git rebase が失敗（終了コード {done.returncode}）: {done.stderr.strip()}")
    return conflicts


def _not_sent(reason: str, head: str, origin_head: Optional[str] = None, conflicts: tuple[str, ...] = ()) -> PushResult:
    return PushResult(False, False, origin_head, head, reason, conflicts)


def push_fix(g: git.Git, head_branch: str, fix_commit: Optional[str], verified: Optional[Verified] = None) -> PushResult:
    """修正を PR のブランチへ送る。`fix_commit`（修正担当が報告したコミット）は送るコミットの祖先でなければ送らない。
    `verified` があれば、その `head` だけを送る。PR のブランチから `base` が外れていれば送らず（取り除かれたコミットを戻さない）、
    確かめた後に作業場所の HEAD が動いていれば載せ直さない。"""
    head = verified.head if verified is not None else g.rev_parse("HEAD")
    if fix_commit and not g.is_ancestor(fix_commit, head):
        return PushResult(False, False, None, None, NOT_ANCESTOR, ())
    if verified is not None and not g.is_ancestor(verified.base, head):
        return _not_sent(BASE_LOST, head)
    remote = g.remote()  # エントリポイントで 1 回だけ求める（送り直しの経路で `git worktree list` を打ち直さない）
    first = _push(g, head_branch, remote, head, verified.base if verified is not None else None)
    if first.returncode == 0:
        return PushResult(True, False, None, head, None, ())
    _rejected_or_raise(first)
    if verified is not None and g.rev_parse("HEAD") != head:
        return _not_sent(HEAD_MOVED, head)
    origin_head = worktree.fetch_head(g, head_branch, remote)
    if verified is not None and not g.is_ancestor(verified.base, origin_head):
        return _not_sent(BASE_LOST, head, origin_head)
    conflicts = _rebase_onto(g, origin_head)
    if conflicts:
        return _not_sent(DIVERGED, head, origin_head, conflicts)
    return _push_again(g, head_branch, remote, origin_head, verified is not None)


def _push_again(g: git.Git, head_branch: str, remote: str, origin_head: str, guarded: bool) -> PushResult:
    """載せ直した HEAD を 1 回だけ送り直す。`guarded` なら、PR のブランチの先頭が `origin_head` のときだけ送る。"""
    head = g.rev_parse("HEAD")
    if guarded and not g.is_ancestor(origin_head, head):
        return _not_sent(HEAD_MOVED, head)
    again = _push(g, head_branch, remote, head, origin_head if guarded else None)
    if again.returncode == 0:
        return PushResult(True, True, origin_head, head, None, ())
    if not _REJECTED.search(again.stderr):
        raise ResendFailed(str(_failed(again)), Verified(head, origin_head))
    return PushResult(False, True, origin_head, head, REJECTED_TWICE, ())
