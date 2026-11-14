"""リレーハブの起動と起こし直しの判断（#2563・#2583・#2753）。時計とプロセスを読まない純粋な関数。

- 起こし直し（restart）は、止める前に上限を判定する。上限の数には起こし直す対象を数えない（止めた後に上限で
  断ってレーンが止まったまま残ることが無い。#2753）
- 起動（spawn・restart）が「起動した」を返すのは、ラッパーのプロセスが生きていて、置き場の一覧に載ったときだけ
  （#2563）
- ラッパーの置き場（プラグインの版の置き場）が無ければ、何も出さずに失敗せず、理由と打ち直しの手を返す（#2583）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple


@dataclass(frozen=True)
class Lanes:
    """名前つきのラッパーの数と上限。`target_running` は起こし直す対象が今動いているか。"""

    running: int
    max_named: int
    target_running: bool
    starts_today: int
    max_starts: int


class Decision(NamedTuple):
    """`kind` は `go`（進める）か `refuse`（断る。対象は止めない）。"""

    kind: str
    reason: str


def restart(lanes: Lanes) -> Decision:
    """起こし直してよいか。止める前に判定する。"""
    others = lanes.running - (1 if lanes.target_running else 0)
    if others + 1 > lanes.max_named:
        return Decision("refuse", f"対象を除いて {others} 本が動いており、上限 {lanes.max_named} 本を超える。対象は止めない")
    if lanes.starts_today >= lanes.max_starts:
        return Decision("refuse", f"今日起動し直した回数が上限（{lanes.max_starts} 回）に当たった。対象は止めない")
    return Decision("go", "止めてから起動し直す")


def spawn(lanes: Lanes) -> Decision:
    """新しく起動してよいか。"""
    if lanes.running + 1 > lanes.max_named:
        return Decision("refuse", f"{lanes.running} 本が動いており、上限 {lanes.max_named} 本を超える")
    return Decision("go", "起動する")


@dataclass(frozen=True)
class Started:
    """起動の後に確かめた事実。"""

    wrapper_present: bool
    process_alive: bool
    registered: bool


def started(facts: Started, name: str) -> Decision:
    """起動を「起動した」と返してよいか。"""
    if not facts.wrapper_present:
        return Decision(
            "refuse",
            f"{name} のラッパーの置き場が無い（プラグインの更新で版の置き場が消えた）。ndf relay install で置き直してから打ち直す",
        )
    if not facts.process_alive:
        return Decision("refuse", f"{name} のラッパーが立ち上がらなかった（プロセスが無い）")
    if not facts.registered:
        return Decision("refuse", f"{name} のラッパーは動いているが、置き場の一覧に載らない")
    return Decision("go", f"{name} を起動した")
