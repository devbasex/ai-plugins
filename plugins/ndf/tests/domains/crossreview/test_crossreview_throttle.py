"""PR へ書く操作が GitHub の二次レート制限に当たったときの待ちと、スレッドを続けて作るときの間隔（#3941）。"""

from __future__ import annotations

from typing import Optional

import pytest

from ndf.contracts import limits
from ndf.domains.crossreview import threads, throttle_decide

HEAD = "a" * 40


class _Limited(Exception):
    """`github.GitHubError` の形（状態・待ちの秒・文）の偽物。"""

    def __init__(self, detail: str, status: int = 403, retry_after: Optional[float] = None):
        super().__init__(detail)
        self.status, self.retry_after = status, retry_after


def test_secondary_limit_is_told_by_the_marker_or_the_body() -> None:
    assert throttle_decide.secondary_limited(403, "You have exceeded a secondary rate limit.")
    assert throttle_decide.secondary_limited(429, "GitHub の上限（rest のスロット・secondary rate limit）: POST /x")
    assert not throttle_decide.secondary_limited(403, "Resource not accessible by integration")
    assert not throttle_decide.secondary_limited(500, "secondary rate limit")


def test_wait_uses_retry_after_then_the_interval_and_stops_at_the_attempts() -> None:
    attempts = int(limits.value(throttle_decide.POLICY, "attempts"))
    interval = limits.value(throttle_decide.POLICY, "interval")
    assert throttle_decide.wait_before_retry(True, 1, 7.0) == 7.0
    assert throttle_decide.wait_before_retry(True, 1, None) == interval
    assert throttle_decide.wait_before_retry(True, attempts, 7.0) is None
    assert throttle_decide.wait_before_retry(False, 1, 7.0) is None


def test_patient_retries_only_the_secondary_limit() -> None:
    waits: list[float] = []
    calls = iter([_Limited("secondary rate limit", retry_after=2.0), "ok"])

    def call():
        got = next(calls)
        if isinstance(got, Exception):
            raise got
        return got

    assert threads.patient(call, _Limited, waits.append) == "ok"
    assert waits == [2.0]
    with pytest.raises(_Limited):
        threads.patient(lambda: (_ for _ in ()).throw(_Limited("403 forbidden")), _Limited, waits.append)
    assert waits == [2.0]


class _Hub:
    """書く要求の偽物。最初の `limited` 回は二次レート制限で断る。"""

    repo = "o/r"

    def __init__(self, limited: int = 0):
        self.limited = limited
        self.posts: list[str] = []

    def rest(self, method, path, payload=None, params=None):
        if method == "GET":
            return [{"filename": "a.py"}]
        if self.limited:
            self.limited -= 1
            raise _Limited("secondary rate limit")
        self.posts.append(path)
        return {"id": len(self.posts)}


class _Publisher:
    def guarded(self, *texts):
        return list(texts), {}


def test_github_port_waits_on_the_limit_and_spaces_threads() -> None:
    waits: list[float] = []
    now = [100.0]
    hub = _Hub(limited=1)
    port = threads.GitHubPort(hub, _Publisher(), 7, _Limited, sleep=waits.append, clock=lambda: now[0])
    assert port.open_thread("a.py", None, HEAD, "一つ目") == 1
    assert waits == [limits.value(throttle_decide.POLICY, "interval")]
    port.open_thread("a.py", None, HEAD, "二つ目")
    assert waits[-1] == limits.value(throttle_decide.SPACING, "interval")
    port.reply(1, "直した")
    assert len(hub.posts) == 3


def test_limit_on_a_line_comment_is_not_retried_as_a_file_comment() -> None:
    """PR 3944 の r1-9: 行への投稿が二次レート制限の打ち直しの上限に当たれば、ファイルへ付け直さずにその失敗を返す
    （投稿は attempts 回まで）。"""
    attempts = int(limits.value(throttle_decide.POLICY, "attempts"))
    hub = _Hub(limited=attempts * 2)
    port = threads.GitHubPort(hub, _Publisher(), 7, _Limited, sleep=lambda s: None, clock=lambda: 0.0)
    with pytest.raises(_Limited):
        port.open_thread("a.py", 3, HEAD, "本文")
    assert hub.limited == attempts and hub.posts == []
