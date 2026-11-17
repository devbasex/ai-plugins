"""承認資料とリリースブランチに集める差分の範囲の起点と終点を、リリース方式から決める（純粋な関数。#3615 の決定 6）。

範囲の取り方（`SpanKind`）は `route_decide.route_of` の経路から 1 つに決め、リリース方式の名前で分岐しない。

| 取り方 | 経路 | 範囲の起点 | 範囲の終点 |
| --- | --- | --- | --- |
| `base` | 版上げの PR の起点がベースブランチ（`merge_base_branch`） | 本番ブランチの HEAD | ベースブランチの HEAD |
| `trunk` | 版上げの PR の起点が本番ブランチ（`trunk` の `release_pr`） | 単位ごとの前のリリースの起点 | 本番ブランチの HEAD |
| `units` | 選んだスプリントを集める（`release_branch`） | 単位ごとの前のリリースの起点 | 選んだスプリントを集めた先頭 |
| `pr` | 版上げの PR を作らない（`production_pr`） | 本番ブランチと PR の分岐点 | PR の先頭 |

前のリリースの起点は、タグ・今のバージョンが保存先に入ったコミット・本番ブランチの最初のコミットの順に選ぶ（10 系の
`release_prev.prev_release` と同じ順）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Optional

from ndf.core.branch_strategy import Strategy
from ndf.domains.release import route_decide


class SpanKind(StrEnum):
    """範囲の取り方。"""

    BASE = "base"
    TRUNK = "trunk"
    UNITS = "units"
    PR = "pr"


def kind_of(strategy: Strategy) -> SpanKind:
    """設定から範囲の取り方を 1 つ決める。"""
    route = route_decide.route_of(strategy)
    if not route.release_pr:
        return SpanKind.PR
    if route.collects_units:
        return SpanKind.UNITS
    if route.source == strategy.base and strategy.base != strategy.production:
        return SpanKind.BASE
    return SpanKind.TRUNK


def prev_release(tagged: Optional[str], introduced: Optional[str], root_commit: str) -> str:
    """前のリリースの起点（タグ・今のバージョンが入ったコミット・最初のコミットのうち、最初に None でないもの）。"""
    for candidate in (tagged, introduced):
        if candidate is not None:
            return candidate
    return root_commit


def since_of(kind: SpanKind, production_head: str, prev: Optional[str], fork_point: Optional[str]) -> str:
    """範囲の起点。`trunk`・`units` では `prev`、`pr` では `fork_point` が要る（無ければ ValueError）。"""
    if kind == SpanKind.BASE:
        return production_head
    got = fork_point if kind == SpanKind.PR else prev
    if got is None:
        raise ValueError(f"範囲の取り方 {kind} の起点が無い")
    return got


def until_of(kind: SpanKind, base_head: str, production_head: str, units_head: Optional[str], pr_head: Optional[str]) -> str:
    """範囲の終点。`units` では `units_head`、`pr` では `pr_head` が要る（無ければ ValueError）。"""
    if kind == SpanKind.BASE:
        return base_head
    if kind == SpanKind.TRUNK:
        return production_head
    got = units_head if kind == SpanKind.UNITS else pr_head
    if got is None:
        raise ValueError(f"範囲の取り方 {kind} の終点が無い")
    return got
