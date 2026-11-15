"""承認資料と版上げの材料を、git と GitHub から集める。

- 変わったファイルと含む PR は `<前のリリース>..<ベースブランチ>` の範囲から読む（前のリリースは本番ブランチの先端）
- 含む PR の題と CI の結論は、GraphQL のエイリアスで 1 回のクエリにまとめて読む（PR ごとに 1 回ずつ呼ばない）。
  CI の結論は各 PR の最後のコミットの `statusCheckRollup` で、手元の検査の状態やレーンの記録を読まない（#2655）
- 出し済みの版は、本番ブランチの先端のプラグインの定義の版と、origin のタグの版から読む（ベースブランチの定義の版を
  そのまま出す版にしない。#2554）
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from http import HTTPStatus
from pathlib import Path
from typing import Any, NamedTuple

from ndf.adapters import git as git_mod
from ndf.adapters.github import GitHub, GitHubError, text_of
from ndf.domains.release import changes_decide, checks_decide, included_decide, materials_decide, version_decide
from ndf.domains.release import plugins as plugins_mod
from ndf.domains.release.declaration import Declaration

# GET で対象が無いときの状態
NOT_FOUND = HTTPStatus.NOT_FOUND
# 含む PR から読む項目（題と、最後のコミットのチェックの結論）
PULL_SELECTION = (
    "number title commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) { nodes {"
    " __typename ... on CheckRun { name status conclusion } ... on StatusContext { context state } } } } } } }"
)


class Range(NamedTuple):
    """リリースの範囲。`since` は前のリリース（本番ブランチの先端）、`until` はベースブランチの先端。"""

    since: str
    until: str


def fetch(repo_git: git_mod.Git, name: str) -> None:
    """origin のブランチを取り込む。取り込めなければ `GitError`。"""
    done = repo_git.fetch_branch(git_mod.ORIGIN, name)
    if done.returncode != 0:
        raise git_mod.GitError(f"origin の {name} を取り込めない: {done.stderr.strip()}")


def resource_exists(hub: GitHub, path: str) -> bool:
    """GitHub の `path` を GET して、通れば真、404 なら偽（ほかの誤りは呼び出し元へ）。"""
    try:
        hub.rest("GET", path)
    except GitHubError as exc:
        if exc.status != NOT_FOUND:
            raise
        return False
    return True


def remote_has_branch(repo_git: git_mod.Git, branch: str) -> bool:
    """origin にブランチ `branch` があるか（`ls-remote --exit-code` の終了コード）。"""
    return repo_git.attempt("ls-remote", "--exit-code", "--heads", "--", git_mod.ORIGIN, branch).returncode == 0


def merge_or_abort(repo_git: git_mod.Git, merge_args: Sequence[str], before_abort: Callable[[], None] | None = None) -> bool:
    """`git merge <merge_args>` を打ち、失敗したら `before_abort`（衝突を読む関数）を呼んでから `merge --abort` して偽。"""
    if repo_git.attempt("merge", *merge_args).returncode == 0:
        return True
    if before_abort is not None:
        before_abort()
    repo_git.attempt("merge", "--abort")
    return False


def pull_ref(number: int) -> str:
    """PR の先端を取り込む手元の参照（`refs/remotes/origin/pull/<番号>`）。"""
    return f"refs/remotes/{git_mod.ORIGIN}/pull/{int(number)}"


def fetch_pull(repo_git: git_mod.Git, number: int) -> str:
    """origin の PR の先端（`refs/pull/<番号>/head`）を `pull_ref` へ取り込み、その参照を返す。取り込めなければ `GitError`。"""
    ref = pull_ref(number)
    done = repo_git.fetch(git_mod.ORIGIN, f"+refs/pull/{int(number)}/head:{ref}")
    if done.returncode != 0:
        raise git_mod.GitError(f"origin の PR #{number} の先端を取り込めない: {done.stderr.strip()}")
    return ref


def labels_of(hub: GitHub, number: int) -> list[str]:
    """PR のラベルの名前（上げ幅の宣言を読む）。"""
    rows = hub.rest("GET", f"/repos/{hub.repo}/issues/{int(number)}/labels")
    return [text_of(r["name"]) for r in rows if isinstance(r, dict) and "name" in r] if isinstance(rows, list) else []


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


def included(repo_git: git_mod.Git, span: Range, production: str) -> list[int]:
    """範囲のベースブランチの first-parent の件名から、含む PR の番号（本番ブランチ `production` から戻す PR を除く）。"""
    subjects = repo_git.run("log", "--first-parent", "--format=%s", git_mod.END_OF_OPTIONS, f"{span.since}..{span.until}")
    return included_decide.numbers(subjects.splitlines(), (production,))


def _versions_at(repo_git: git_mod.Git, plugin: plugins_mod.Plugin, ref: str) -> list[str]:
    """コミット `ref` のプラグインの定義（先頭）の版。定義が無ければ空。"""
    path = f"{plugin.dir}/{plugins_mod.MANIFESTS[0].as_posix()}"
    if not repo_git.run("ls-tree", "--name-only", git_mod.END_OF_OPTIONS, ref, path).strip():
        return []
    try:
        value = json.loads(repo_git.run("show", git_mod.END_OF_OPTIONS, f"{ref}:{path}")).get("version")
    except (json.JSONDecodeError, AttributeError) as exc:
        raise plugins_mod.PluginsError(f"{ref} の {path} を読めない: {exc}") from None
    return [value] if isinstance(value, str) else []


def version_at(repo_git: git_mod.Git, plugin: plugins_mod.Plugin, ref: str) -> str | None:
    """コミット `ref` のプラグインの定義（先頭）の版（定義が無ければ None）。"""
    found = _versions_at(repo_git, plugin, ref)
    return found[0] if found else None


def remote_tags(repo_git: git_mod.Git) -> list[str]:
    """origin のタグの名前（`refs/tags/` を外したもの。`ls-remote` の並び）。"""
    refs = repo_git.run("ls-remote", "--tags", "--refs", git_mod.ORIGIN).splitlines()
    return [line.partition("\t")[2].removeprefix("refs/tags/") for line in refs]


def shipped(repo_git: git_mod.Git, found: Sequence[plugins_mod.Plugin], production_ref: str) -> dict[str, str]:
    """プラグインごとの出し済みの版（本番ブランチの版とタグの版のうち基底が最も大きいもの。無いプラグインは入れない）。

    タグは origin から 1 回で読む（手元に取り込んだタグに限らない）。
    """
    tags = remote_tags(repo_git)
    out = {}
    for p in found:
        tagged = [v for v in (version_decide.version_of_tag(p.name, t) for t in tags) if v is not None]
        top = version_decide.highest([*_versions_at(repo_git, p, production_ref), *tagged])
        if top is not None:
            out[p.name] = top
    return out


def changes(paths: Sequence[str], found: Sequence[plugins_mod.Plugin], decl: Declaration) -> list[changes_decide.Change]:
    """範囲の変わったファイル（`paths`）のプラグインごとの変化。"""
    return changes_decide.changes(paths, {p.name: p.dir for p in found}, decl.undistributed)


def plan_bumps(
    root: Path,
    found: Sequence[plugins_mod.Plugin],
    change_list: Sequence[changes_decide.Change],
    decl: Declaration,
    level: str,
    channel: str,
) -> list[version_decide.Planned]:
    """版を上げるプラグインと次の版（マーケットプレイスの登録の順）。"""
    wanted = {c.plugin for c in change_list if changes_decide.needs_bump(c, decl.bump_undistributed)}
    out = []
    for p in found:
        if p.name in wanted:
            current = plugins_mod.version(root, p)
            out.append(version_decide.Planned(p.name, current, version_decide.next_version(current, level, channel)))
    return out


def clear_shipped(
    planned: Sequence[version_decide.Planned], released: Mapping[str, str], level: str, channel: str
) -> list[version_decide.Planned]:
    """次の版を、出し済みの版 `released`（プラグインの名前 → 版）より大きくなるよう繰り上げる（#2554）。"""
    return [p._replace(next=version_decide.clear_of(p.next, released.get(p.plugin), level, channel)) for p in planned]
