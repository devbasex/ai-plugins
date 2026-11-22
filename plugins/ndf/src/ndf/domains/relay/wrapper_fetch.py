"""ラッパーの使用率の API の取得のスレッド（#3604 #3864 の決定 4。`wrapper.py` から行数の上限のため分けた）。

見回りが取り直すアカウントをキューへ入れ、別のスレッドが 1 本ずつ `accounts.fetch_usage` を打って結果を返りのキューへ
入れる。見回りと端末の中継を止めない。ファイルを書くのは取得の中だけで、このスレッドは画面と `log.jsonl` に書かない
（返りを読む見回り（`Wrapper._drain_fetch`）が書く）。止めるとき（`close`）は未開始の取得を捨て、`FetchGate` を閉じて
新しい GET と更新を始めさせず、送り始めた往復が終わるのを待つ。
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Callable, Optional, Union

from ndf.adapters import claude_usage
from ndf.domains.relay import accounts

# 使用率の API の応答の形が変わったときの 1 行（値と本文は出さない）
SHAPE_LINE = "ndf-relay: 使用率の API の応答の形が変わった。status line の記録で続ける"

Fetch = Callable[..., Optional[claude_usage.Usage]]  # `accounts.fetch_usage` の形（テストは偽物を渡す）
Result = Union[claude_usage.Usage, Exception, None]


class Fetcher:
    """ラッパー 1 つの取得のスレッド。`busy` は取得の途中の名前、`tried` は名前ごとに最後にキューへ入れた登録の世代と時刻（壁時計）。"""

    def __init__(self, state_dir: Path, fetch: Fetch):
        self.state_dir, self.fetch = state_dir, fetch
        self.gate = accounts.FetchGate()
        self.jobs: queue.Queue[Optional[tuple[accounts.FetchTarget, float, Optional[float]]]] = queue.Queue()
        self.done: queue.Queue[tuple[str, Result]] = queue.Queue()
        self.busy: set[str] = set()
        self.tried: dict[str, tuple[str, float]] = {}
        self.shape_told = False
        self.thread: Optional[threading.Thread] = None
        self.closed = False

    def put(self, target: accounts.FetchTarget, now: float, until: Optional[float]) -> None:
        """取り直すアカウントをキューへ入れる（初めてならスレッドを始める）。"""
        if self.thread is None:
            self.thread = threading.Thread(target=self._work, name="ndf-relay-usage-fetch", daemon=True)
            self.thread.start()
        self.busy.add(target.name)
        self.tried[target.name] = (target.generation, now)
        self.jobs.put((target, now, until))

    def _work(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                return
            target, now, until = job
            got: Result
            try:
                got = self.fetch(self.state_dir, target, now, until, self.gate)
            except Exception as exc:  # noqa: BLE001 ネットワーク: スレッドを止めず、返りのキューで見回りへ渡す（見回りが記録する）
                got = exc
            self.done.put((target.name, got))

    def results(self) -> list[tuple[str, Result]]:
        """返りのキューに溜まった結果（名前, 結果か例外か None）。取得の途中の名前から外す。"""
        out: list[tuple[str, Result]] = []
        while True:
            try:
                name, got = self.done.get_nowait()
            except queue.Empty:  # 溜まった分を読み終えた
                return out
            self.busy.discard(name)
            out.append((name, got))

    def close(self) -> None:
        """未開始の取得を捨て、GET と更新を始めさせず、送り始めた往復と今の取得が終わるまで待つ（2 回目からは何もしない）。"""
        if self.closed:
            return
        self.closed = True
        while True:
            try:
                self.jobs.get_nowait()
            except queue.Empty:  # 未開始の取得を捨て終えた
                break
        self.jobs.put(None)
        self.gate.close()
        if self.thread is not None:
            self.thread.join(accounts.fetch_wait())
