"""区間を切り替えるかの判断（#2962・#2438・#3035）。

中継のラッパーが見回るたびに、今の区間の状態（シグナルファイルの中身・子の生き死に・起動し直した回数・経った秒）を
値で渡し、次の手を受け取る。判断は時計と端末を読まない純粋な関数で、同じ状態に同じ手を返す（端末と時計は呼ぶ側が
読んで値で渡す）。擬似端末を立てずに単体のテストで確かめられる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple, Optional


@dataclass(frozen=True)
class SectionState:
    """見回り 1 回の区間の状態。

    - `next_block`: Stop の hook が書いた次の区間のブロック（無ければ None）
    - `question`: AskUserQuestion の hook が書いた問いがまだ回されていないか
    - `child_alive`: 子のセッションのプロセスが生きているか
    - `child_exit`: 子が終わったときの終了コード（生きていれば None）
    - `starts_today`・`max_starts`: 今日起動し直した回数と上限
    - `idle_seconds`・`stall_seconds`: 子の出力が止まってからの秒と、止まったとみなす秒
    """

    next_block: Optional[str]
    question: bool
    child_alive: bool
    child_exit: Optional[int]
    starts_today: int
    max_starts: int
    idle_seconds: float = 0.0
    stall_seconds: float = 0.0


class Step(NamedTuple):
    """次の手。`kind` は `wait`・`forward`・`switch`・`restart`・`finish`・`stop`。"""

    kind: str
    reason: str
    prompt: str = ""


def decide(state: SectionState) -> Step:
    """次の手を決める。上から順に最初に当たった条件で決まる。"""
    if state.question:
        return Step("forward", "AskUserQuestion の問いを人かオーガナイザーへ回す（hook は待たない）")
    if state.next_block is not None:
        # /goal の続き（目標が未達）でも、次の区間のブロックがあれば切り替える（#2438）
        return Step("switch", "最後の応答が次の区間のブロックを持つ", prompt=state.next_block)
    if state.child_alive:
        if state.stall_seconds and state.idle_seconds >= state.stall_seconds:
            return _restart_or_stop(state, f"子の出力が {state.idle_seconds:g} 秒止まっている")
        return Step("wait", "子が動いている")
    if state.child_exit == 0:
        return Step("finish", "子が次の区間のブロックを書かずに終わった")
    return _restart_or_stop(state, f"子が終了コード {state.child_exit} で終わった")


def _restart_or_stop(state: SectionState, why: str) -> Step:
    if state.starts_today >= state.max_starts:
        return Step("stop", f"{why}。今日起動し直した回数が上限（{state.max_starts} 回）に当たった")
    return Step("restart", why)
