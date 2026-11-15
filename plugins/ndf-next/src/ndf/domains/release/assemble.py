"""承認資料を組む。取り込み方の行で振る舞いを選ぶ（#3572 の決定 6・16）。

| 行 | すること |
| --- | --- |
| `github-flow` | 本番の PR（本番ブランチ宛てのスプリントの PR）の本文の承認資料の節だけを書く。ブランチを作らず、push しない |
| ほか（`promote-whole`・`promote-selected`） | リリースブランチを組み、バージョンを上げ、リリースの PR を作る |

`github-flow` の手順:

1. origin の本番ブランチと PR の head（`refs/pull/<番号>/head`）を取り込み、範囲（本番ブランチの先端..PR の head）を決める
2. 変わったファイルからバージョンを上げる単位を決め（`version_store`）、予定のバージョンを PR の上げ幅のラベルから出す。上げるものが
   無ければ `not_applicable`。確定のバージョンは CD が決める（決定 7）
3. PR の本文の承認資料の節だけを書き直す（`materials_decide.splice`）。本文の書き換えは head を動かさないため、承認の対象を変えない

ほかの行の手順（`_assemble_branch`）:

1. origin の本番ブランチとベースブランチを取り込み、範囲（本番ブランチの先端..ベースブランチの先端）を決める
2. 変わったファイルから版を上げるプラグインと次の版を決める。上げるものが無ければ何もせずに `not_applicable`
3. ベースブランチの先端から `release/v<版>` を作り、版を書き換え、本番の版なら CHANGELOG に節を足し、宣言の手順を流す（`bump.write_bump`）
4. 1 つのコミットにして push し、リリースの PR（本番は本番ブランチ宛て、開発版はベースブランチ宛て）を作り、承認資料を本文に書く

本番のリリースは、本番ブランチ宛ての別のリリースの PR が開いている間は組まずに `ReleaseOpen` で止まる（#2441）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ndf.adapters import git as git_mod
from ndf.adapters.github import PER_PAGE, GitHub, text_of
from ndf.adapters.publish import Publisher
from ndf.domains.release import bump, bump_decide, changes_decide, gather, materials_decide, version_decide, version_store
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod


class NothingToRelease(Exception):
    """版を上げる単位が無い。"""


class ReleaseOpen(Exception):
    """本番ブランチ宛ての別のリリースの PR が開いている（閉じるまで組まない）。"""


class AssembleError(Exception):
    """組み立ての前提が無い（行に合わない引数・worktree の先端が範囲の先端でない・ブランチが既にある・名前の単位が上げる対象に無い）。"""


@dataclass(frozen=True)
class Target:
    """組み立ての指定。`github-flow` は本番の PR（`pr`）を、ほかの行はチャネル・上げ幅・ベースブランチを使う。"""

    production: str
    pr: int | None = None
    plugin: str | None = None
    channel: str | None = None
    level: str = version_decide.LEVELS[2]
    base: str | None = None

    @property
    def is_production(self) -> bool:
        """本番のリリースか。"""
        return self.channel == version_decide.PRODUCTION

    @property
    def pr_base(self) -> str:
        """リリースの PR の宛先（本番は本番ブランチ、開発版はベースブランチ）。"""
        return self.production if self.is_production or self.base is None else self.base


@dataclass(frozen=True)
class Built:
    """組み立ての結果（`branch` は今の行のリリースブランチ。`github-flow` では None）。"""

    pr: int
    version: str
    url: str
    facts: materials_decide.Facts
    branch: str | None = None


@dataclass(frozen=True)
class _Run:
    """1 回の組み立て（か材料の収集）で持ち回る、worktree の根・その git・GitHub・組み立ての指定・取り込み方の行。"""

    root: Path
    repo_git: git_mod.Git
    hub: GitHub
    target: Target
    flow: str

    @property
    def trunk(self) -> bool:
        return self.flow == decl_mod.GITHUB_FLOW


def _run_of(root: Path, hub: GitHub, target: Target) -> _Run:
    """公開のエントリポイントの引数から `_Run` を作る。行に合わない引数なら `AssembleError`。"""
    run = _Run(root, git_mod.Git(root), hub, target, decl_mod.flow_of(root))
    if run.trunk and (target.pr is None or target.channel is not None or target.base is not None):
        raise AssembleError(f"取り込み方 {run.flow} では --pr（本番の PR）だけを渡す（--channel・--base は使わない）")
    if not run.trunk and (target.channel is None or target.base is None or target.pr is not None):
        raise AssembleError(f"取り込み方 {run.flow} では --channel と --base が要る（--pr は使わない）")
    return run


def _span(run: _Run) -> gather.Range:
    """範囲。`github-flow` は本番ブランチの先端..PR の head、ほかの行は本番ブランチの先端..ベースブランチの先端。両端を取り込む。"""
    target = run.target
    gather.fetch(run.repo_git, target.production)
    if run.trunk and target.pr is not None:
        return gather.Range(f"{git_mod.ORIGIN}/{target.production}", gather.fetch_pull(run.repo_git, target.pr))
    base = str(target.base)
    gather.fetch(run.repo_git, base)
    return gather.Range(f"{git_mod.ORIGIN}/{target.production}", f"{git_mod.ORIGIN}/{base}")


def _named(planned: Sequence[version_decide.Planned], plugin: str | None) -> version_decide.Planned:
    if plugin is None:
        return planned[0]
    hit = [p for p in planned if p.plugin == plugin]
    if not hit:
        raise AssembleError(f"{plugin} は版を上げる対象に無い（上げるもの: {', '.join(p.plugin for p in planned)}）")
    return hit[0]


def _materials(
    run: _Run,
    span: gather.Range,
    channel: str,
    planned: Sequence[version_decide.Planned],
    change_list: Sequence[changes_decide.Change],
    paths: Sequence[str],
) -> materials_decide.Facts:
    """上げる予定と変化から材料を組む。上げるものが無ければ `NothingToRelease`。"""
    if not planned:
        raise NothingToRelease(f"{span.since}..{span.until} に版を上げる変化が無い")
    pulls = gather.pull_facts(run.hub, gather.included(run.repo_git, span, run.target.production))
    return materials_decide.Facts(
        channel,
        tuple(planned),
        tuple(pulls),
        tuple(change_list),
        tuple(changes_decide.instruction_changes(paths)),
        run.repo_git.rev_parse(span.until),
    )


def _trunk_facts(run: _Run, span: gather.Range) -> materials_decide.Facts:
    """`github-flow` の材料。上げ幅は PR のラベルから、今のバージョンは本番ブランチの先端から読む（バージョンの置き場を介す）。"""
    decl = decl_mod.load(run.root)
    store = version_store.of(run.root, decl, run.flow)
    level = bump_decide.level_of(gather.labels_of(run.hub, int(run.target.pr or 0)))
    paths = run.repo_git.changed_files(span.since, span.until)
    planned = version_store.plan(store, run.repo_git, paths, decl, level, span.since)
    return _materials(run, span, version_decide.PRODUCTION, planned, store.changes(paths, decl), paths)


def _branch_facts(run: _Run, span: gather.Range) -> materials_decide.Facts:
    """今の行の材料（worktree は範囲の先端を取り出したもの）。"""
    root, repo_git, target = run.root, run.repo_git, run.target
    channel = str(target.channel)
    decl = decl_mod.load(root)
    found = plugins_mod.load(root)
    paths = repo_git.changed_files(span.since, span.until)
    change_list = gather.changes(paths, found, decl)
    planned = gather.clear_shipped(
        gather.plan_bumps(root, found, change_list, decl, target.level, channel),
        gather.shipped(repo_git, found, span.since),
        target.level,
        channel,
    )
    return _materials(run, span, channel, planned, change_list, paths)


def _facts(run: _Run, span: gather.Range) -> materials_decide.Facts:
    return _trunk_facts(run, span) if run.trunk else _branch_facts(run, span)


def collect(root: Path, hub: GitHub, target: Target) -> materials_decide.Facts:
    """承認資料の材料（ブランチは作らない）。上げるものが無ければ `NothingToRelease`。

    宣言と今の版は worktree から読むため、worktree の HEAD が範囲の先端でなければ `AssembleError` で止まる。
    """
    run = _run_of(root, hub, target)
    span = _span(run)
    if run.repo_git.rev_parse("HEAD") != run.repo_git.rev_parse(span.until):
        raise AssembleError(f"worktree の HEAD が {span.until} の先端でない（範囲の先端で打つ）")
    return _facts(run, span)


def open_releases(hub: GitHub, target: Target) -> list[int]:
    """本番ブランチ宛ての開いているリリースの PR（head が `release/v` で始まるかベースブランチ）の番号。

    1 ページ（`PER_PAGE` 件）で読み切れなければ、確かめられないとして `AssembleError`。
    """
    rows = hub.rest("GET", f"/repos/{hub.repo}/pulls", params={"state": "open", "base": target.production, "per_page": PER_PAGE})
    found = rows if isinstance(rows, list) else []
    if len(found) >= PER_PAGE:
        raise AssembleError(f"{target.production} 宛ての開いている PR が {PER_PAGE} 件以上あり、リリースの PR の有無を確かめられない")
    heads = [(int(r["number"]), text_of(r["head"]["ref"])) for r in found if isinstance(r, dict)]
    return sorted(n for n, ref in heads if ref.startswith(version_decide.BRANCH_PREFIX) or ref == target.base)


def _commit_and_push(repo_git: git_mod.Git, branch: str, message: str) -> str:
    repo_git.run("add", "--all")
    repo_git.run("commit", "--quiet", "-m", message)
    repo_git.run("push", "--quiet", "--", git_mod.ORIGIN, f"HEAD:refs/heads/{branch}")
    return repo_git.rev_parse("HEAD")


def _open_pr(hub: GitHub, branch: str, base: str, title: str, body: str) -> tuple[int, str]:
    made = hub.rest("POST", f"/repos/{hub.repo}/pulls", {"title": title, "head": branch, "base": base, "body": ""})
    number = int(made["number"])
    Publisher(hub).edit_body(number, body)
    return number, str(made["html_url"])


def _assemble_branch(run: _Run, span: gather.Range) -> Built:
    """今の行のリリースブランチを組み、バージョンを上げて PR を作る。"""
    root, repo_git, hub, target = run.root, run.repo_git, run.hub, run.target
    facts = _branch_facts(run, span)
    if target.is_production:
        opened = open_releases(hub, target)
        if opened:
            listed = ", ".join(f"#{n}" for n in opened)
            raise ReleaseOpen(f"{target.production} 宛てのリリースの PR {listed} が開いている（閉じた後に打ち直す）")
    named = _named(facts.planned, target.plugin)
    branch = version_decide.branch_of(named.next)
    if gather.remote_has_branch(repo_git, branch):
        raise AssembleError(f"origin に {branch} が既にある（前の組み立ての PR を先に片付ける）")
    repo_git.run("switch", "--quiet", "--create", branch, facts.head)
    decl = decl_mod.load(root)
    store = version_store.of(root, decl, run.flow)
    bump.write_bump(root, store, decl, facts.planned, facts.pulls, date.today().isoformat(), facts.channel)
    title = f"Release: {named.plugin} v{named.next}"
    facts = facts._replace(head=_commit_and_push(repo_git, branch, title))
    number, url = _open_pr(hub, branch, target.pr_base, title, materials_decide.render(facts))
    return Built(number, named.next, url, facts, branch)


def assemble(root: Path, hub: GitHub, target: Target) -> Built:
    """行に合わせて承認資料を組む。範囲の先端を取り出してから材料を読む。"""
    run = _run_of(root, hub, target)
    span = _span(run)
    run.repo_git.run("switch", "--quiet", "--detach", span.until)
    if not run.trunk:
        return _assemble_branch(run, span)
    number = int(target.pr or 0)
    facts = _trunk_facts(run, span)
    named = _named(facts.planned, target.plugin)
    body = materials_decide.splice(hub.pull(number).body, materials_decide.render(facts))
    Publisher(hub).edit_body(number, body)
    return Built(number, named.next, f"https://github.com/{hub.repo}/pull/{number}", facts)
