"""route.py: 取り込み方の値から版上げの経路（`Route`）を決める `route_of` の本体（#3672 の D17。表は `strategy_route.py`）。

GitHub の照会の部品に依存しないので、計画の組み立て（`supervise_lib/decl.trunk_release`）からも読める（#3703）。
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
