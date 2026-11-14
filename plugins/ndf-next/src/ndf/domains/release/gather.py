"""承認資料と版上げの材料を、git と GitHub から集める。

- 変わったファイルと含む PR は `<前のリリース>..<ベースブランチ>` の範囲から読む（前のリリースは本番ブランチの先端）
- 含む PR の題と CI の結論は、GraphQL のエイリアスで 1 回のクエリにまとめて読む（PR ごとに 1 回ずつ呼ばない）。
  CI の結論は各 PR の最後のコミットの `statusCheckRollup` で、手元の検査の状態やレーンの記録を読まない（#2655）
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

from ndf.adapters import git as git_mod
from ndf.adapters.github import GitHub, text_of
from ndf.domains.release import changes_decide, checks_decide, included_decide, materials_decide, version_decide
from ndf.domains.release import plugins as plugins_mod
from ndf.domains.release.declaration import Declaration

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


def included(repo_git: git_mod.Git, span: Range) -> list[int]:
    """範囲のベースブランチの first-parent の件名から、含む PR の番号。"""
    subjects = repo_git.run("log", "--first-parent", "--format=%s", git_mod.END_OF_OPTIONS, f"{span.since}..{span.until}")
    return included_decide.numbers(subjects.splitlines())


def changes(repo_git: git_mod.Git, span: Range, found: Sequence[plugins_mod.Plugin], decl: Declaration) -> list[changes_decide.Change]:
    """プラグインごとの変化。"""
    return changes_in(repo_git.changed_files(span.since, span.until), found, decl)


def changes_in(paths: Sequence[str], found: Sequence[plugins_mod.Plugin], decl: Declaration) -> list[changes_decide.Change]:
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
