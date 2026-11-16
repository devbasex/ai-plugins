"""セッションを始めたときのブランチの追従（宣言の `follow_branch: true` のときだけ。判断は `session_decide.follow_target`）。

メインディレクトリの HEAD を、稼働中の開発 worktree のブランチ（1 つだけのとき）か、ベースブランチの指すコミットへ
detached HEAD で合わせる。HEAD を動かす git の操作はメインディレクトリの排他（`mainlock`）の中で行う。合わせる先を
解決できない・合わせられないときは止めず、伝える文に載せる（作業は続けられる）。

ベースブランチが取得済みの参照に無いときだけリモート名のリモートから取得し、`refs/remotes/<リモート名>/<ベースブランチ>` を読む
（`FETCH_HEAD` を読まない。#3317）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from filelock import Timeout

from ndf.adapters.git import Git
from ndf.domains.worktree import declaration, mainlock, repo, session_decide

# セッションの開始で排他を待つ秒（開始を長く止めない）
LOCK_WAIT = 60


def resolve(main: Git, name: str, remote: str) -> Optional[str]:
    """ブランチ名が指すコミット（ローカル → 取得済みの `remote` の参照の順）。無ければ `None`。

    名前をそのまま `git rev-parse` へ渡さない（`refs/remotes/<リモート名>/<名前>` は名前の解決の順に含まれない）。
    リモート名は呼び手が 1 回求めて渡す（`Git.remote()` は呼ぶたびに `git worktree list` を打つ）。
    """
    for ref in (f"refs/heads/{name}", f"refs/remotes/{remote}/{name}"):
        if main.has_ref(ref):
            return main.rev_parse(ref)
    return None


def _resolve_base(main: Git, name: str) -> Optional[str]:
    remote = main.remote()
    found = resolve(main, name, remote)
    if found is not None:
        return found
    if main.fetch_branch(remote, name).returncode != 0:
        return None
    return resolve(main, name, remote)


def _wanted(main: Git, main_dir: Path, decl: declaration.Declaration) -> tuple[Optional[str], str]:
    """合わせる先のコミットと、その説明。合わせなければ `(None, "")`。"""
    dirty = bool(repo.dirty_paths(main))
    branches = [w.branch for w in repo.dev_worktrees(main_dir, main.worktrees())]
    verdict, branch = session_decide.follow_target(dirty, branches)
    if verdict == session_decide.FOLLOW_DETACH and branch is not None:
        return resolve(main, branch, main.remote()), f"worktree のブランチ {branch}"
    if verdict != session_decide.FOLLOW_BASE:
        return None, ""
    base = repo.base_branch(main, decl)
    if base is None or repo.current_branch(main) == base:
        return None, ""
    return _resolve_base(main, base), f"ベースブランチ {base}"


def follow(main_dir: Path, decl: Optional[declaration.Declaration]) -> Optional[str]:
    """追従し、伝える文を返す（動かさなければ `None`）。"""
    if decl is None or not decl.follow_branch:
        return None
    main = Git(main_dir)
    wanted, label = _wanted(main, main_dir, decl)
    if wanted is None or wanted == main.rev_parse("HEAD"):
        return None
    try:
        with mainlock.held(main, timeout=LOCK_WAIT):
            done = main.checkout_detached(wanted)
    except Timeout:
        return f"メインディレクトリを {label} へ合わせられなかった（排他を取れない）。作業は続けられる。"
    if done.returncode != 0:
        return f"メインディレクトリを {label} へ合わせられなかった。作業は続けられる。"
    return f"メインディレクトリを {label} が指すコミットへ detached HEAD で合わせた。"
