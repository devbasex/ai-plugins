"""リリースブランチを組み立て、リリースの PR を作る（GitHub Actions の workflow が打つ）。承認資料の材料もここで組む（`collect`）。

手順（`assemble`）:

1. 設定のリリースの形を確かめる（扱わない形は止まる。I1）。範囲の取り方をリリース方式から決める（`since_decide.kind_of`）
2. リモートの本番ブランチ（とベースブランチ）を取得し、範囲の終点を決める。`release_branch` は本番ブランチの HEAD へ選んだ
   スプリント（`--units`）を集めた先頭、`trunk` は本番ブランチの HEAD、`merge_base_branch` はベースブランチの HEAD
3. 範囲の起点を決め（`trunk`・`release_branch` はバージョンの単位ごとの前のリリースの起点）、変わったファイルからバージョンを上げる
   単位と次のバージョンを決める。上げるものが無ければ何もせずに `not_applicable`
4. 範囲の終点から書式の名前のリリースブランチを作り、保存先のアダプターでバージョンを書き、本番のバージョンなら CHANGELOG に
   見出しの書式でセクションを足し、設定の手順（トークン消費の記録など）を流す
5. 1 つのコミットにしてリモートへ送り、リリースの PR（本番は本番ブランチ宛て、開発版はベースブランチ宛て）を作り、承認資料を
   PR の本文に書く

バージョンアップの PR を作らないリリース方式（`production_pr`・版数を持たない `merge_base_branch`）では組み立てず `NoReleasePr`。
ブランチの名前のバージョンは、バージョンを上げる単位のうち `--plugin` で渡したもの（省けば単位の順の先頭）のバージョンである。

本番のリリースは、本番ブランチ宛ての別のリリースの PR（head がリリースブランチの書式に合うかベースブランチ）が開いている
間は組まずに `ReleaseOpen` で止まる（#2441）。リモート名は `Target.remote` だけを使う（I8）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from ndf.adapters import git as git_mod
from ndf.adapters.github import PER_PAGE, GitHub, text_of
from ndf.adapters.publish import Publisher
from ndf.core.branch_strategy import Strategy
from ndf.domains.release import (
    changelog_decide,
    changes_decide,
    forms_decide,
    gather,
    materials_decide,
    names_decide,
    route_decide,
    since_decide,
    units,
    version_decide,
)
from ndf.domains.release import ndf_settings as decl_mod
from ndf.domains.release import version_store as store_mod
from ndf.domains.release.since_decide import SpanKind


class NothingToRelease(Exception):
    """バージョンを上げる単位が無い。"""


class NoReleasePr(Exception):
    """設定のリリース方式はバージョンアップの PR を作らない（`production_pr`・版数を持たない `merge_base_branch`）。"""


class ReleaseOpen(Exception):
    """本番ブランチ宛ての別のリリースの PR が開いている（閉じるまで組まない）。"""


class AssembleError(Exception):
    """組み立ての前提が無い（ブランチが既にある・名前の単位が上げる対象に無い・引数が足りない）。"""


# `release_branch` の設定で `--units` が空のときの文言
_UNITS_REQUIRED = "release_branch の設定では --units に集めるスプリントブランチを渡す"


@dataclass(frozen=True)
class Target:
    """組み立ての指定。`units` は `release_branch` で集めるスプリントブランチ、`pr` は `production_pr` の本番の PR。"""

    channel: str
    level: str
    base: str
    production: str
    remote: str
    plugin: Optional[str] = None
    units: tuple[str, ...] = ()
    pr: Optional[int] = None

    @property
    def is_production(self) -> bool:
        """本番のリリースか。"""
        return self.channel == version_decide.PRODUCTION

    @property
    def pr_base(self) -> str:
        """リリースの PR のマージ先（本番は本番ブランチ、開発版はベースブランチ）。"""
        return self.production if self.is_production else self.base

    def ref(self, name: str) -> str:
        """リモートのブランチの参照（`<remote>/<name>`）。"""
        return f"{self.remote}/{name}"

    def required_units(self) -> tuple[str, ...]:
        """`release_branch` の設定で集めるスプリントブランチ。空なら `AssembleError`。"""
        if not self.units:
            raise AssembleError(_UNITS_REQUIRED)
        return self.units


@dataclass(frozen=True)
class Built:
    """組み立ての結果。"""

    branch: str
    version: str
    pr: int
    url: str
    facts: materials_decide.Facts


@dataclass(frozen=True)
class _Setting:
    """1 回の実行で設定から決めたもの。"""

    strategy: Strategy
    kind: SpanKind
    form: forms_decide.Form
    formats: names_decide.NameFormats
    versioned: bool


def _setting(strategy: Strategy) -> _Setting:
    release = strategy.section["release"]
    form = forms_decide.form_of(release["form"])
    return _Setting(strategy, since_decide.kind_of(strategy), form, names_decide.formats_of(release), route_decide.route_of(strategy).bump)


def _provisional(formats: names_decide.NameFormats) -> str:
    """単位を集める前に件名へ書く仮のリリースブランチの名前（承認資料の材料を読むときだけ使う）。"""
    return formats.branch_of(store_mod.INITIAL)


def _span(repo_git: git_mod.Git, target: Target, kind: SpanKind, hub: GitHub) -> tuple[str, Optional[str]]:
    """要るブランチを取得し、範囲の終点と（`pr` の取り方では）本番ブランチとの分岐点を返す。`units` は呼び手が組む。"""
    gather.fetch(repo_git, target.remote, target.production)
    if kind == SpanKind.BASE:
        gather.fetch(repo_git, target.remote, target.base)
        return target.ref(target.base), None
    if kind == SpanKind.TRUNK:
        return target.ref(target.production), None
    if kind == SpanKind.UNITS:
        raise AssembleError(_UNITS_REQUIRED)
    if target.pr is None:
        raise AssembleError("本番の PR が承認の対象の設定（production_pr）では --pr に本番の PR の番号を渡す")
    pull = hub.pull(target.pr)
    if pull.base_ref != target.production:
        raise AssembleError(f"#{target.pr} は本番ブランチ {target.production} 宛てでない（{pull.base_ref}）")
    head = target.ref(f"pull/{target.pr}")
    done = repo_git.fetch(target.remote, f"+refs/pull/{target.pr}/head:refs/remotes/{head}")
    if done.returncode != 0:
        raise git_mod.GitError(f"{target.remote} の #{target.pr} の先頭を取得できない: {done.stderr.strip()}")
    return head, repo_git.merge_base(target.ref(target.production), head)


@contextmanager
def _units_errors() -> Iterator[None]:
    """スプリントブランチを集める失敗（`units.UnitsError`）を `AssembleError` にする。"""
    try:
        yield
    except units.UnitsError as exc:
        raise AssembleError(str(exc)) from None


def _select_branch_unit(planned: Sequence[version_decide.Planned], unit_name: Optional[str]) -> version_decide.Planned:
    """リリースブランチの名前のバージョンを決める単位の計画（`unit_name` が None なら単位の順の先頭）。"""
    if unit_name is None:
        return planned[0]
    hit = [p for p in planned if p.plugin == unit_name]
    if not hit:
        raise AssembleError(f"{unit_name} はバージョンを上げる対象に無い（上げるもの: {', '.join(p.plugin for p in planned)}）")
    return hit[0]


def open_releases(hub: GitHub, target: Target, formats: names_decide.NameFormats) -> list[int]:
    """本番ブランチ宛ての開いているリリースの PR（head がリリースブランチの書式に合うかベースブランチ）の番号。

    1 ページ（`PER_PAGE` 件）で読み切れなければ、確かめられないとして `AssembleError`。
    """
    rows = hub.rest("GET", f"/repos/{hub.repo}/pulls", params={"state": "open", "base": target.production, "per_page": PER_PAGE})
    found = rows if isinstance(rows, list) else []
    if len(found) >= PER_PAGE:
        raise AssembleError(f"{target.production} 宛ての開いている PR が {PER_PAGE} 件以上あり、リリースの PR の有無を確かめられない")
    heads = [(int(r["number"]), text_of(r["head"]["ref"])) for r in found if isinstance(r, dict)]
    return sorted(n for n, ref in heads if formats.is_release_branch(ref) or (ref == target.base and target.base != target.production))


def _write(
    root: Path,
    decl: decl_mod.NdfSettings,
    facts: materials_decide.Facts,
    store: store_mod.VersionStore,
    formats: names_decide.NameFormats,
    today: Callable[[], date],
) -> None:
    """保存先でバージョンを書き、見出しの書式で CHANGELOG を書き（日付は `today` で 1 度だけ読む）、設定の手順を流す。"""
    by_name = {u.name: u for u in store.units()}
    entries = [changelog_decide.Entry(p.number, p.title) for p in facts.pulls]
    log_path = root / decl.changelog
    stamp = today().isoformat()
    for p in facts.planned:
        store.write(by_name[p.plugin], p.next)
        if facts.channel == version_decide.PRODUCTION and log_path.is_file():
            new = changelog_decide.block(formats.heading_of(p.plugin, p.next, stamp), entries)
            key = formats.heading_key(p.plugin, p.next)
            log_path.write_text(changelog_decide.insert(log_path.read_text(encoding="utf-8"), key, new), encoding="utf-8")
    for step in decl.for_stage(facts.channel):
        decl_mod.run_step(root, step, facts.planned[0].next)


def _commit_and_push(repo_git: git_mod.Git, remote: str, branch: str, message: str) -> str:
    """1 つのコミットにしてリモートへ送る。書き換えたファイルが無ければ空のコミットにする（保存先 `tag` で CHANGELOG の無いとき）。"""
    repo_git.run("add", "--all")
    repo_git.run("commit", "--quiet", "--allow-empty", "-m", message)
    repo_git.run("push", "--quiet", "--", remote, f"HEAD:refs/heads/{branch}")
    return repo_git.rev_parse("HEAD")


def _open_pr(hub: GitHub, branch: str, base: str, title: str, body: str) -> tuple[int, str]:
    made = hub.rest("POST", f"/repos/{hub.repo}/pulls", {"title": title, "head": branch, "base": base, "body": ""})
    number = int(made["number"])
    Publisher(hub).edit_body(number, body)
    return number, str(made["html_url"])


def _since_by_unit(
    repo_git: git_mod.Git,
    target: Target,
    setting: _Setting,
    store: store_mod.VersionStore,
    found: Sequence[store_mod.Unit],
    fork: Optional[str],
) -> dict[str, str]:
    """単位ごとの範囲の起点（I4）。"""
    production_ref = target.ref(target.production)
    if setting.kind in (SpanKind.TRUNK, SpanKind.UNITS):
        gather.fetch_tags(repo_git, target.remote)
        return gather.prev_releases(repo_git, store, setting.formats, found, production_ref)
    since = since_decide.since_of(setting.kind, production_ref, None, fork)
    return {u.name: since for u in found}


def _plan(
    scope: gather.Scope, target: Target, setting: _Setting, change_list: Sequence[changes_decide.Change], decl: decl_mod.NdfSettings
) -> list[version_decide.Planned]:
    """バージョンを上げる単位の計画（出し済みのバージョンを繰り上げる）。上げるものが無ければ `NothingToRelease`。"""
    planned = gather.clear_shipped(
        gather.plan_bumps(scope, change_list, decl, target.level, target.channel),
        gather.shipped(scope.repo_git, target.remote, scope.store, setting.formats, scope.units, target.ref(target.production)),
        target.level,
        target.channel,
    )
    if not planned:
        sinces = sorted(set(scope.since_by_unit.values()))
        span = f"{sinces[0]}..{scope.until}" if len(sinces) == 1 else f"単位ごとの起点..{scope.until}"
        raise NothingToRelease(f"{span} にバージョンを上げる変化が無い")
    return planned


def _facts(
    root: Path, hub: GitHub, target: Target, setting: _Setting, until: str, fork: Optional[str]
) -> tuple[materials_decide.Facts, store_mod.VersionStore]:
    """作業場所（HEAD が範囲の終点）から承認資料の材料を読む。バージョンを持つ設定で上げるものが無ければ `NothingToRelease`。"""
    repo_git = git_mod.Git(root)
    decl = decl_mod.load(root)
    store = store_mod.load(root, setting.strategy, hub.repo)
    found = store.units()
    since_by_unit = _since_by_unit(repo_git, target, setting, store, found, fork)
    change_list = gather.changes(repo_git, since_by_unit, until, found, decl)
    scope = gather.Scope(repo_git, store, found, since_by_unit, until)
    planned = _plan(scope, target, setting, change_list, decl) if setting.versioned else []
    sinces = sorted({p.since for p in planned} or set(since_by_unit.values()))
    numbers = sorted({n for s in sinces for n in gather.included(repo_git, s, until, target.production, setting.formats)})
    paths = sorted({p for s in sinces for p in repo_git.changed_files(s, until)})
    facts = materials_decide.Facts(
        target.channel,
        tuple(planned),
        tuple(gather.pull_facts(hub, numbers)),
        tuple(change_list),
        tuple(changes_decide.instruction_changes(paths)),
        repo_git.rev_parse(until),
        setting.form,
        setting.versioned,
    )
    return facts, store


def collect(root: Path, hub: GitHub, target: Target, strategy: Strategy) -> materials_decide.Facts:
    """承認資料の材料（版上げの計画を含む。ブランチを残さない）。上げるものが無ければ `NothingToRelease`。

    `release_branch` は一時の作業場所で選んだスプリントを集めて読む。`merge_base_branch`・`trunk` は作業場所の HEAD が範囲の
    終点でなければ `AssembleError` で止まる（ほかのブランチのバージョンから計画するとバージョンが逆行する）。
    """
    setting = _setting(strategy)
    repo_git = git_mod.Git(root)
    if setting.kind == SpanKind.UNITS:
        selected = target.required_units()
        with (
            _units_errors(),
            units.scratch(repo_git, target.remote, target.production, selected, _provisional(setting.formats)) as made,
        ):
            return _facts(made.path, hub, target, setting, made.head, None)[0]
    until, fork = _span(repo_git, target, setting.kind, hub)
    if setting.kind != SpanKind.PR and repo_git.rev_parse("HEAD") != repo_git.rev_parse(until):
        raise AssembleError(f"worktreeの HEAD が {until} の HEAD でない（範囲の終点で打つ）")
    return _facts(root, hub, target, setting, until, fork)[0]


def _build_units(repo_git: git_mod.Git, target: Target, branch: str) -> str:
    with _units_errors():
        return units.build(repo_git, target.remote, target.production, target.units, branch)


def assemble(root: Path, hub: GitHub, target: Target, strategy: Strategy, *, today: Callable[[], date] = date.today) -> Built:
    """リリースブランチを組み立てて PR を作る。材料は範囲の終点を取り出してから読む。`today` は CHANGELOG の見出しの日付。"""
    setting = _setting(strategy)
    if setting.kind == SpanKind.PR or not setting.versioned:
        raise NoReleasePr(
            f"リリース方式 {strategy.release_strategy}（承認の対象 {strategy.section['approval']['target']}）はバージョンアップの PR を作らない"
        )
    repo_git = git_mod.Git(root)
    if setting.kind == SpanKind.UNITS:
        target.required_units()
        until, fork = _build_units(repo_git, target, _provisional(setting.formats)), None
    else:
        until, fork = _span(repo_git, target, setting.kind, hub)
        repo_git.run("switch", "--quiet", "--detach", until)
    facts, store = _facts(root, hub, target, setting, until, fork)
    if target.is_production:
        opened = open_releases(hub, target, setting.formats)
        if opened:
            listed = ", ".join(f"#{n}" for n in opened)
            raise ReleaseOpen(f"{target.production} 宛てのリリースの PR {listed} が開いている（閉じた後に打ち直す）")
    branch_unit = _select_branch_unit(facts.planned, target.plugin)
    branch = setting.formats.branch_of(branch_unit.next)
    if repo_git.attempt("ls-remote", "--exit-code", "--heads", "--", target.remote, branch).returncode == 0:
        raise AssembleError(f"{target.remote} に {branch} が既にある（前の組み立ての PR を先に片付ける）")
    if setting.kind == SpanKind.UNITS:
        facts = facts._replace(head=_build_units(repo_git, target, branch))
    repo_git.run("switch", "--quiet", "--create", branch, facts.head)
    _write(root, decl_mod.load(root), facts, store, setting.formats, today)
    title = f"Release: {branch_unit.plugin} v{branch_unit.next}"
    head = _commit_and_push(repo_git, target.remote, branch, title)
    facts = facts._replace(head=head)
    number, url = _open_pr(hub, branch, target.pr_base, title, materials_decide.render(facts))
    return Built(branch, branch_unit.next, number, url, facts)
