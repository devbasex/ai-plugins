"""ブランチ戦略の宣言から、版上げの PR の起点・宛先・同期の PR の要否と、マージの主体と方式を決める（純粋な関数。#3672・#3681）。

取り込み方（`release_strategy`）の名前で振る舞いを分ける分岐は、このモジュールの `route_of` の 1 か所に置く。
10 系の `release_lib/strategy_route.py` の `route_of` と同じ表で、5 種の宣言から同じ値を返す。

| 取り込み方 | 承認の対象 | 版上げの PR | 起点 → 宛先 | 同期の PR |
| --- | --- | --- | --- | --- |
| `merge_base_branch` | `release_pr` | 有り（版数の置き場が `none` なら base → production の PR そのもの。`bump` が偽） | base → production | 有り |
| `release_branch` | `release_pr` | 有り（選んだスプリントを集めたリリースブランチ） | production → production | 有り |
| `trunk` | `release_pr` | 有り（版数と CHANGELOG だけ） | production → production | 無し |
| `trunk` | `production_pr` | 無し（スプリントの PR が本番の PR を兼ねる） | — | 無し |
"""

from __future__ import annotations

from typing import NamedTuple

from ndf.core.branch_strategy import Strategy


class Route(NamedTuple):
    """版上げの経路。`release_pr` が偽なら版上げの PR を作らない。`bump` が偽なら版数・CHANGELOG・タグ・Release を作らない。"""

    release_pr: bool
    bump: bool
    source: str | None
    target: str
    sync: bool
    collects_units: bool = False


class Landing(NamedTuple):
    """本番の PR のマージの主体（`ndf`・`human`）と方式（`direct`・`strict`・`merge-queue`）と履歴の作り方・管理者の迂回。"""

    actor: str
    method: str
    commit: str
    admin: bool


def route_of(strategy: Strategy) -> Route:
    """宣言から版上げの経路を決める。"""
    sec = strategy.section
    rs = sec["release_strategy"]
    prod, base = strategy.production, strategy.base
    versioned = sec["release"]["version_store"]["kind"] != "none"
    if sec["approval"]["target"] == "production_pr":
        return Route(False, False, None, prod, False)
    if rs == "merge_base_branch":
        return Route(True, versioned, base, prod, True)
    if rs == "release_branch":
        return Route(True, True, prod, prod, True, collects_units=True)
    return Route(True, True, prod, prod, False)


def landing_of(strategy: Strategy) -> Landing:
    """本番の PR のマージの主体と方式。迂回は `method: direct` で `admin: true` のときだけ（#3672 の I6）。"""
    merge = strategy.section["merge"]
    return Landing(merge["actor"], merge["method"], merge["commit"], merge["admin"] is True and merge["method"] == "direct")
