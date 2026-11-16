"""リリースの後に、メインディレクトリのベースブランチを origin へそろえる手を決める（純粋な関数）。

リリース後テストの前に、メインディレクトリのベースブランチをマージの後の origin へ進める。

| メインディレクトリの状態 | 手 |
| --- | --- |
| ベースブランチ以外（本番ブランチ・detached HEAD）にいる | 取り出さずにベースブランチの参照だけを fast-forward で進める（`update_ref`。#2140） |
| ベースブランチにいて、変更が無い | fast-forward（`fast_forward`） |
| 変更が取り込む差分と重なり、どれも origin と同じ中身（版のファイルの残りなど） | その変更を戻してから fast-forward（`restore_then_ff`。中身は失われない。#3108） |
| 変更が取り込む差分と重ならない | そのまま fast-forward（git は重ならない変更を残す） |
| 変更が取り込む差分と重なり、origin と違う中身がある | 止める（`stop`。利用者の変更を消さない） |
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple

FAST_FORWARD, UPDATE_REF, RESTORE_THEN_FF, STOP = "fast_forward", "update_ref", "restore_then_ff", "stop"


class Plan(NamedTuple):
    """手と、戻すファイル（`restore_then_ff` のとき）か止めた理由のファイル（`stop` のとき）。"""

    action: str
    paths: tuple[str, ...] = ()


def plan(current: str | None, base: str, dirty: Iterable[str], incoming: Iterable[str], landed: Iterable[str]) -> Plan:
    """手を決める。

    `current` はメインディレクトリのブランチ（detached HEAD は None）、`dirty` は変更のあるパス、`incoming` は
    取り込む差分のパス、`landed` は変更のあるパスのうち中身が origin のベースブランチと同じもの。
    """
    if current != base:
        return Plan(UPDATE_REF)
    overlap = sorted(set(dirty) & set(incoming))
    if not overlap:
        return Plan(FAST_FORWARD)
    differing = tuple(p for p in overlap if p not in set(landed))
    if differing:
        return Plan(STOP, differing)
    return Plan(RESTORE_THEN_FF, tuple(overlap))
