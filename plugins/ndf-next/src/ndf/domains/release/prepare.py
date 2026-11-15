"""前の版が出た直後に、次の版のリリースブランチを本番ブランチから用意する（本番のプランの最後のステップ。#3572 の決定 6）。

起点はマージ済みの本番の PR のマージコミット（本番ブランチのマージ後の先端）で、版は出した版の PATCH を 1 つ上げたもの
である。版の上げ幅がスプリントの中身で上がれば、組み立て（`assemble`）が本番の PR を作る前に改名する（決定 5）。
同じ名前のブランチが既にあれば作らない（GitHub が参照の作成を 422 で断る。打ち直しと別のレーンで重ならない）。
"""

from __future__ import annotations

from http import HTTPStatus
from typing import NamedTuple

from ndf.adapters.github import GitHub, GitHubError
from ndf.domains.release import release_plan, version_decide

# GitHub が作成を断る状態（同じ名前の参照が既にある。`land.UNPROCESSABLE` と同じ応答の意味）
UNPROCESSABLE = HTTPStatus.UNPROCESSABLE_ENTITY
# 用意するブランチの版の上げ幅（上げ幅が大きければ組み立てが改名する）
NEXT_LEVEL = "patch"


class PrepareError(Exception):
    """用意の前提が無い（PR がマージされていない・本番ブランチ宛てでない）。"""


class Prepared(NamedTuple):
    """用意したか既にあったリリースブランチ。"""

    branch: str
    version: str
    created: bool


def next_branch(released_ref: str) -> tuple[str, str]:
    """出した版のリリースブランチの名前から、次の版とそのブランチの名前（開発版の接尾辞は付けない）。"""
    released = version_decide.base_of(version_decide.version_of_branch(released_ref))
    version = version_decide.production_next(released, NEXT_LEVEL)
    return version, version_decide.branch_of(version)


def prepare_next(hub: GitHub, number: int, production: str) -> Prepared:
    """マージ済みの本番の PR `number` から、次の版のリリースブランチをマージコミットから 1 本だけ作る。"""
    pull = hub.pull(number)
    if not pull.merged or not pull.merge_sha:
        raise PrepareError(f"#{number} はまだマージされていない（本番のマージの後に打つ）")
    if not release_plan.is_production_pr(pull.base_ref, production):
        raise PrepareError(f"#{number} は本番ブランチ {production} 宛てでない（{pull.base_ref}）")
    version, branch = next_branch(pull.head_ref)
    try:
        hub.rest("POST", f"/repos/{hub.repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": pull.merge_sha})
    except GitHubError as exc:
        if exc.status != UNPROCESSABLE:
            raise
        return Prepared(branch, version, False)
    return Prepared(branch, version, True)
