"""ラッパーの使用率の API の取得のスレッド（#3604 #3864 の決定 4。`wrapper.py` から行数の上限のため分けた）。

見回りが取り直すアカウントをキューへ入れ、別のスレッドが 1 本ずつ `accounts.fetch_usage` を打って結果を返りのキューへ
入れる。見回りと端末の中継を止めない。ファイルを書くのは取得の中だけで、このスレッドは画面と `log.jsonl` に書かない
（返りを読む見回り（`FetchSteps._drain_fetch`）が書く）。止めるとき（`close`）は未開始の取得を捨て、`FetchGate` を閉じて
新しい GET と更新を始めさせず、送り始めた往復が終わるのを待つ。
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union

from ndf.adapters import claude_usage
from ndf.contracts import limits
from ndf.domains.relay import accounts, launch_inputs

# 使用率の API の応答の形が変わったときの 1 行（値と本文は出さない）
SHAPE_LINE = "ndf-relay: 使用率の API の応答の形が変わった。最後に取れた値で続ける"

Fetch = Callable[..., Optional[claude_usage.Usage]]  # `accounts.fetch_usage` の形（テストは偽物を渡す）
Result = Union[claude_usage.Usage, Exception, None]


class Fetcher:
    """ラッパー 1 つの取得のスレッド。`busy` は取得の途中の名前、`tried` は名前ごとに最後にキューへ入れた登録の世代と時刻（壁時計）。"""

    def __init__(self, fetch: Fetch):
        self.fetch = fetch
        self.gate = accounts.FetchGate()
        self.jobs: queue.Queue[Optional[tuple[accounts.FetchTarget, float, bool]]] = queue.Queue()
        self.done: queue.Queue[tuple[str, Result]] = queue.Queue()
        self.busy: set[str] = set()
        self.tried: dict[str, tuple[str, float]] = {}
        self.shape_told = False
        self.thread: Optional[threading.Thread] = None
        self.closed = False

    def put(self, target: accounts.FetchTarget, now: float, force: bool = False) -> None:
        """取り直すアカウントをキューへ入れる（初めてならスレッドを始める）。`force` なら取り直しの間隔を待たない。"""
        if self.thread is None:
            self.thread = threading.Thread(target=self._work, name="ndf-relay-usage-fetch", daemon=True)
            self.thread.start()
        self.busy.add(target.name)
        self.tried[target.name] = (target.generation, now)
        self.jobs.put((target, now, force))

    def _work(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                return
            target, now, force = job
            got: Result
            try:
                got = self.fetch(target, now, force, self.gate)
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


class FetchSteps:
    """`Wrapper` の使用率の取得の手。属性と記録の手（`_log`・`_say`・`_inputs`）は `Wrapper` が持つ。"""

    store: Path
    fetcher: Optional[Fetcher]
    due_memo: dict[str, tuple[tuple, float]]
    _log: Callable[[Mapping[str, Any]], None]
    _say: Callable[[str], None]
    _inputs: Callable[[float, bool], launch_inputs.Inputs]

    def _queue_fetch(self, now: float) -> None:
        """取り直す登録済みアカウント（動いていないものも、待つ間も）を今の登録の世代と組にしてキューへ入れる（決定 4）。

        取り直すのは、最後に試した時刻から `relay.usage-fetch` を過ぎたか、その後のリセットを過ぎたとき（`accounts.due_at`）。
        取得の記録のスタンプと `tried` が同じ間は、その時刻を再利用する（見回りごとに取得の記録を開かない）。
        """
        f = self.fetcher
        usage = self._inputs(now, False).usage if f is not None else None
        if f is None or usage is None:
            return
        interval = limits.seconds("relay.usage-fetch")
        for name, gen in usage.generations.items():
            last = f.tried.get(name)
            tried = last[1] if last is not None and last[0] == gen else None  # 登録し直した名前は旧い世代の投入時刻を使わない
            if name in f.busy:
                continue
            path = accounts.fetch_path(self.store, name, gen)
            key = (gen, launch_inputs.stamp(path), tried)
            memo = self.due_memo.get(name)
            at = memo[1] if memo is not None and memo[0] == key else accounts.due_at(path, interval, tried)
            if at <= now:
                f.put(accounts.FetchTarget(self.store, name, gen), now)
                self.due_memo.pop(name, None)
            else:
                self.due_memo[name] = (key, at)

    def _drain_fetch(self) -> None:
        """取得の返りを読む。形の変化は 1 つのラッパーで 1 回だけ知らせ、取れたら次の変化でまた知らせる（決定 4）。"""
        f = self.fetcher
        if f is None:
            return
        failed = []
        for name, got in f.results():
            if isinstance(got, Exception):
                failed.append({"account": name, "error": type(got).__name__})
                continue
            if got is None:
                continue
            if got.error == "shape" and not f.shape_told:
                f.shape_told = True
                self._say(SHAPE_LINE)
                self._log({"kind": "usage_shape", "account": name, "items": list(got.missing)})
            elif got.error is None:
                f.shape_told = False
        if failed:
            self._log({"kind": "usage_fetch_failed", "count": len(failed), "sample": failed[:10]})
