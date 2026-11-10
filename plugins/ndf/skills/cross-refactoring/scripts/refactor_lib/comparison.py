"""比較の木（#2457 決定 7・I10）。無関係な失敗を、改善項目の変更だけを外した木で見分ける。

比較の木は、今の HEAD から取り消していない改善項目のコミット（テスト・実装・修正）と最終ゲート修正のコミットを
新しい順に `git revert --no-commit` で外した木である。着手の後に取り込んだベースブランチの変更は含む。
その木をどのブランチにも載せないコミット（`git commit-tree`）にして SHA を返し、`test_triage.classify` は
その SHA の一時の detach の worktree で落ちたテストを走らせる。比較の木でも落ちるテストは無関係な失敗である。

外すコミットが衝突して木を作れないときは `None` と理由を返し、呼ぶ側は着手前の HEAD で比べる今の形へ戻る。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile
from typing import Any, Optional

from .items import item_shas, live_items
from .paths import full_commit, git_out, work_dir

_IDENTITY = {
    "GIT_AUTHOR_NAME": "cross-refactoring",
    "GIT_AUTHOR_EMAIL": "cross-refactoring@localhost",
    "GIT_COMMITTER_NAME": "cross-refactoring",
    "GIT_COMMITTER_EMAIL": "cross-refactoring@localhost",
}


def change_commits(state: dict[str, Any], work: str) -> list[str]:
    """外すコミット（完全な SHA）を新しい順に返す。HEAD から辿れないコミットは除く。"""
    wanted: set[str] = set()
    for item in live_items(state):
        wanted.update(full_commit(work, s) for s in item_shas(item))
    for sha in (state.get("final_gate") or {}).get("fix_commits") or []:
        wanted.add(full_commit(work, sha))
    if not wanted:
        return []
    out = git_out(work, ["rev-list", "--topo-order", "HEAD"]) or ""
    return [sha for sha in out.split() if sha in wanted]


def _run_git(cwd: str, args: list[str], env: Optional[dict[str, str]] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)


def build_tree(state: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """比較の木のコミットを作る。`(SHA, 作れなかった理由)` を返す。外すコミットが無ければ HEAD を返す。"""
    work = work_dir(state)
    head = git_out(work, ["rev-parse", "HEAD"])
    if not head:
        return None, "HEAD を読めなかった"
    shas = change_commits(state, work)
    if not shas:
        return head, None
    base = pathlib.Path(state["tmp_dir"])  # 状態の置き場の下に作り、終わったら消す
    base.mkdir(parents=True, exist_ok=True)
    tree = pathlib.Path(tempfile.mkdtemp(prefix="comparison-", dir=base))
    shutil.rmtree(tree)
    if _run_git(work, ["worktree", "add", "--detach", "-q", str(tree), head]).returncode != 0:
        return None, "比較の木の一時の worktree を作れなかった"
    try:
        for sha in shas:
            if _run_git(str(tree), ["revert", "--no-commit", sha]).returncode != 0:
                _run_git(str(tree), ["revert", "--abort"])
                return None, f"改善項目のコミット {sha[:12]} を外せなかった（衝突）"
        written = _run_git(str(tree), ["write-tree"])
        if written.returncode != 0:
            return None, "比較の木を書けなかった"
        made = _run_git(
            str(tree),
            ["commit-tree", written.stdout.strip(), "-p", head, "-m", "cross-refactoring: 比較の木（改善項目の変更を外した木）"],
            env={**os.environ, **_IDENTITY},
        )
        if made.returncode != 0:
            return None, "比較の木のコミットを作れなかった"
        return made.stdout.strip(), None
    finally:
        _run_git(work, ["worktree", "remove", "--force", str(tree)])
        _run_git(work, ["worktree", "prune"])
