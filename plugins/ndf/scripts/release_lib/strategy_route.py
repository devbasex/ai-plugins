"""strategy_route.py: 取り込み方の値から、版上げの PR の起点・宛先・同期の PR の要否を決める（#3672 の D17）。

取り込み方の名前で振る舞いを分ける分岐は、このモジュールの `route_of` の 1 か所に置く。

| 取り込み方 | 承認の対象 | 版上げの PR | 起点 → 宛先 | 同期の PR |
| --- | --- | --- | --- | --- |
| `merge_base_branch` | `release_pr` | 有り（版数の置き場が `none` なら base → production の PR そのもの。`bump` が偽） | base → production | 有り |
| `release_branch` | `release_pr` | 有り（選んだスプリントを集めたリリースブランチ） | production → production | 有り |
| `trunk` | `release_pr` | 有り（版数と CHANGELOG だけ） | production → production | 無し |
| `trunk` | `production_pr` | 無し（スプリントの PR が本番の PR を兼ねる） | — | 無し |
"""

from __future__ import annotations

from typing import NamedTuple


class Route(NamedTuple):
    """版上げの経路。`release_pr` が偽なら版上げの PR を作らない。`bump` が偽なら版数・CHANGELOG・タグ・Release を作らない。"""

    release_pr: bool
    bump: bool
    source: str | None
    target: str
    sync: bool
    collects_units: bool = False


def route_of(strategy) -> Route:
    """`lib/branch_strategy.Strategy` から版上げの経路を決める。"""
    sec = strategy.section
    rs = sec["release_strategy"]
    prod, base = sec["branches"]["production"], sec["branches"]["base"]
    versioned = sec["release"]["version_store"]["kind"] != "none"
    if sec["approval"]["target"] == "production_pr":
        return Route(False, False, None, prod, False)
    if rs == "merge_base_branch":
        return Route(True, versioned, base, prod, True)
    if rs == "release_branch":
        return Route(True, True, prod, prod, True, collects_units=True)
    return Route(True, True, prod, prod, False)
