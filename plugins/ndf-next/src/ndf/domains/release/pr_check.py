"""main 宛ての PR の検査（workflow が PR の出来事で打つ。#3572 の I7・I9）。

宣言の取り込み方の行が `github-flow` で、PR が本番ブランチ宛てのときだけ働き、ほかでは何も見ない（ほかの行ではリリースブランチの
PR がバージョンを上げるため。決定 7・16）。バージョンはバージョンの置き場（`version_store`。決定 22）の単位ごとに比べる。`problems` の判定:

| PR | 破れ |
| --- | --- |
| head が検証用ブランチ | `verify-ref`（ほかを見ない。検証用ブランチは main へ戻さない） |
| head が `release/v` で、差分が版のファイルと CHANGELOG だけ（版上げの PR） | 版数の破れを数えない |
| それ以外で、単位のバージョンが merge-base と PR の head で違う | `version`（版数は CD がマージの時点に上げる） |
| 上げ幅のラベルが 2 つ以上 | `level` |

あわせて PR 側の宣言のレビューの単位を `declaration.review_problems` で見る（#3119 の I2）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

from ndf.adapters import git as git_mod
from ndf.adapters.github import GitHub
from ndf.domains.release import bump, bump_decide, gather, version_decide, version_store
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release.version_store import VersionStore

# 版数の破れの理由
VERSION_DETAIL = "版数は CD がマージの時点に上げる（PR は版数を変えない）"
VERIFY_DETAIL = "検証用ブランチは main へ戻さない"


class PrFacts(NamedTuple):
    """PR の事実。`base_version`・`head_version` は単位の名前 → バージョン。"""

    head_ref: str
    base_version: Mapping[str, str | None]
    head_version: Mapping[str, str | None]
    changed: Sequence[str]
    labels: Sequence[str]
    verify_ref: str | None
    allowed: frozenset[str]


def problems(facts: PrFacts) -> list[dict[str, str]]:
    """PR の事実から、版数・上げ幅のラベル・検証用ブランチの破れを 1 件ずつ返す（モジュールの説明の表）。"""
    if facts.verify_ref is not None and facts.head_ref == facts.verify_ref:
        return [{"kind": "verify-ref", "name": facts.head_ref, "detail": VERIFY_DETAIL}]
    out = []
    is_bump = facts.head_ref.startswith(version_decide.BRANCH_PREFIX) and not bump_decide.only_bump(facts.changed, facts.allowed)
    if not is_bump:
        for path in sorted(set(facts.base_version) | set(facts.head_version)):
            before, after = facts.base_version.get(path), facts.head_version.get(path)
            if before != after:
                out.append({"kind": "version", "name": path, "detail": f"{VERSION_DETAIL}: {before} → {after}"})
    try:
        bump_decide.level_of(facts.labels)
    except bump_decide.LevelError as exc:
        out.append({"kind": "level", "name": ", ".join(x for x in facts.labels if x in bump_decide.LEVEL_LABELS), "detail": str(exc)})
    return out


def _versions(repo_git: git_mod.Git, store: VersionStore, ref: str) -> dict[str, str | None]:
    """単位ごとのバージョン（単位の名前を鍵にする）。"""
    return {u.name: store.read(repo_git, ref, u) for u in store.units()}


def check(root: Path, hub: GitHub, repo_git: git_mod.Git, number: int) -> list[dict[str, str]] | None:
    """宣言の行が `github-flow` で、PR が本番ブランチ宛てのときだけ PR の事実を集めて破れを返す（ほかは None で何も見ない）。"""
    flow = decl_mod.flow_of(root)
    if flow != decl_mod.GITHUB_FLOW:
        return None
    pull = hub.pull(number)
    if pull.base_ref != decl_mod.production_branch(root):
        return None
    gather.fetch(repo_git, pull.base_ref)
    head = gather.fetch_pull(repo_git, number)
    base = repo_git.merge_base(f"{git_mod.ORIGIN}/{pull.base_ref}", head)
    decl = decl_mod.load(root)
    store = version_store.of(root, decl, flow)
    facts = PrFacts(
        pull.head_ref,
        _versions(repo_git, store, base),
        _versions(repo_git, store, head),
        repo_git.changed_files(base, head),
        gather.labels_of(hub, number),
        decl_mod.optional_verify_ref(root),
        bump.allowed_paths(store, decl),
    )
    return problems(facts) + decl_mod.review_problems(root)
