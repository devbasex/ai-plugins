"""進みの観測: 実装のステップの無進捗の打ち切りの締め切りを決める（#2505 の決定 4〜7）。

進みとして数えるのは、会話の記録の伸び・進捗ログの worker の行・コミットの増加のどれかである。待ちの部品（`run_ticking`）は
待ちの周期ごとに `deadline` を問い、最後の進みに許容時間を足した時刻まで締め切りをずらす。経過時間の上限（`hard`）は
書いたときだけ効く。起動し直し（利用上限でのアカウントの切り替え）は開始の時刻が変わるので、そこから数え直す。
"""

from __future__ import annotations

import time
from typing import Callable

import limits
import slow_step as ss

OBSERVE_EVERY = 60  # 信号を読む間隔（秒）
TRANSCRIPT_GRACE = limits.seconds("supervise.transcript-grace")
BLIND_STALL = limits.seconds("supervise.blind-stall")


class ProgressWatch:
    """1 つのステップの進みを見る。`session` は会話の id と設定の置き場（起動のたびに書き換わる辞書）、`lines` は worker の行の数、
    `commits` は作業場所のコミットの数を返す。"""

    def __init__(
        self,
        stall: int,
        session: dict,
        lines: Callable[[], int],
        commits: Callable[[], int | None],
        hard: int | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.stall, self.hard, self.session, self.lines, self.commits, self.now = stall, hard, session, lines, commits, now
        self.every = min(OBSERVE_EVERY, stall / 3)
        self.started: float | None = None
        self.last_at = now()
        self.last_signal = "start"
        self.observable = True
        self.seen_transcript = False
        self.next_at = 0.0
        self.mark: tuple | None = None

    @property
    def allowance(self) -> int:
        """今の許容時間（秒）。会話の記録を読めないときは長い値を使う。"""
        return self.stall if self.observable else max(self.stall, BLIND_STALL)

    def first_timeout(self) -> int:
        """待ちの最初の締め切りまでの秒。ここから `deadline` が後ろへずらす。"""
        return min(self.stall, self.hard) if self.hard else self.stall

    def _start(self, started: float) -> None:
        self.started, self.last_at, self.last_signal = started, started, "start"
        self.seen_transcript, self.observable, self.mark = False, True, None
        self.next_at = 0.0

    def _read(self) -> tuple:
        size = ss.session_size(self.session)
        return (str(self.session.get("id") or ""), size), self.lines(), self.commits()

    def _observe(self, now: float) -> None:
        cur = self._read()
        self.seen_transcript = self.seen_transcript or cur[0][1] > 0
        self.observable = self.seen_transcript or now - (self.started or now) < TRANSCRIPT_GRACE
        before, self.mark = self.mark, cur
        if before is None:
            return
        for name, old, new in zip(("transcript", "progress_log", "commit"), before, cur):
            if new != old and (name != "transcript" or new[1] > 0):
                self.last_at, self.last_signal = now, name

    def deadline(self, started: float) -> float:
        """`run_ticking` の `extend`。信号を読み、今の締め切りの時刻を返す。"""
        now = self.now()
        if self.started != started:
            self._start(started)
        if now >= self.next_at:
            self.next_at = now + self.every
            self._observe(now)
        limit = self.last_at + self.allowance
        return min(limit, started + self.hard) if self.hard else limit

    def stalled(self) -> bool:
        """打ち切りが無進捗によるものか（偽なら経過時間の上限が先に来た）。"""
        return not (self.hard and self.started is not None and self.now() - self.started >= self.hard)
