"""本番の PR（main 宛てのスプリントの PR）の承認資料を組み、PR の本文の承認資料の節へ書く（#3572 の決定 6）。

GitHub flow ではリリースのためのブランチを組まない。スプリントの PR がリリース承認ゲートの対象で、マージ＝リリース承認＝
マージ承認である。手順（`assemble`）:

1. 取り込み方の行が `github-flow` かを確かめる（ほかの行では止まる。決定 5）
2. origin の本番ブランチと PR の先端（`refs/pull/<番号>/head`）を取り込み、範囲（本番ブランチの先端..PR の先端）を決める
3. 変わったファイルから版を上げるプラグインを決め、予定の版を PR の上げ幅の宣言（ラベル）から出す。上げるものが無ければ
   `not_applicable`。確定の版は CD が決める（決定 7）
4. PR の本文の承認資料の節だけを書き直す（`materials_decide.splice`）。本文の書き換えは先端を動かさないため、承認の対象を変えない

ブランチを作らず、push しない。差し戻しの後に直しが同じ PR へ入れば、打ち直すと新しい先端で資料を書き直す。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ndf.adapters import git as git_mod
from ndf.adapters.github import GitHub
from ndf.adapters.publish import Publisher
from ndf.domains.release import bump_decide, changes_decide, gather, materials_decide, version_decide
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod


class NothingToRelease(Exception):
    """版を上げるプラグインが無い。"""


class AssembleError(Exception):
    """組み立ての前提が無い（worktree の先端が PR の先端でない・名前のプラグインが上げる対象に無い）。"""


@dataclass(frozen=True)
class Target:
    """組み立ての指定（本番ブランチ・本番の PR・予定の版を出すプラグイン）。"""

    production: str
    pr: int
    plugin: str | None = None


@dataclass(frozen=True)
class Built:
    """組み立ての結果。"""

    pr: int
    version: str
    url: str
    facts: materials_decide.Facts


@dataclass(frozen=True)
class _Run:
    """1 回の組み立て（か材料の収集）で持ち回る、worktree の根・その git・GitHub・組み立ての指定。"""

    root: Path
    repo_git: git_mod.Git
    hub: GitHub
    target: Target


def _run_of(root: Path, hub: GitHub, target: Target) -> _Run:
    """公開のエントリポイント（`assemble`・`collect`）の引数から `_Run` を作る（取り込み方の行を先に確かめる）。"""
    decl_mod.require_github_flow(root)
    return _Run(root, git_mod.Git(root), hub, target)


def _span(run: _Run) -> gather.Range:
    """範囲（`origin/<本番ブランチ>..refs/remotes/origin/pull/<番号>`）。両端を取り込む。"""
    gather.fetch(run.repo_git, run.target.production)
    head = gather.fetch_pull(run.repo_git, run.target.pr)
    return gather.Range(f"{git_mod.ORIGIN}/{run.target.production}", head)


def _named(planned: Sequence[version_decide.Planned], plugin: str | None) -> version_decide.Planned:
    if plugin is None:
        return planned[0]
    hit = [p for p in planned if p.plugin == plugin]
    if not hit:
        raise AssembleError(f"{plugin} は版を上げる対象に無い（上げるもの: {', '.join(p.plugin for p in planned)}）")
    return hit[0]


def _facts(run: _Run, span: gather.Range) -> materials_decide.Facts:
    """範囲の変更と含む PR から材料を読む。上げ幅は PR のラベルから求め、上げるものが無ければ `NothingToRelease`。"""
    root, repo_git, hub, target = run.root, run.repo_git, run.hub, run.target
    decl = decl_mod.load(root)
    found = plugins_mod.load(root)
    level = bump_decide.level_of(gather.labels_of(hub, target.pr))
    paths = repo_git.changed_files(span.since, span.until)
    change_list = gather.changes(paths, found, decl)
    bumps = gather.plan_bumps(root, found, change_list, decl, level, version_decide.PRODUCTION)
    shipped = gather.shipped(repo_git, found, span.since)
    planned = gather.clear_shipped(bumps, shipped, level, version_decide.PRODUCTION)
    if not planned:
        raise NothingToRelease(f"{span.since}..{span.until} に版を上げる変化が無い")
    pulls = gather.pull_facts(hub, gather.included(repo_git, span, target.production))
    return materials_decide.Facts(
        version_decide.PRODUCTION,
        tuple(planned),
        tuple(pulls),
        tuple(change_list),
        tuple(changes_decide.instruction_changes(paths)),
        repo_git.rev_parse(span.until),
    )


def collect(root: Path, hub: GitHub, target: Target) -> materials_decide.Facts:
    """承認資料の材料（ブランチは作らない）。上げるものが無ければ `NothingToRelease`。

    宣言・マーケットプレイス・今の版は worktree から読むため、worktree の HEAD が PR の先端でなければ `AssembleError` で止まる。
    """
    run = _run_of(root, hub, target)
    span = _span(run)
    if run.repo_git.rev_parse("HEAD") != run.repo_git.rev_parse(span.until):
        raise AssembleError(f"worktree の HEAD が #{target.pr} の先端でない（PR の先端で打つ）")
    return _facts(run, span)


def assemble(root: Path, hub: GitHub, target: Target) -> Built:
    """承認資料を組み、本番の PR の本文の承認資料の節だけを書き直す。PR の先端を取り出してから材料を読む。"""
    run = _run_of(root, hub, target)
    span = _span(run)
    run.repo_git.run("switch", "--quiet", "--detach", span.until)
    facts = _facts(run, span)
    named = _named(facts.planned, target.plugin)
    body = materials_decide.splice(hub.pull(target.pr).body, materials_decide.render(facts))
    Publisher(hub).edit_body(target.pr, body)
    url = f"https://github.com/{hub.repo}/pull/{target.pr}"
    return Built(target.pr, named.next, url, facts)
