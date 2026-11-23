"""PR へ書く操作が GitHub の二次レート制限に当たったときの待ち（#3941・#4016 の決定 8）。書き込みの間隔はマシン全体の
`WriteGate` が持ち、スレッドを書く口は間を空けない。"""

from __future__ import annotations

from typing import Optional

import pytest

from ndf.contracts import limits
from ndf.domains.crossreview import threads, throttle_decide

HEAD = "a" * 40


class _Limited(Exception):
    """`github.GitHubError` の形（状態・待ちの秒・二次レート制限か・GitHub が待ちの秒を示したか）の偽物。"""

    def __init__(self, detail: str, status: int = 403, retry_after: Optional[float] = 60.0, secondary: bool = True, hinted: bool = False):
        super().__init__(detail)
        self.status, self.retry_after, self.secondary, self.hinted, self.reasons = status, retry_after, secondary, hinted, ()


def test_wait_grows_exponentially_when_github_gives_no_wait() -> None:
    """受け入れ条件 5: GitHub が待ちの秒を示さなければ 60・120・240 秒と延ばし、4 回目の失敗で打ち直さない。"""
    assert [throttle_decide.wait_before_retry(True, n, 60.0, False) for n in (1, 2, 3)] == [60.0, 120.0, 240.0]
    assert throttle_decide.wait_before_retry(True, 4, 60.0, False) is None


def test_wait_uses_the_hinted_seconds_and_does_not_wait_for_other_failures() -> None:
    assert throttle_decide.wait_before_retry(True, 2, 7.0, True) == 7.0
    assert throttle_decide.wait_before_retry(False, 1, 7.0, True) is None


def test_wait_stops_growing_at_the_max_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    capped = limits.Policy(throttle_decide.POLICY, "", interval=60, factor=2, max_interval=100, attempts=10)
    monkeypatch.setattr(limits, "policy", lambda name: capped)
    assert throttle_decide.wait_before_retry(True, 3, None, False) == 100.0


def _failing(*outcomes: object):
    calls = iter(outcomes)

    def call():
        got = next(calls)
        if isinstance(got, Exception):
            raise got
        return got

    return call


def test_patient_waits_longer_each_time_tells_the_gate_and_gives_up_at_the_attempts() -> None:
    """受け入れ条件 5: 待ちの秒を `block` へ知らせてから眠る。4 回目の失敗はそのまま出す。"""
    waits: list[float] = []
    blocked: list[float] = []
    call = _failing(*[_Limited("secondary rate limit") for _ in range(4)])
    with pytest.raises(_Limited):
        threads.patient(call, _Limited, waits.append, block=blocked.append)
    assert waits == blocked == [60.0, 120.0, 240.0]


def test_patient_uses_the_hinted_wait_and_does_not_retry_other_failures() -> None:
    waits: list[float] = []
    call = _failing(_Limited("secondary rate limit", retry_after=2.0, hinted=True), "ok")
    assert threads.patient(call, _Limited, waits.append) == "ok"
    assert waits == [2.0]
    with pytest.raises(_Limited):
        threads.patient(_failing(_Limited("403 forbidden", secondary=False)), _Limited, waits.append)
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


def test_github_port_waits_only_on_the_limit_and_does_not_space_threads() -> None:
    """#4016: 書き込みの間隔は `WriteGate` が持つので、口は二次レート制限の打ち直しの待ちだけ眠る。"""
    waits: list[float] = []
    blocked: list[float] = []
    hub = _Hub(limited=1)
    port = threads.GitHubPort(hub, _Publisher(), 7, _Limited, sleep=waits.append, block=blocked.append)
    assert port.open_thread("a.py", None, HEAD, "一つ目") == 1
    assert waits == [limits.value(throttle_decide.POLICY, "interval")]
    # 打ち直しの待ちはマシン全体の待ちの終わりへも知らせる（PR 4265 の r1-1）
    assert blocked == waits
    port.open_thread("a.py", None, HEAD, "二つ目")
    port.reply(1, "直した")
    assert len(hub.posts) == 3 and len(waits) == 1


def test_review_waits_on_the_limit_like_other_writes() -> None:
    """レビューの作成も二次レート制限なら 60・120 秒と延ばして打ち直し、待ちを `block` へ知らせる（受け入れ条件 5）。"""
    waits: list[float] = []
    blocked: list[float] = []
    hub = _Hub(limited=2)
    port = threads.GitHubPort(hub, _Publisher(), 7, _Limited, sleep=waits.append, block=blocked.append)
    assert port.open_review(HEAD, [threads.Placed("r1-1", "a.py", 3, "本文")]) == 1
    assert waits == blocked == [60.0, 120.0] and hub.posts == ["/repos/o/r/pulls/7/reviews"]


def test_limit_on_a_line_comment_is_not_retried_as_a_file_comment() -> None:
    """PR 3944 の r1-9: 行への投稿が二次レート制限の打ち直しの上限に当たれば、ファイルへ付け直さずにその失敗を返す
    （投稿は attempts 回まで）。"""
    attempts = int(limits.value(throttle_decide.POLICY, "attempts"))
    hub = _Hub(limited=attempts * 2)
    port = threads.GitHubPort(hub, _Publisher(), 7, _Limited, sleep=lambda s: None)
    with pytest.raises(_Limited):
        port.open_thread("a.py", 3, HEAD, "本文")
    assert hub.limited == attempts and hub.posts == []
