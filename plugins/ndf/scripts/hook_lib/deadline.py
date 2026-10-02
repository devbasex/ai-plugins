"""hook の締め切り（#1340 の決定 10・11）。

NDF が配る PreToolUse と userPromptSubmit の hook の上限は 5 秒か 10 秒である。最も短い 5 秒から余裕
`MARGIN_SECONDS` を引いた `DEADLINE_SECONDS` を、ランタイムを問わず使う（I13: 締め切り + 余裕 ≤ 上限）。

締め切りは `signal.setitimer(ITIMER_REAL)` の割り込みで掛ける。`git` の呼び出し・ロックの待ち・構文解析の読み込みの
どこで時間を使っていても、hook の 1 回の全体に掛かる。`subprocess.run` は待っている間の例外で子のプロセスを止めて
送り直すため、子が残らない。`setitimer` を持たない環境（Windows）とメインのスレッドの外では掛けずに今のまま通す。
"""

from __future__ import annotations

import signal
import threading

DEADLINE_SECONDS = 3.5
MARGIN_SECONDS = 1.5


class HookDeadlineExceeded(BaseException):
    """締め切りを過ぎた。判定の中の `except Exception` に飲まれないよう `BaseException` から派生する。"""

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


class Deadline:
    """hook の 1 回の締め切り。`current` に今の判定の名前を置き、過ぎたらその名前で `HookDeadlineExceeded` を送出する。"""

    def __init__(self, seconds: float | None = None):
        self.seconds = DEADLINE_SECONDS if seconds is None else seconds
        self.current = ""
        self._prev = None
        self._armed = False

    def _fire(self, signum, frame) -> None:
        if self._armed:  # 外した後に届いた割り込みは捨てる
            raise HookDeadlineExceeded(self.current or "hook")

    def start(self) -> bool:
        """締め切りを掛ける。掛けられない環境なら False（今の振る舞いのまま通す）。"""
        if not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread():
            return False
        self._prev = signal.signal(signal.SIGALRM, self._fire)
        self._armed = True
        signal.setitimer(signal.ITIMER_REAL, self.seconds)
        return True

    def cancel(self) -> None:
        if not self._armed:
            return
        self._armed = False
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, self._prev if self._prev is not None else signal.SIG_DFL)

    def notice(self, name: str) -> str:
        """標準エラーへ出す 1 行。"""
        return f"[ndf hook] 締め切り {self.seconds:g} 秒を過ぎたため {name} を飛ばして通した"
