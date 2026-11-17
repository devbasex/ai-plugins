"""`release_branch` のリリースの単位を組む: 本番ブランチの HEAD から、選んだスプリントブランチを順に `--no-ff` でマージする
（10 系の `strategy_route._build_release_branch` を移したもの。#3615 の決定 8）。

- 組むのは detached HEAD で、手元のブランチを作らず動かさない。既に本番ブランチに入っているスプリントは飛ばす
- マージの件名は `included_decide.UNIT_MERGE_SUBJECT`（`Merge <スプリント> into <リリースブランチ>`）。含む PR を数える側が
  同じ形で見分ける
- 衝突すれば取り消して `UnitsError`。同じ衝突の解き方は rerere で再利用し、rerere が衝突をすべて解いたマージはそのままコミットする
- 承認資料（`materials`）はブランチを残さないよう、一時の作業場所（`scratch`）で組んで外す
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ndf.adapters import git as git_mod
from ndf.domains.release import included_decide

# 承認資料のために組む一時の作業場所の名前の頭
SCRATCH_PREFIX = "ndf-release-units-"
# 衝突の出力から結果へ載せる末尾の文字数
TAIL = 400


class UnitsError(Exception):
    """リリースの単位を組めない（選んだスプリントが無い・衝突した・本番ブランチから切れない）。"""


def merge_subject(unit: str, branch: str) -> str:
    """リリースの単位のマージの件名。"""
    return included_decide.UNIT_MERGE_SUBJECT.format(unit=unit, branch=branch)


def _resolved_by_rerere(repo_git: git_mod.Git, subject: str) -> bool:
    """rerere が衝突をすべて解いたマージなら、そのままコミットして真。"""
    if repo_git.run("diff", "--name-only", "--diff-filter=U").strip():
        return False
    return repo_git.attempt("commit", "--quiet", "--no-edit", "-m", subject).returncode == 0


def _merge_unit(repo_git: git_mod.Git, remote: str, unit: str, branch: str) -> None:
    """HEAD へ単位 `unit` を `--no-ff` でマージする。既に入っていれば飛ばし、衝突が残れば取り消して `UnitsError`。"""
    ref = f"{remote}/{unit}"
    if repo_git.is_ancestor(ref, "HEAD"):
        return
    subject = merge_subject(unit, branch)
    merged = repo_git.attempt(
        "-c", "rerere.enabled=true", "-c", "rerere.autoupdate=true", "merge", "--no-ff", "--no-edit", "-m", subject,
        git_mod.END_OF_OPTIONS, ref,
    )  # fmt: skip
    if merged.returncode != 0 and not _resolved_by_rerere(repo_git, subject):
        repo_git.attempt("merge", "--abort")
        raise UnitsError(f"{unit} を {branch} へマージできない（衝突は人が解く）: {(merged.stdout + merged.stderr).strip()[-TAIL:]}")


def build(repo_git: git_mod.Git, remote: str, production: str, units: Sequence[str], branch: str) -> str:
    """`<remote>/<production>` の HEAD から detached HEAD で `units` を順にマージし、先頭の SHA を返す。"""
    if not units:
        raise UnitsError("リリースの単位（--units）が無い")
    for name in (production, *units):
        done = repo_git.fetch_branch(remote, name)
        if done.returncode != 0:
            raise UnitsError(f"{remote} の {name} を取得できない: {done.stderr.strip()}")
    done = repo_git.checkout_detached(f"{remote}/{production}")
    if done.returncode != 0:
        raise UnitsError(f"{remote}/{production} から {branch} を切れない: {done.stderr.strip()}")
    for unit in units:
        _merge_unit(repo_git, remote, unit, branch)
    return repo_git.rev_parse("HEAD")


@dataclass
class Scratch:
    """一時の作業場所で組んだ単位。`leftover` は外せなかった作業場所のパス（`git worktree prune` で外れる）。"""

    path: Path
    head: str
    leftover: list[str] = field(default_factory=list)


@contextmanager
def scratch(repo_git: git_mod.Git, remote: str, production: str, units: Sequence[str], branch: str) -> Iterator[Scratch]:
    """一時の作業場所で `build` を流し、抜けるときに作業場所を外す（例外でも外す）。"""
    parent = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX))
    path = parent / "work"
    repo_git.fetch_branch_or_raise(remote, production)
    repo_git.run("worktree", "add", "--quiet", "--detach", "--", str(path), f"{remote}/{production}")
    made = Scratch(path, "")
    try:
        made.head = build(git_mod.Git(path), remote, production, units, branch)
        yield made
    finally:
        if repo_git.attempt("worktree", "remove", "--force", "--", str(path)).returncode != 0:
            made.leftover.append(str(path))
        shutil.rmtree(parent, ignore_errors=True)
        if made.leftover:
            repo_git.attempt("worktree", "prune")
