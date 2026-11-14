"""作業場所の片付け: マージ済みのブランチの worktree を消す（判断は `cleanup_decide`）。

1. メインディレクトリの `git worktree list` から、置き場 `.worktrees/` の下の worktree を集める
2. git だけで分かる事実（detached・鎖・今の位置・未コミットの変更）を読み、残すものを先に決める
3. 残りのブランチの PR を GitHub から 1 回のクエリで読む（`adapters.github.GitHub.pulls_by_head`）
4. 消すと決めた worktree の公開を閉じてテスト環境を破棄し（`testenv_sweep.close_for_removal`。できなければ消さない）、
   `git worktree remove`（`--force` を付けない）で消し、ブランチを `git branch -d` で消す
5. 置き場の消えた worktree の管理情報を `git worktree prune` で片付ける

`dry_run` では 1〜3 だけを行い、何も消さない。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Protocol

from ndf.adapters.git import Git, GitError
from ndf.adapters.github import Pull
from ndf.domains.worktree import cleanup_decide, repo, testenv_sweep
from ndf.domains.worktree.cleanup_decide import REMOVE, Facts, PullState


class PullReader(Protocol):
    """ブランチごとの PR を読むもの（`adapters.github.GitHub` が満たす。テストは差し替える）。"""

    def pulls_by_head(self, branches: Iterable[str]) -> dict[str, list[Pull]]: ...


@dataclass(frozen=True)
class Outcome:
    """1 つの worktree の片付けの結果。`note` は消せなかったときの理由。"""

    path: Path
    branch: Optional[str]
    verdict: str
    removed: bool = False
    branch_deleted: bool = False
    note: str = ""

    def item(self) -> dict[str, object]:
        out: dict[str, object] = {
            "path": str(self.path),
            "branch": self.branch,
            "verdict": self.verdict,
            "reason": cleanup_decide.REASONS[self.verdict],
            "removed": self.removed,
            "branch_deleted": self.branch_deleted,
        }
        if self.note:
            out["note"] = self.note
        return out


def _inside(path: Path, cwd: Path) -> bool:
    here, there = os.path.realpath(cwd), os.path.realpath(path)
    return here == there or here.startswith(there + os.sep)


def _local_facts(item: repo.Worktree, cwd: Path) -> Facts:
    """git だけで分かる事実。置き場が消えた worktree は変更なしとして扱う（prune が片付ける）。"""
    dirty = item.path.is_dir() and bool(repo.dirty_paths(Git(item.path), untracked=True))
    return Facts(item.branch, item.locked, _inside(item.path, cwd), dirty, ())


def _with_pulls(facts: Facts, pulls: Iterable[Pull]) -> Facts:
    states = tuple(PullState(p.state, p.merged) for p in pulls)
    return Facts(facts.branch, facts.locked, facts.current, facts.dirty, states)


def survey(main_dir: Path, cwd: Path, hub: PullReader, git: Optional[Git] = None) -> list[tuple[repo.Worktree, Facts]]:
    """片付けの対象の worktree と、判断に要る事実。PR を読むのは git の事実で残すと決まらないものだけ。"""
    g = git if git is not None else Git(main_dir)
    items = repo.dev_worktrees(main_dir, g.worktrees())
    local = [(item, _local_facts(item, cwd)) for item in items]
    asked = sorted({f.branch for _, f in local if f.branch is not None and cleanup_decide.decide(f) == cleanup_decide.KEEP_NO_PR})
    pulls = hub.pulls_by_head(asked) if asked else {}
    return [(item, _with_pulls(f, pulls[f.branch]) if f.branch in pulls else f) for item, f in local]


def _remove(g: Git, item: repo.Worktree) -> Outcome:
    closed = testenv_sweep.close_for_removal(item.path)
    if closed is not None:
        return Outcome(item.path, item.branch, REMOVE, note=f"worktree を消さなかった: {closed}")
    try:
        g.run("worktree", "remove", str(item.path))
    except GitError as exc:
        return Outcome(item.path, item.branch, REMOVE, note=f"worktree を消せなかった: {exc}")
    refused = repo.delete_merged_branch(g, str(item.branch))
    if refused is not None:
        note = f"ブランチは残した（git がマージ済みと認めない。{repo.MANUAL_FORCE}）: {refused}"
        return Outcome(item.path, item.branch, REMOVE, removed=True, note=note)
    return Outcome(item.path, item.branch, REMOVE, removed=True, branch_deleted=True)


def cleanup(main_dir: Path, cwd: Path, hub: PullReader, dry_run: bool = False, git: Optional[Git] = None) -> list[Outcome]:
    """片付けを行い、worktree ごとの結果を返す。"""
    g = git if git is not None else Git(main_dir)
    out: list[Outcome] = []
    for item, facts in survey(main_dir, cwd, hub, g):
        verdict = cleanup_decide.decide(facts)
        if verdict == REMOVE and not dry_run:
            out.append(_remove(g, item))
        else:
            out.append(Outcome(item.path, item.branch, verdict))
    if not dry_run:
        g.run("worktree", "prune")
    return out
