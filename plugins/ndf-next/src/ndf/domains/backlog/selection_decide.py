"""候補を上限で切る並びと持ち越し（純粋）。

上限で切るときは 利用者が渡した課題（`manual`）→ コミットの件名が指す課題（`commit-subject`）→ 前回の持ち越し
（`carried`）→ 経路の数の多い順 → 番号の昇順 で残す。`manual` の課題は上限を超えても残す（#2064）。見直し
（`review`）は `manual` の後を更新の古い順に並べる。切った分は持ち越しとして記録し、次の回が `carried` で拾う（#1640）。

見直しの起動の材料にする持ち越しの数（`carry`）は、見積の欠け（`unscored`）だけで入った課題を数えない。
見積の欠けは採点で解け、見直しを起動する理由にならない（#2540）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NamedTuple

from ndf.domains.backlog.model import REVIEW

MANUAL, COMMIT_SUBJECT, CARRIED, UNSCORED = "manual", "commit-subject", "carried", "unscored"
DIFF_PATH, NO_MILESTONE, ALL = "diff-path", "no-milestone", "all"
# 経路の並び（結果の routes の並びと、経路ごとの件数の並び）
ROUTES = (MANUAL, COMMIT_SUBJECT, CARRIED, DIFF_PATH, NO_MILESTONE, UNSCORED, ALL)


class Selection(NamedTuple):
    keep: list[int]
    deferred: list[int]
    # 見直しの起動の材料にする持ち越しの数（見積の欠けだけで入った課題を除く）
    carry: int
    # manual だけで上限を超えたときの件数（超えた分も残す）
    manual_over: int


def order(found: Mapping[int, set[str]], depth: str, updated: Mapping[int, str]) -> list[int]:
    """上限で切る前の並び。"""
    if depth == REVIEW:
        return sorted(found, key=lambda n: (MANUAL not in found[n], updated[n] if n in updated else "", n))
    return sorted(
        found,
        key=lambda n: (MANUAL not in found[n], COMMIT_SUBJECT not in found[n], CARRIED not in found[n], -len(found[n] - {UNSCORED}), n),
    )


def select(found: Mapping[int, set[str]], limit: int, depth: str, updated: Mapping[int, str]) -> Selection:
    """候補を上限で切る。`manual` は上限を超えても残す。"""
    ranked = order(found, depth, updated)
    manual = [n for n in ranked if MANUAL in found[n]]
    rest = [n for n in ranked if MANUAL not in found[n]]
    room = max(limit - len(manual), 0)
    keep = manual + rest[:room]
    deferred = rest[room:]
    carry = sum(1 for n in deferred if found[n] - {UNSCORED})
    return Selection(keep, deferred, carry, max(len(manual) - limit, 0))


def by_route(found: Mapping[int, set[str]], keep: list[int]) -> dict[str, int]:
    return {r: sum(1 for n in keep if r in found[n]) for r in ROUTES}
