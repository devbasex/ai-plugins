"""リレーハブの起動と再起動の判断（#2563・#2583・#2753）。時計とプロセスを読まない純粋な関数。

- 置き換えの起動（spawn --replace）は、止める前に対象を除いた本数で上限を判定する。日次の再起動の回数は見ない（#3989 の決定 7）
- 再起動（restart）は、止める前に上限を判定する。上限の数には再起動する対象を数えない（止めた後に上限で
  断ってレーンが止まったまま残ることが無い。#2753）
- 起動（spawn・restart）が「起動した」を返すのは、ラッパーのプロセスが生きていて、保存先の一覧に載ったときだけ
  （#2563）
- ラッパーの保存先（プラグインのバージョンの保存先）が無ければ、何も出さずに失敗せず、理由と打ち直しの手を返す（#2583）
- バージョンの違うラッパー（旧い NDF 10.x のものを含む）が動いていれば、起動も再起動も断る。旧いラッパーと新しい hook を
  混ぜない（#3341 の 3341-2）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple, Optional


@dataclass(frozen=True)
class Lanes:
    """名前つきのラッパーの数と上限。`target_running` は再起動する対象が今動いているか。"""

    running: int
    max_named: int
    target_running: bool
    starts_today: int
    max_starts: int


GO = "go"
REFUSE = "refuse"


class Decision(NamedTuple):
    """`kind` は `GO`（進める）か `REFUSE`（断る。対象は止めない）。"""

    kind: str
    reason: str

    @property
    def ok(self) -> bool:
        return self.kind == GO


def _over_limit_without_target(lanes: Lanes) -> Optional[Decision]:
    """対象を除いた本数で上限を判定する。超えるなら断る判断を、収まるなら None を返す。"""
    others = lanes.running - (1 if lanes.target_running else 0)
    if others + 1 > lanes.max_named:
        return Decision(REFUSE, f"対象を除いて {others} 本が動いており、上限 {lanes.max_named} 本を超える。対象は止めない")
    return None


def restart(lanes: Lanes) -> Decision:
    """再起動してよいか。止める前に判定する。"""
    refused = _over_limit_without_target(lanes)
    if refused is not None:
        return refused
    if lanes.starts_today >= lanes.max_starts:
        return Decision(REFUSE, f"今日起動し直した回数が上限（{lanes.max_starts} 回）に当たった。対象は止めない")
    return Decision(GO, "止めてから起動し直す")


def replace(lanes: Lanes) -> Decision:
    """行を始める置き換えの起動（`spawn --replace`）をしてよいか。対象を除いた本数で判定し、日次の再起動の回数は見ない（#3989 の決定 7）。"""
    refused = _over_limit_without_target(lanes)
    if refused is not None:
        return refused
    return Decision(GO, "止めてから置き換えて起動する")


def spawn(lanes: Lanes) -> Decision:
    """新しく起動してよいか。"""
    if lanes.running + 1 > lanes.max_named:
        return Decision(REFUSE, f"{lanes.running} 本が動いており、上限 {lanes.max_named} 本を超える")
    return Decision(GO, "起動する")


@dataclass(frozen=True)
class Started:
    """起動の後に確かめた事実。"""

    wrapper_present: bool
    process_alive: bool
    registered: bool


class Running(NamedTuple):
    """動いているラッパー 1 本。`version` は登録したバージョン（旧い NDF のラッパーは None）。"""

    name: str
    version: Optional[str]


def _major(version: Optional[str]) -> Optional[str]:
    """バージョンの最初の `.` の前（メジャーバージョン）。"""
    return None if version is None else version.split(".", 1)[0]


def mixed(own_version: str, running: list[Running]) -> Decision:
    """旧い NDF かメジャーバージョンの違うラッパーが動いていれば断る（I6）。"""
    own = _major(own_version)
    other = [r for r in running if r.version is None or _major(r.version) != own]
    if not other:
        return Decision(GO, "動いているラッパーはすべて同じバージョン")
    names = "・".join(f"{r.name}（{r.version or '旧い NDF'}）" for r in other)
    return Decision(
        REFUSE,
        f"バージョンの違うラッパーが動いている: {names}。旧いバージョンのラッパーを止めて（/exit）から、このバージョンで起動し直す",
    )


def started(facts: Started, name: str) -> Decision:
    """起動を「起動した」と返してよいか。"""
    if not facts.wrapper_present:
        return Decision(
            REFUSE,
            f"{name} のラッパーの保存先が無い（プラグインの更新でバージョンの保存先が消えた）。ndf relay install で置き直してから打ち直す",
        )
    if not facts.process_alive:
        return Decision(REFUSE, f"{name} のラッパーが立ち上がらなかった（プロセスが無い）")
    if not facts.registered:
        return Decision(REFUSE, f"{name} のラッパーは動いているが、保存先の一覧に載らない")
    return Decision(GO, f"{name} を起動した")
