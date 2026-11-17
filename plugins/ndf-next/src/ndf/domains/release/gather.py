"""承認資料と版上げの材料を、git と GitHub から集める。

- 範囲の起点と終点はリリース方式で決まる（`since_decide`）。`trunk` の `release_pr` と `release_branch` では、バージョンの単位ごとの
  前のリリースの起点（タグ・今のバージョンが保存先に入ったコミット・本番ブランチの最初のコミット）から集める（#3615 の決定 6）
- バージョンの読み書きは保存先のアダプター（`version_store`）のインタフェースだけを呼び、保存先の種類で分岐しない（I2）
- リモート名は引数で受け、名前を書かない（I8）
- 含む PR の題と CI の結論は、GraphQL のエイリアスで 1 回のクエリにまとめて読む（PR ごとに 1 回ずつ呼ばない）。
  CI の結論は各 PR の最後のコミットの `statusCheckRollup` で、手元の検査の状態やレーンの記録を読まない（#2655）
- 出し済みのバージョンは、本番ブランチの HEAD の保存先のバージョンと、リモートのタグのバージョンから読む（ベースブランチの
  バージョンをそのまま出すバージョンにしない。#2554）
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple, Optional

from ndf.adapters import git as git_mod
from ndf.adapters.github import GitHub, text_of
from ndf.domains.release import changes_decide, checks_decide, included_decide, materials_decide, names_decide, since_decide, version_decide
from ndf.domains.release import version_store as store_mod
from ndf.domains.release.ndf_settings import NdfSettings

# 含む PR から読む項目（題と、最後のコミットのチェックの結論）
PULL_SELECTION = (
    "number title commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) { nodes {"
    " __typename ... on CheckRun { name status conclusion } ... on StatusContext { context state } } } } } } }"
)
# first-parent の各コミットを読む形（SHA・親・件名を NUL で区切る）
_LOG_FORMAT = "--format=%H%x00%P%x00%s"


class Scope(NamedTuple):
    """バージョンアップの計画を読む範囲: 保存先と単位と、単位ごとの範囲の起点（`since_by_unit`）と終点（`until`）。"""

    repo_git: git_mod.Git
    store: store_mod.VersionStore
    units: Sequence[store_mod.Unit]
    since_by_unit: Mapping[str, str]
    until: str


def fetch(repo_git: git_mod.Git, remote: str, name: str) -> None:
    """リモートのブランチを `refs/remotes/<remote>/<name>` へ取得する。取得できなければ `GitError`。"""
    repo_git.fetch_branch_or_raise(remote, name)


def fetch_tags(repo_git: git_mod.Git, remote: str) -> None:
    """リモートのタグを手元へ取得する（強制しない。手元の同じ名前の別のタグとぶつかれば `GitError`）。"""
    done = repo_git.attempt("fetch", "--quiet", "--tags", "--", remote)
    if done.returncode != 0:
        raise git_mod.GitError(f"{remote} のタグを取得できない（手元の同じ名前のタグを消すか合わせる）: {done.stderr.strip()}")


def _context(node: dict) -> tuple[str, str]:
    """チェックの 1 件を (名前, 結論) にする。check run が終わっていなければ結論は空。

    commit status の状態（`success`・`failure`・`error`・`pending`・`expected`）は、小文字にすればそのまま
    事実の表 `ci-outcomes` の結論の id として読める（待ちの状態も表が `pending` に区分する）。
    """
    if node["__typename"] == "CheckRun":
        done = text_of(node["status"]).lower() == "completed"
        return text_of(node["name"]), text_of(node["conclusion"]).lower() if done else ""
    return text_of(node["context"]), text_of(node["state"]).lower()


def _contexts(raw: dict) -> list[tuple[str, str]]:
    commits = raw["commits"]["nodes"] if isinstance(raw.get("commits"), dict) else []
    rollup = commits[-1]["commit"]["statusCheckRollup"] if commits else None
    if not isinstance(rollup, dict):
        return []
    return [_context(n) for n in rollup["contexts"]["nodes"] if isinstance(n, dict) and "__typename" in n]


def pull_facts(hub: GitHub, numbers: Sequence[int]) -> list[materials_decide.PullFact]:
    """含む PR の題と CI の件数（1 回のクエリ。PR でない番号は落とす）。"""
    if not numbers:
        return []
    got: dict[int, Any] = hub.nodes(numbers, "pullRequest", PULL_SELECTION)
    return [
        materials_decide.PullFact(n, text_of(raw["title"]), checks_decide.tally(_contexts(raw))) for n, raw in sorted(got.items()) if raw
    ]


def included(repo_git: git_mod.Git, since: str, until: str, production: str, formats: names_decide.NameFormats) -> list[int]:
    """範囲の first-parent の件名の PR と、リリースの単位のマージ（第 1 親..第 2 親）の中の PR の番号（本番ブランチから戻す PR と
    リリースブランチからのマージを除く。#3615 の決定 8）。"""
    rows = [line.split("\0") for line in repo_git.log_of(f"{since}..{until}", "--first-parent", _LOG_FORMAT).splitlines() if line]
    subjects = [r[2] for r in rows if len(r) == 3]
    for sha, parents, subject in (r for r in rows if len(r) == 3):
        pair = parents.split()
        if len(pair) == 2 and included_decide.is_unit_merge(subject, formats.is_release_branch):
            subjects += repo_git.log_of(f"{pair[0]}..{pair[1]}", "--first-parent", "--format=%s").splitlines()
    return included_decide.numbers(subjects, (production,), formats.is_release_branch)


def introduced(repo_git: git_mod.Git, store: store_mod.VersionStore, unit: store_mod.Unit, production_ref: str) -> Optional[str]:
    """本番ブランチの履歴で、今のバージョンが保存先に入ったコミット（10 系の `untagged_since`）。保存先がファイルを持たない・
    今のバージョンが無い・履歴が読めない（浅い取得）なら None。"""
    paths = sorted(store.paths())
    current = store.read(repo_git, production_ref, unit) if paths else None
    if current is None:
        return None
    try:
        commits = repo_git.run("log", "--format=%H", git_mod.END_OF_OPTIONS, production_ref, "--", *paths).split()
        last = None
        for sha in commits:
            if store.read(repo_git, sha, unit) != current:
                break
            last = sha
    except (git_mod.GitError, store_mod.StoreError):  # noqa: NDF401 外部コマンド: 浅い取得で git log が読めなければ次の候補（最初のコミット）へ進む
        return None
    return last


def root_commit(repo_git: git_mod.Git, production_ref: str) -> str:
    """本番ブランチの最初のコミット（根が複数あれば最も古いもの）。"""
    roots = repo_git.run("rev-list", "--max-parents=0", git_mod.END_OF_OPTIONS, production_ref).split()
    if not roots:
        raise git_mod.GitError(f"{production_ref} の最初のコミットが無い")
    return roots[-1]


def prev_releases(
    repo_git: git_mod.Git,
    store: store_mod.VersionStore,
    formats: names_decide.NameFormats,
    units: Sequence[store_mod.Unit],
    production_ref: str,
) -> dict[str, str]:
    """単位ごとの前のリリースの起点（タグ・バージョンの変わり目・最初のコミットの順。#3615 の決定 6）。タグは取得済みであること。"""
    tags = store_mod.tags_at(repo_git, production_ref)
    first = root_commit(repo_git, production_ref)
    out = {}
    for unit in units:
        top = names_decide.highest_tag(formats, unit.name, tags)
        tagged = repo_git.rev_parse(top[0]) if top is not None else None
        found = introduced(repo_git, store, unit, production_ref) if tagged is None else None
        out[unit.name] = since_decide.prev_release(tagged, found, first)
    return out


def shipped(
    repo_git: git_mod.Git,
    remote: str,
    store: store_mod.VersionStore,
    formats: names_decide.NameFormats,
    units: Sequence[store_mod.Unit],
    production_ref: str,
) -> dict[str, str]:
    """単位ごとの出し済みのバージョン（本番ブランチの保存先のバージョンとリモートのタグのバージョンのうち基底が最も大きいもの。
    無い単位は入れない）。タグはリモートから 1 回で読む（手元に取得したタグに限らない）。"""
    refs = repo_git.run("ls-remote", "--tags", "--refs", "--", remote).splitlines()
    tags = [line.partition("\t")[2].removeprefix("refs/tags/") for line in refs]
    out = {}
    for unit in units:
        tagged = [v for v in (formats.version_of_tag(unit.name, t) for t in tags) if v is not None]
        at_production = store.read(repo_git, production_ref, unit)
        top = version_decide.highest([*([at_production] if at_production else []), *tagged])
        if top is not None:
            out[unit.name] = top
    return out


def changes(
    repo_git: git_mod.Git, since_by_unit: Mapping[str, str], until: str, units: Sequence[store_mod.Unit], decl: NdfSettings
) -> list[changes_decide.Change]:
    """単位ごとに、その単位の範囲の起点から `until` までの変化（同じ起点の範囲は 1 回だけ読む）。"""
    read: dict[str, list[str]] = {}
    for since in set(since_by_unit.values()):
        read[since] = repo_git.changed_files(since, until)
    paths_by_unit = {u.name: read[since_by_unit[u.name]] for u in units}
    return changes_decide.changes(paths_by_unit, {u.name: u.dir for u in units}, decl.undistributed)


def plan_bumps(
    scope: Scope, change_list: Sequence[changes_decide.Change], decl: NdfSettings, level: str, channel: str
) -> list[version_decide.Planned]:
    """バージョンを上げる単位と次のバージョンと範囲の起点（単位の順）。範囲の終点の保存先にバージョンが無い単位は `INITIAL` から上げる。"""
    wanted = {c.plugin for c in change_list if changes_decide.needs_bump(c, decl.bump_undistributed)}
    out = []
    for unit in scope.units:
        if unit.name in wanted:
            current = scope.store.read(scope.repo_git, scope.until, unit) or store_mod.INITIAL
            since = scope.repo_git.rev_parse(scope.since_by_unit[unit.name])
            out.append(version_decide.Planned(unit.name, current, version_decide.next_version(current, level, channel), since))
    return out


def clear_shipped(
    planned: Sequence[version_decide.Planned], released: Mapping[str, str], level: str, channel: str
) -> list[version_decide.Planned]:
    """次のバージョンを、出し済みのバージョン `released`（単位の名前 → バージョン）より大きくなるよう繰り上げる（#2554）。"""
    return [p._replace(next=version_decide.clear_of(p.next, released.get(p.plugin), level, channel)) for p in planned]
