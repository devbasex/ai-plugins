"""`review-scope` の範囲を、手元のオブジェクトと状態の記録から決める（リモートから取得しない。#3181・#3780）。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NamedTuple, Optional, Protocol

from ndf.adapters import git
from ndf.domains.crossreview import changes, round_scope, scope_decide, store
from ndf.domains.records import mvv


class _Policy(Protocol):
    @property
    def main(self) -> Path: ...


class _Seats(Protocol):
    @property
    def names(self) -> tuple[str, ...]: ...


class _Settings(Protocol):
    @property
    def focus(self) -> tuple[str, ...]: ...


class ScopeSettings(Protocol):
    """範囲を決めるのに読む、実行者の設定から決まる値（`cli.Resolved`）。"""

    @property
    def policy(self) -> _Policy: ...

    @property
    def seats(self) -> _Seats: ...

    @property
    def settings(self) -> _Settings: ...

    @property
    def unit(self) -> str: ...


class _LocalBase(NamedTuple):
    """取得済みの参照だけから決めた、差分の比較元のコミットと、MVV を読む参照（決められなければ None）。"""

    commit: str
    mvv_ref: Optional[str]


def _local_base(g: git.Git, remote: str, base: str, branch: Optional[str]) -> _LocalBase:
    """`run` の起動の始めと同じ決め方で、リモートから取得せずに比較元と MVV を読む参照を決める。

    `--base` がブランチなら、取得済みのリモートのブランチの先頭が両方になる。コミット（SHA・タグなど）なら、MVV は PR の
    マージ先のブランチから読むので、`run` がラウンドの記録に残した `branch` を使う。記録が無ければ MVV を読む参照は決められない。
    """
    tracking = f"refs/remotes/{remote}"
    commit = base if git.FULL_SHA.match(base) else None
    if commit is None:
        try:
            tip = g.rev_parse(f"{tracking}/{base.removeprefix(f'{remote}/')}")
            return _LocalBase(tip, tip)
        except git.GitError:
            commit = g.commit_named(base)
    if commit is None:
        # リモートに無い手元だけのブランチ。`run` は止まるので、MVV を読む先も無い
        return _LocalBase(base, None)
    return _LocalBase(commit, f"{tracking}/{branch}" if branch is not None else None)


def _local_mvv(wt: Path, ref: Optional[str]) -> tuple[str, list[str]]:
    """参照 `ref` の MVV を見分ける値と注意。確かめられなければ `scope_decide.UNKNOWN_MVV`（全差分になる）。"""
    if ref is None:
        why = "MVV を読むブランチを決められない（`--base` は取得済みのリモートのブランチか、`run` に渡したコミットにする）"
    else:
        try:
            return mvv.identity_at(wt, ref), []
        except mvv.MvvError as exc:
            why = str(exc)
    return scope_decide.UNKNOWN_MVV, [f"前のラウンドと MVV が同じかを確かめられないので、全差分を範囲にした: {why}"]


def _local_sections(g: git.Git, base: str, wt: Path) -> tuple[list[str], Optional[str]]:
    """変わった Markdown のセクション。手元のオブジェクトだけで読めなければ、読めない理由（全差分になる）。"""
    try:
        return changes.changed_sections(g, base, wt), None
    except git.GitError as exc:
        return [], f"変わった文書のセクションを手元のオブジェクトだけで読めないので、全差分を範囲にした: {exc}"


def decide(
    wt: Path, base: str, every_section: Callable[[], bool], data: Mapping[str, Any], resolved: ScopeSettings
) -> tuple[scope_decide.Scope, list[str]]:
    """次のラウンドの範囲と注意を、手元のオブジェクトと状態の記録 `data` から決める（リモートから取得しない）。

    `every_section` は、範囲がセクションの読み取りで全差分に決まらないときだけ呼ぶ（呼ぶとルートのテンプレートを読んで検証する）。
    """
    rounds = store.list_of(data, store.ROUNDS)
    g = git.Git.offline(wt)
    # `cli._resolve` が求めたメインディレクトリから読む（`g.remote()` は `git worktree list` を打ち直す）
    based = _local_base(g, git.remote_of(resolved.policy.main), base, scope_decide.mvv_branch(data))
    # ラウンドの記録の比較基点は merge-base の SHA（PR 3944 の r2-1）。`--base` の誤りは、セクションを読む前にここで止まる
    merge_base = g.merge_base(based.commit, "HEAD")
    sections, unread = _local_sections(g, based.commit, wt)
    if unread is not None:
        # 全差分に決まっているので、範囲を変えない MVV を読まない
        return scope_decide.Scope(scope_decide.FULL, None, ()), [unread]
    now, unsure = _local_mvv(wt, based.mvv_ref)
    unit = scope_decide.unit_id(resolved.unit)
    basis = scope_decide.Basis(merge_base, resolved.seats.names, resolved.settings.focus, now, unit=unit)
    prev, reviewed = scope_decide.read_by_all_head(data, basis), scope_decide.reviewed_of(data, basis.mvv)
    found = scope_decide.review_scope(every_section(), len(rounds) + 1, prev, sections, reviewed)
    got = round_scope.descended(g, found, "HEAD")
    return got, unsure if rounds else []
