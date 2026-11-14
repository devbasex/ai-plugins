"""リリースのブランチを組み立て、リリースの PR を作る（GitHub Actions の workflow が打つ）。

手順（`assemble`）:

1. origin の本番ブランチとベースブランチを取り込み、範囲（本番ブランチの先端..ベースブランチの先端）を決める
2. 変わったファイルから版を上げるプラグインと次の版を決める。上げるものが無ければ何もせずに `not_applicable`
3. ベースブランチの先端から `release/v<版>` を作り、版を書き換え、本番の版なら CHANGELOG に節を足し、宣言の手順
   （トークン消費の記録など）を流す
4. 1 つのコミットにして push し、リリースの PR（本番は本番ブランチ宛て、開発版はベースブランチ宛て）を作り、承認資料を
   PR の本文に書く

ブランチの名前の版は、版を上げるプラグインのうち `--plugin` で渡したもの（省けば登録の順の先頭）の版である。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ndf.adapters import git as git_mod
from ndf.adapters.github import GitHub
from ndf.adapters.publish import Publisher
from ndf.domains.release import changelog_decide, changes_decide, gather, materials_decide, version_decide
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod


class NothingToRelease(Exception):
    """版を上げるプラグインが無い。"""


class AssembleError(Exception):
    """組み立ての前提が無い（ブランチが既にある・名前のプラグインが上げる対象に無い）。"""


@dataclass(frozen=True)
class Target:
    """組み立ての指定。"""

    channel: str
    level: str
    base: str
    production: str
    plugin: str | None = None

    @property
    def is_production(self) -> bool:
        """本番のリリースか。"""
        return self.channel == version_decide.PRODUCTION

    @property
    def pr_base(self) -> str:
        """リリースの PR の宛先（本番は本番ブランチ、開発版はベースブランチ）。"""
        return self.production if self.is_production else self.base


@dataclass(frozen=True)
class Built:
    """組み立ての結果。"""

    branch: str
    version: str
    pr: int
    url: str
    facts: materials_decide.Facts


def _span(repo_git: git_mod.Git, target: Target) -> gather.Range:
    for name in (target.base, target.production):
        gather.fetch(repo_git, name)
    return gather.Range(f"{git_mod.ORIGIN}/{target.production}", f"{git_mod.ORIGIN}/{target.base}")


def _named(planned: Sequence[version_decide.Planned], plugin: str | None) -> version_decide.Planned:
    if plugin is None:
        return planned[0]
    hit = [p for p in planned if p.plugin == plugin]
    if not hit:
        raise AssembleError(f"{plugin} は版を上げる対象に無い（上げるもの: {', '.join(p.plugin for p in planned)}）")
    return hit[0]


def _write(root: Path, decl: decl_mod.Declaration, facts: materials_decide.Facts) -> None:
    """版・CHANGELOG を書き換え、宣言の手順を流す。"""
    found = plugins_mod.by_name(plugins_mod.load(root))
    entries = [changelog_decide.Entry(p.number, p.title) for p in facts.pulls]
    log_path = root / decl.changelog
    for p in facts.planned:
        plugins_mod.bump(root, found[p.plugin], p.next, decl.version_files)
        if facts.channel == version_decide.PRODUCTION and log_path.is_file():
            new = changelog_decide.block(p.plugin, p.next, date.today().isoformat(), entries)
            log_path.write_text(changelog_decide.insert(log_path.read_text(encoding="utf-8"), p.plugin, p.next, new), encoding="utf-8")
    for step in decl.for_stage(facts.channel):
        decl_mod.run_step(root, step, facts.planned[0].next)


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


def collect(root: Path, hub: GitHub, target: Target) -> materials_decide.Facts:
    """承認資料の材料（版上げの計画を含む。ブランチは作らない）。上げるものが無ければ `NothingToRelease`。"""
    repo_git = git_mod.Git(root)
    span = _span(repo_git, target)
    decl = decl_mod.load(root)
    found = plugins_mod.load(root)
    paths = repo_git.changed_files(span.since, span.until)
    change_list = gather.changes(paths, found, decl)
    planned = gather.plan_bumps(root, found, change_list, decl, target.level, target.channel)
    if not planned:
        raise NothingToRelease(f"{span.since}..{span.until} に版を上げる変化が無い")
    pulls = gather.pull_facts(hub, gather.included(repo_git, span))
    return materials_decide.Facts(
        target.channel,
        tuple(planned),
        tuple(pulls),
        tuple(change_list),
        tuple(changes_decide.instruction_changes(paths)),
        repo_git.rev_parse(span.until),
    )


def assemble(root: Path, hub: GitHub, target: Target) -> Built:
    """リリースのブランチを組み立てて PR を作る。"""
    repo_git = git_mod.Git(root)
    facts = collect(root, hub, target)
    named = _named(facts.planned, target.plugin)
    branch = version_decide.branch_of(named.next)
    if repo_git.attempt("ls-remote", "--exit-code", "--heads", "--", git_mod.ORIGIN, branch).returncode == 0:
        raise AssembleError(f"origin に {branch} が既にある（前の組み立ての PR を先に片付ける）")
    repo_git.run("switch", "--quiet", "--create", branch, facts.head)
    _write(root, decl_mod.load(root), facts)
    title = f"Release: {named.plugin} v{named.next}"
    head = _commit_and_push(repo_git, branch, title)
    facts = facts._replace(head=head)
    number, url = _open_pr(hub, branch, target.pr_base, title, materials_decide.render(facts))
    return Built(branch, named.next, number, url, facts)
