"""`release_branch` のリリースの単位を組む: 本番ブランチの HEAD から、選んだスプリントブランチを順に `--no-ff` でマージする
（10 系の `strategy_route._build_release_branch` を移したもの。#3615 の決定 8）。

- 組むのは detached HEAD で、手元のブランチを作らず動かさない。既に本番ブランチに入っているスプリントは飛ばす
- 取得は `pin` の 1 回だけで、`build` は固定した SHA から組む（計画と最終の組み立てで、間に進んだブランチを混ぜない）
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
from typing import NamedTuple

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


def _merge_unit(repo_git: git_mod.Git, unit: str, sha: str, branch: str) -> None:
    """HEAD へ単位 `unit`（固定した SHA `sha`）を `--no-ff` でマージする。既に入っていれば飛ばし、衝突が残れば取り消して `UnitsError`。"""
    if repo_git.is_ancestor(sha, "HEAD"):
        return
    subject = merge_subject(unit, branch)
    merged = repo_git.attempt(
        "-c", "rerere.enabled=true", "-c", "rerere.autoupdate=true", "merge", "--no-ff", "--no-edit", "-m", subject,
        git_mod.END_OF_OPTIONS, sha,
    )  # fmt: skip
    if merged.returncode != 0 and not _resolved_by_rerere(repo_git, subject):
        repo_git.attempt("merge", "--abort")
        raise UnitsError(f"{unit} を {branch} へマージできない（衝突は人が解く）: {(merged.stdout + merged.stderr).strip()[-TAIL:]}")


class Pins(NamedTuple):
    """組む元の SHA: 本番ブランチ（`production`）と、選んだスプリントごと（`units`。選んだ順）。"""

    production: str
    units: tuple[tuple[str, str], ...]


def pin(repo_git: git_mod.Git, remote: str, production: str, units: Sequence[str]) -> Pins:
    """本番ブランチと `units` を取得し、その時点の SHA に固定する（計画と最終の組み立てを同じ SHA から組むため）。"""
    if not units:
        raise UnitsError("リリースの単位（--units）が無い")
    for name in (production, *units):
        done = repo_git.fetch_branch(remote, name)
        if done.returncode != 0:
            raise UnitsError(f"{remote} の {name} を取得できない: {done.stderr.strip()}")
    return Pins(repo_git.rev_parse(f"{remote}/{production}"), tuple((u, repo_git.rev_parse(f"{remote}/{u}")) for u in units))


def build(repo_git: git_mod.Git, pins: Pins, branch: str) -> str:
    """固定した本番ブランチの SHA から detached HEAD で単位を順にマージし、先頭の SHA を返す（取得しない）。"""
    done = repo_git.checkout_detached(pins.production)
    if done.returncode != 0:
        raise UnitsError(f"{pins.production} から {branch} を切れない: {done.stderr.strip()}")
    for unit, sha in pins.units:
        _merge_unit(repo_git, unit, sha, branch)
    return repo_git.rev_parse("HEAD")


@dataclass
class Scratch:
    """一時の作業場所で組んだ単位。`leftover` は外せなかった作業場所のパス（`git worktree prune` で外れる）。"""

    path: Path
    head: str
    leftover: list[str] = field(default_factory=list)


@contextmanager
def scratch(repo_git: git_mod.Git, remote: str, production: str, units: Sequence[str], branch: str) -> Iterator[Scratch]:
    """一時の作業場所で `build` を流し、抜けるときに作業場所を外す（例外でも外す）。取得（`pin`）は一時の場所を作る前に済ませる。"""
    pins = pin(repo_git, remote, production, units)
    parent = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX))
    path = parent / "work"
    repo_git.run("worktree", "add", "--quiet", "--detach", "--", str(path), pins.production)
    made = Scratch(path, "")
    try:
        made.head = build(git_mod.Git(path), pins, branch)
        yield made
    finally:
        if repo_git.attempt("worktree", "remove", "--force", "--", str(path)).returncode != 0:
            made.leftover.append(str(path))
        shutil.rmtree(parent, ignore_errors=True)
        if made.leftover:
            repo_git.attempt("worktree", "prune")
