"""ラッパーがバックグラウンドの作業の記録を読み、待ちを状態に書く手（#4049。`wrapper.py` から行数の上限のため分けた）。

`Wrapper` の基底クラス `BusySteps` が持つ。記録を書くのは子の会話の hook だけで、ここは読むだけである（`signals.read_busy`）。
`_BUSY_ALLOW` は、判断（`switch_decide.Step.busy`）ごとに、子を止める直前に読み直した記録で止めてよいかを決める。判断の後に
子が作業を始めていれば止めない。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from ndf.domains.relay import signals, switch_decide
from ndf.domains.relay.wrapper_launch import Ended

Section = Any  # `wrapper.Section`（import の輪を作らないよう型の名前だけで受ける）
Busy = Optional[tuple[int, str, str, int]]  # `signals.read_busy` の返り


def _busy_zero(found: Busy) -> bool:
    """作業が無いと確かめられたか（監視は数えない）。"""
    return found is not None and found[0] == 0


def _busy_none_running(found: Busy) -> bool:
    """作業も監視も無いか、確かめられないか。"""
    return found is None or (found[0] == 0 and found[3] == 0)


_BUSY_ALLOW: Mapping[Optional[str], Callable[[Busy], bool]] = {"zero": _busy_zero, "none_running": _busy_none_running}


class BusySteps:
    """`Wrapper` のバックグラウンドの作業の手。属性と `_write_state` は `Wrapper` が持つ。"""

    dir: Path
    _write_state: Callable[[str, str], None]

    def _with_background(self, sec: Section, state: switch_decide.SectionState) -> switch_decide.SectionState:
        """選ばれる判定が件数を要る見回りだけ、今の起動の記録を読んで状態へ足す。"""
        if not switch_decide.needs_background(state):
            return state
        found = signals.read_busy(self.dir, sec.launch, sec.session_id)
        return state if found is None else replace(state, background=switch_decide.Background(*found))

    def _on_active(self, step: switch_decide.Step, sec: Section) -> Optional[Ended]:
        """止めるものも待つものも無い見回り。前の見回りの待ちの理由を状態に残さない。"""
        self._write_state("running", step.reason)
        return None
