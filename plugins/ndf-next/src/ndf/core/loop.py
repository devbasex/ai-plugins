"""判断のループと判断の関数の型。

判断の関数は、今の状態と前の手の結果を受けて次の手（`Next`）を返す純粋な関数である。手を実行する関数は手を受けて
結果を返す。`run` は 2 つを交互に呼び、終わりの手が出たらその状態を返す。

手を実行する関数に耐久ステップ（`ndf.core.durable.step` で包んだ関数）を渡し、`run` を耐久ワークフローの中で呼ぶと、
落ちた後の回復で済んだ手を流し直さずに続く。普通の関数を渡せば DBOS を起こさずに同じ判断を回せる。

判断の関数は同じ状態と結果に同じ次の手を返す（回復のときに判断の関数をもう一度呼ぶため）。このモジュールは
DBOS も入出力も読まない。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

A = TypeVar("A")
S = TypeVar("S")
D = TypeVar("D")


class DecideLoopError(Exception):
    """判断の関数が次の手の形でない値を返した（判断の関数の誤り）。"""


@dataclass(frozen=True)
class Next(Generic[A, S]):
    """判断の関数が返す次の手。実行する手 1 つか、終わりのどちらか。"""

    act: A | None
    state: S
    finished: bool

    @classmethod
    def step(cls, act: A, state: S) -> Next[A, S]:
        """手 `act` を実行し、状態を `state` へ進める次の手。"""
        return cls(act=act, state=state, finished=False)

    @classmethod
    def finish(cls, state: S) -> Next[Any, S]:
        """状態 `state` で終わる手。"""
        return cls(act=None, state=state, finished=True)


Decide = Callable[[S, D | None], Next[A, S]]
Perform = Callable[[A], D]


def run(decide: Decide[S, D, A], perform: Perform[A, D], state: S) -> S:
    """終わりの手が出るまで、判断の関数と手を実行する関数を交互に呼ぶ。

    初めの呼び出しでは結果に None を渡す。戻り値は判断の関数が終わりの手に載せた状態である。
    `perform` の例外は包まずに上げる。
    """
    result: D | None = None
    while True:
        nxt = _checked(decide(state, result))
        if nxt.finished:
            return nxt.state
        state = nxt.state
        result = perform(nxt.act)


def _checked(nxt: object) -> Next[Any, Any]:
    """判断の関数の戻り値が次の手の形かを確かめて返す。形でなければ DecideLoopError を上げる。"""
    if not isinstance(nxt, Next):
        raise DecideLoopError(f"判断の関数が次の手でない値を返した: {nxt!r}")
    if not nxt.finished and nxt.act is None:
        raise DecideLoopError(f"終わりでない次の手が実行する手を持たない: {nxt!r}")
    return nxt
