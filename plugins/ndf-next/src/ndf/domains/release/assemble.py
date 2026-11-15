"""リリースのブランチを組み立て、リリースの PR を作る（GitHub Actions の workflow が打つ）。

手順（`assemble`）:

1. origin の本番ブランチとベースブランチを取り込み、範囲（本番ブランチの先端..ベースブランチの先端）を決める
2. 変わったファイルから版を上げるプラグインと次の版を決める。上げるものが無ければ何もせずに `not_applicable`
3. ベースブランチの先端から `release/v<版>` を作り、版を書き換え、本番の版なら CHANGELOG に節を足し、宣言の手順
   （トークン消費の記録など）を流す
4. 1 つのコミットにして push し、リリースの PR（本番は本番ブランチ宛て、開発版はベースブランチ宛て）を作り、承認資料を
   PR の本文に書く

ブランチの名前の版は、版を上げるプラグインのうち `--plugin` で渡したもの（省けば登録の順の先頭）の版である。

本番のリリースは、本番ブランチ宛ての別のリリースの PR（head が `release/v` で始まるかベースブランチ）が開いている
間は組まずに `ReleaseOpen` で止まる（同じリポジトリの別の組み立てと版数・ブランチがぶつからない。閉じた後に打ち直せば
組む。#2441）。

本番のリリースで、組むリリースブランチが origin に既にあれば作り直さずに進める（#3572 の決定 6・7。`_existing`）。

| 既にあるブランチ | 進め方 |
| --- | --- |
| 先端が組む起点の祖先（前の版の直後に用意したブランチなど） | fast-forward で進める |
| 開いた本番の PR の head（差し戻しの後の組み直し） | `--source base` は組む起点を取り込みのマージで入れる。PR を作らずに本文の承認資料を新しい先端で書き直す |
| 祖先でなく、開いた本番の PR も無い | `AssembleError` で止まる |
| 前の版の直後に用意した名前（PATCH）で、計画した版と違う | 本番の PR が無ければ計画した版の名前へ改名する。あれば改名せずに `AssembleError` |

`--source release`（常設のリリースブランチ）は、範囲の先端を origin のリリースブランチ（出し済みの版より大きい最小の版の
`release/v<版>`）にし、版は出し済みの版から計画する（承認の前に積んだ版上げのコミットを二重に上げない）。
push は先端を進めるだけで、強制 push を使わない。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ndf.adapters import git as git_mod
from ndf.adapters.github import PER_PAGE, GitHub, text_of
from ndf.adapters.publish import Publisher
from ndf.domains.release import changelog_decide, changes_decide, gather, materials_decide, version_decide
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod


class NothingToRelease(Exception):
    """版を上げるプラグインが無い。"""


class ReleaseOpen(Exception):
    """本番ブランチ宛ての別のリリースの PR が開いている（閉じるまで組まない）。"""


class AssembleError(Exception):
    """組み立ての前提が無い（ブランチが既にある・名前のプラグインが上げる対象に無い）。"""


# 範囲の先端の出所（ベースブランチ・常設のリリースブランチ）
BASE_SOURCE, RELEASE_SOURCE = "base", "release"
SOURCES = (BASE_SOURCE, RELEASE_SOURCE)


@dataclass(frozen=True)
class Target:
    """組み立ての指定。"""

    channel: str
    level: str
    base: str
    production: str
    plugin: str | None = None
    source: str = BASE_SOURCE

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
    # 開いた本番の PR を組み直したか（PR を作っていない）・改名した前の名前
    reassembled: bool = False
    renamed_from: str | None = None


def _remote_releases(repo_git: git_mod.Git) -> list[tuple[str, str]]:
    """origin のリリースブランチ（開発版を除く）の (版, 名前)。"""
    out = repo_git.run("ls-remote", "--heads", "--", git_mod.ORIGIN, f"refs/heads/{version_decide.BRANCH_PREFIX}*")
    names = [line.partition("\t")[2].removeprefix("refs/heads/") for line in out.splitlines()]
    found = [(_version_or_none(n), n) for n in names]
    return [(v, n) for v, n in found if v is not None and not version_decide.is_dev(v)]


def _version_or_none(branch: str) -> str | None:
    try:
        return version_decide.version_of_branch(branch)
    except version_decide.VersionError:  # noqa: NDF401 外部コマンド: 版として読めない名前はリリースブランチに数えない
        return None


def _shipped_of(root: Path, repo_git: git_mod.Git, target: Target) -> str | None:
    """名前に版を使うプラグインの出し済みの版（無ければ None）。"""
    found = plugins_mod.load(root)
    name = target.plugin if target.plugin is not None else found[0].name if found else None
    shipped = gather.shipped(repo_git, found, f"{git_mod.ORIGIN}/{target.production}")
    return shipped[name] if name in shipped else None


def _standing(repo_git: git_mod.Git, shipped: str | None) -> str | None:
    """出し済みの版より大きい最小の版の origin のリリースブランチ（前の版の直後に用意したもの。無ければ None）。"""
    floor = version_decide.parse(version_decide.base_of(shipped)) if shipped is not None else None
    found = [(version_decide.parse(v), n) for v, n in _remote_releases(repo_git)]
    later = sorted((v, n) for v, n in found if floor is None or v > floor)
    return later[0][1] if later else None


def _prepared(root: Path, repo_git: git_mod.Git, target: Target) -> str | None:
    """前の版の直後に用意した常設のリリースブランチ（出し済みの版より大きい最小の版。無ければ None）。"""
    return _standing(repo_git, _shipped_of(root, repo_git, target))


def _span(root: Path, repo_git: git_mod.Git, target: Target) -> gather.Range:
    """範囲（本番ブランチの先端..範囲の先端）。`release` なら範囲の先端は常設のリリースブランチ。"""
    for name in (target.base, target.production):
        gather.fetch(repo_git, name)
    since = f"{git_mod.ORIGIN}/{target.production}"
    if target.source != RELEASE_SOURCE:
        return gather.Range(since, f"{git_mod.ORIGIN}/{target.base}")
    branch = _prepared(root, repo_git, target)
    if branch is None:
        raise AssembleError("origin に常設のリリースブランチが無い（前の版の後の release-steps prepare-next を先に打つ）")
    gather.fetch(repo_git, branch)
    return gather.Range(since, f"{git_mod.ORIGIN}/{branch}")


def _named(planned: Sequence[version_decide.Planned], plugin: str | None) -> version_decide.Planned:
    if plugin is None:
        return planned[0]
    hit = [p for p in planned if p.plugin == plugin]
    if not hit:
        raise AssembleError(f"{plugin} は版を上げる対象に無い（上げるもの: {', '.join(p.plugin for p in planned)}）")
    return hit[0]


def open_release_heads(hub: GitHub, target: Target) -> dict[int, str]:
    """本番ブランチ宛ての開いているリリースの PR（head が `release/v` で始まるかベースブランチ）の番号 → head のブランチ。

    1 ページ（`PER_PAGE` 件）で読み切れなければ、確かめられないとして `AssembleError`。
    """
    rows = hub.rest("GET", f"/repos/{hub.repo}/pulls", params={"state": "open", "base": target.production, "per_page": PER_PAGE})
    found = rows if isinstance(rows, list) else []
    if len(found) >= PER_PAGE:
        raise AssembleError(f"{target.production} 宛ての開いている PR が {PER_PAGE} 件以上あり、リリースの PR の有無を確かめられない")
    heads = [(int(r["number"]), text_of(r["head"]["ref"])) for r in found if isinstance(r, dict)]
    return {n: ref for n, ref in sorted(heads) if ref.startswith(version_decide.BRANCH_PREFIX) or ref == target.base}


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
    """変更をコミットして push する（変更が無ければコミットを足さない）。push は先端を進めるだけで強制しない。"""
    repo_git.run("add", "--all")
    if repo_git.run("status", "--porcelain").strip():
        repo_git.run("commit", "--quiet", "-m", message)
    repo_git.run("push", "--quiet", "--", git_mod.ORIGIN, f"HEAD:refs/heads/{branch}")
    return repo_git.rev_parse("HEAD")


def _open_pr(hub: GitHub, branch: str, base: str, title: str, body: str) -> tuple[int, str]:
    made = hub.rest("POST", f"/repos/{hub.repo}/pulls", {"title": title, "head": branch, "base": base, "body": ""})
    number = int(made["number"])
    Publisher(hub).edit_body(number, body)
    return number, str(made["html_url"])


def _facts(root: Path, hub: GitHub, target: Target, span: gather.Range) -> materials_decide.Facts:
    """worktree（範囲の先端を取り出したもの）から材料を読む。上げるものが無ければ `NothingToRelease`。"""
    repo_git = git_mod.Git(root)
    decl = decl_mod.load(root)
    found = plugins_mod.load(root)
    paths = repo_git.changed_files(span.since, span.until)
    change_list = gather.changes(paths, found, decl)
    planned = gather.clear_shipped(
        gather.plan_bumps(root, found, change_list, decl, target.level, target.channel),
        gather.shipped(repo_git, found, span.since),
        target.level,
        target.channel,
    )
    if not planned:
        raise NothingToRelease(f"{span.since}..{span.until} に版を上げる変化が無い")
    if target.source == RELEASE_SOURCE:
        planned = _from_shipped(planned, gather.shipped(repo_git, found, span.since), target)
    pulls = gather.pull_facts(hub, gather.included(repo_git, span, target.production))
    return materials_decide.Facts(
        target.channel,
        tuple(planned),
        tuple(pulls),
        tuple(change_list),
        tuple(changes_decide.instruction_changes(paths)),
        repo_git.rev_parse(span.until),
    )


def _from_shipped(planned: Sequence[version_decide.Planned], shipped: dict[str, str], target: Target) -> list[version_decide.Planned]:
    """常設のリリースブランチの計画を、出し済みの版から上げた版にする（承認の前に積んだ版上げを二重に上げない）。"""
    out = []
    for p in planned:
        current = shipped[p.plugin] if p.plugin in shipped else p.current
        out.append(p._replace(current=current, next=version_decide.next_version(current, target.level, target.channel)))
    return out


def collect(root: Path, hub: GitHub, target: Target) -> materials_decide.Facts:
    """承認資料の材料（版上げの計画を含む。ブランチは作らない）。上げるものが無ければ `NothingToRelease`。

    宣言・マーケットプレイス・今の版はworktreeから読むため、worktreeの HEAD が範囲の先端（origin のベースブランチ）で
    なければ `AssembleError` で止まる（ほかのブランチの版から計画すると版が逆行する）。
    """
    repo_git = git_mod.Git(root)
    span = _span(root, repo_git, target)
    if repo_git.rev_parse("HEAD") != repo_git.rev_parse(span.until):
        raise AssembleError(f"worktreeの HEAD が {span.until} の先端でない（範囲の先端で打つ）")
    return _facts(root, hub, target, span)


def _on_origin(repo_git: git_mod.Git, branch: str) -> bool:
    return repo_git.attempt("ls-remote", "--exit-code", "--heads", "--", git_mod.ORIGIN, branch).returncode == 0


def _existing(root: Path, repo_git: git_mod.Git, hub: GitHub, target: Target, branch: str) -> tuple[str | None, int | None]:
    """組むべき既にあるリリースブランチ（計画した版の名前か、前の版の直後に用意した名前）と、その開いた本番の PR の番号。

    別のブランチの開いた本番の PR があれば `ReleaseOpen`。用意した名前の PR があり計画した版と違えば `AssembleError`。
    """
    prepared = _prepared(root, repo_git, target)
    found = branch if _on_origin(repo_git, branch) else prepared
    heads = open_release_heads(hub, target)
    others = sorted(n for n, ref in heads.items() if ref != found)
    if others:
        listed = ", ".join(f"#{n}" for n in others)
        raise ReleaseOpen(f"{target.production} 宛てのリリースの PR {listed} が開いている（閉じた後に打ち直す）")
    number = next((n for n, ref in heads.items() if ref == found), None)
    if found is not None and found != branch and number is not None:
        raise AssembleError(
            f"本番の PR #{number} の {found} と計画した版の {branch} が違う（release-steps defer で PR を閉じてから打ち直す）"
        )
    return found, number


def _advance(repo_git: git_mod.Git, branch: str, until: str, source: str, reopen: bool) -> None:
    """既にあるリリースブランチを、先端を戻さずに組む起点まで進めた状態で取り出す。

    `base`: origin の先端が起点の祖先なら起点を取り出す（fast-forward）。祖先でなく開いた本番の PR の組み直しなら、
    起点を取り込みのマージで入れる（衝突は起点の側を取り、版と CHANGELOG は後で書き直す）。どちらでもなければ `AssembleError`。
    `release`: origin の先端（範囲の先端）をそのまま取り出す。
    """
    gather.fetch(repo_git, branch)
    remote = f"{git_mod.ORIGIN}/{branch}"
    if source == RELEASE_SOURCE or repo_git.is_ancestor(remote, until):
        start = remote if source == RELEASE_SOURCE else until
        repo_git.run("switch", "--quiet", "-C", branch, start)
        return
    if not reopen:
        tips = f"{repo_git.rev_parse(remote)[:12]} と {repo_git.rev_parse(until)[:12]}"
        raise AssembleError(f"origin の {branch} の先端が組む起点の祖先でなく、開いた本番の PR も無い（{tips}）")
    repo_git.run("switch", "--quiet", "-C", branch, remote)
    if repo_git.attempt("merge", "--quiet", "--no-edit", "-X", "theirs", until).returncode != 0:
        repo_git.attempt("merge", "--abort")
        raise git_mod.GitError(f"{until} を {branch} へ取り込めない（衝突。直しを入れてから打ち直す）")


def _rename(hub: GitHub, old: str, new: str) -> None:
    """用意したリリースブランチを計画した版の名前へ改名する（本番の PR を作る前だけ。決定 5）。"""
    hub.rest("POST", f"/repos/{hub.repo}/branches/{old}/rename", {"new_name": new})


def _reassemble(hub: GitHub, number: int, facts: materials_decide.Facts) -> None:
    """開いた本番の PR の本文の承認資料を、新しい先端で書き直す（決定 7）。"""
    Publisher(hub).edit_body(number, materials_decide.render(facts))


def _checkout(root: Path, repo_git: git_mod.Git, hub: GitHub, target: Target, branch: str, until: str) -> tuple[int | None, str | None]:
    """組むリリースブランチを取り出す。(開いた本番の PR の番号, 改名した前の名前)。開発版は今のまま新しく作る。"""
    found, number = _existing(root, repo_git, hub, target, branch) if target.is_production else (None, None)
    if found is None:
        if _on_origin(repo_git, branch):
            raise AssembleError(f"origin に {branch} が既にある（前の組み立ての PR を先に片付ける）")
        repo_git.run("switch", "--quiet", "--create", branch, until)
        return None, None
    renamed = None
    if found != branch:
        _rename(hub, found, branch)
        renamed = found
    _advance(repo_git, branch, until, target.source, number is not None)
    return number, renamed


def assemble(root: Path, hub: GitHub, target: Target) -> Built:
    """リリースのブランチを組み立てて PR を作る（開いた本番の PR があれば組み直す）。材料は範囲の先端を取り出してから読む。"""
    repo_git = git_mod.Git(root)
    span = _span(root, repo_git, target)
    repo_git.run("switch", "--quiet", "--detach", span.until)
    facts = _facts(root, hub, target, span)
    named = _named(facts.planned, target.plugin)
    branch = version_decide.branch_of(named.next)
    number, renamed = _checkout(root, repo_git, hub, target, branch, facts.head)
    _write(root, decl_mod.load(root), facts)
    title = f"Release: {named.plugin} v{named.next}"
    facts = facts._replace(head=_commit_and_push(repo_git, branch, title))
    if number is not None:
        _reassemble(hub, number, facts)
        url = f"https://github.com/{hub.repo}/pull/{number}"
        return Built(branch, named.next, number, url, facts, reassembled=True, renamed_from=renamed)
    number, url = _open_pr(hub, branch, target.pr_base, title, materials_decide.render(facts))
    return Built(branch, named.next, number, url, facts, renamed_from=renamed)
