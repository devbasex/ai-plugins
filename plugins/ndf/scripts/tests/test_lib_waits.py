"""待ちとやり直しの包み（lib/waits.py・#1142 の決定 19、#2343 の受け入れ条件 4・6）。外部パッケージは全体テストの環境
（根の pyproject.toml）が入れる。

眠りは差し替え、実時間を使わない。上限・間隔・回数は方針（`limits.Policy`）で渡す。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import limits  # noqa: E402
import tenacity  # noqa: E402,F401  包みの外部パッケージ。無ければ集めるところで落とす
import waits  # noqa: E402


def pol(**values) -> limits.Policy:
    return limits.Policy(name="test.wait", summary="試験", **values)


class FakeClock:
    """眠りと照会の所要時間で進む単調時計。"""

    def __init__(self, call_cost: float = 0.0):
        self.now, self.call_cost, self.calls = 0.0, call_cost, []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def query(self, value):
        def fn():
            self.calls.append(self.now)
            self.now += self.call_cost
            return value

        return fn


def test_retry_call_stops_when_the_limit_is_used_up():
    slept, told = [], []
    r = waits.retry_call(
        lambda: "limit",
        lambda v: v == "limit",
        policy=pol(seconds=90, interval=30),
        sleep=slept.append,
        on_wait=lambda s, n: told.append((s, n)),
    )
    assert (r.value, r.attempts, r.waited, r.done, r.stop) == ("limit", 4, 90.0, False, "deadline") and slept == [30, 30, 30]
    assert told == [(30, 2), (30, 3), (30, 4)]
    assert "方針 test.wait" in r.reason and "上限 90 秒" in r.reason


def test_retry_call_returns_the_first_good_result_and_retries_listed_errors():
    values = iter(["limit", "ok"])
    r = waits.retry_call(lambda: next(values), lambda v: v == "limit", policy=pol(seconds=900, interval=5), sleep=lambda s: None)
    assert (r.value, r.attempts, r.waited, r.done, r.reason) == ("ok", 2, 5.0, True, "")

    def boom():
        raise OSError("x")

    r = waits.retry_call(boom, retry_on=(OSError,), policy=pol(seconds=10, interval=5), sleep=lambda s: None)
    assert (r.attempts, r.done, type(r.error)) == (3, False, OSError)
    with pytest.raises(ZeroDivisionError):
        waits.retry_call(lambda: 1 / 0, retry_on=(OSError,), policy=pol(seconds=10, interval=5), sleep=lambda s: None)


def test_zero_interval_runs_once():
    r = waits.retry_call(lambda: "limit", lambda v: True, policy=pol(seconds=10, interval=0))
    assert (r.value, r.attempts, r.done) == ("limit", 1, False) and r.waited < 1


def test_the_same_cause_stops_before_the_total_wait():
    """受け入れ条件 4: 理由の数字だけが違う失敗は同じ原因として数え、宣言の回数で止まる。"""
    runs = iter(range(100, 200))
    slept = []
    r = waits.retry_call(
        lambda: f"CI が失敗: runtime-smoke（run {next(runs)}）",
        lambda v: True,
        policy=pol(seconds=900, interval=30, same_cause=3),
        sleep=slept.append,
    )
    assert (r.attempts, r.stop, r.same, r.done) == (3, "same-cause", 3, False) and slept == [30, 30]
    assert r.reason.startswith("同じ原因（result: CI が失敗: runtime-smoke（run <数>）") and "3 回失敗した（方針 test.wait）" in r.reason


def test_different_causes_are_counted_apart():
    texts = iter(["A が失敗", "B が失敗", "A が失敗", "B が失敗", "A が失敗"])
    r = waits.retry_call(lambda: next(texts), lambda v: True, policy=pol(seconds=900, interval=1, same_cause=3), sleep=lambda s: None)
    assert (r.attempts, r.stop, r.cause, r.same) == (5, "same-cause", "result: A が失敗", 3)


def test_the_attempts_cap_stops():
    r = waits.retry_call(lambda: "x", lambda v: True, policy=pol(seconds=900, interval=1, attempts=4), sleep=lambda s: None)
    assert (r.attempts, r.stop) == (4, "attempts") and "4 回" in r.reason


def test_a_policy_without_a_needed_value_is_refused():
    with pytest.raises(limits.LimitsError, match="interval"):
        waits.retry_call(lambda: 1, policy=pol(seconds=10))


def test_wait_until_grows_the_gap_while_nothing_changes_and_resets_on_change():
    values, slept = iter([1, 1, 1, 2, 2, 3]), []
    r = waits.wait_until(lambda: next(values), lambda v: v == 3, policy=pol(seconds=1000, interval=10, factor=2), sleep=slept.append)
    assert (r.value, r.attempts, r.waited, r.done) == (3, 6, 100.0, True) and slept == [10, 20, 40, 10, 20]


def test_wait_until_takes_the_last_query_at_the_deadline():
    """受け入れ条件 6・#2224: 次の眠りが残りを超えるときは残りだけ眠り、期限の時刻に最後の照会を打つ。"""
    slept = []
    r = waits.wait_until(lambda: 1, lambda v: False, policy=pol(seconds=100, interval=10, factor=2, max_interval=40), sleep=slept.append)
    assert slept == [10, 20, 40, 30] and (r.attempts, r.waited, r.stop) == (5, 100.0, "deadline")
    assert r.reason == "上限で止めた（方針 test.wait）: 待った 100 秒・照会 5 回・上限 100 秒"


def test_the_query_time_counts_and_no_query_starts_after_the_deadline():
    """照会に 2 秒かかる時計（上限 30 秒・間隔 10 秒）で、照会は 0・12・24・30 秒に打たれる。"""
    c = FakeClock(call_cost=2.0)
    r = waits.wait_until(c.query("pending"), lambda v: False, policy=pol(seconds=30, interval=10), sleep=c.sleep, clock=c)
    assert c.calls == [0, 12, 24, 30] and r.attempts == 4 and r.stop == "deadline"


def test_the_2224_wait_uses_the_whole_limit():
    """#2224 の再現: 上限 1154 秒・間隔が 120 秒まで伸びる待ちが、上限の時刻に最後の照会を打つ。"""
    c = FakeClock(call_cost=1.0)
    policy = limits.policy("test-triage.check-wait")._replace(seconds=1154)
    r = waits.wait_until(c.query("pending"), lambda v: v != "pending", policy=policy, sleep=c.sleep, clock=c)
    assert c.calls[-1] == 1154 and r.waited == 1155 and r.stop == "deadline"
    assert "上限 1154 秒" in r.reason
