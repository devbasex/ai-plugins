"""待ちとやり直しの包み（lib/waits.py・#1142 の決定 19）。外部パッケージは全体テストの環境（根の pyproject.toml）が入れる。

眠りは差し替え、実時間を使わない。回数と待ちの合計は `post_queue.retry`（待ちの合計 + 間隔が上限を超えたら打ち切る）と同じ。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import tenacity  # noqa: E402,F401  包みの外部パッケージ。無ければ集めるところで落とす
import waits  # noqa: E402


def test_retry_call_stops_before_the_wait_would_exceed_the_limit():
    slept, told = [], []
    r = waits.retry_call(lambda: "limit", lambda v: v == "limit", max_wait=90, interval=30,
                         sleep=slept.append, on_wait=lambda s, n: told.append((s, n)))
    assert r == waits.Waited("limit", 4, 90.0, False) and slept == [30, 30, 30]
    assert told == [(30, 2), (30, 3), (30, 4)]


def test_retry_call_returns_the_first_good_result_and_retries_listed_errors():
    values = iter(["limit", "ok"])
    assert waits.retry_call(lambda: next(values), lambda v: v == "limit", interval=5,
                            sleep=lambda s: None) == waits.Waited("ok", 2, 5.0, True)

    def boom():
        raise OSError("x")

    r = waits.retry_call(boom, retry_on=(OSError,), max_wait=10, interval=5, sleep=lambda s: None)
    assert (r.attempts, r.done, type(r.error)) == (3, False, OSError)
    with pytest.raises(ZeroDivisionError):
        waits.retry_call(lambda: 1 / 0, retry_on=(OSError,), sleep=lambda s: None)


def test_zero_interval_runs_once():
    assert waits.retry_call(lambda: "limit", lambda v: True, interval=0) == waits.Waited("limit", 1, 0.0, False)


def test_wait_until_grows_the_gap_while_nothing_changes_and_resets_on_change():
    values, slept = iter([1, 1, 1, 2, 2, 3]), []
    r = waits.wait_until(lambda: next(values), lambda v: v == 3, max_wait=1000, interval=10, factor=2,
                         sleep=slept.append)
    assert r == waits.Waited(3, 6, 100.0, True) and slept == [10, 20, 40, 10, 20]
    slept = []
    r = waits.wait_until(lambda: 1, lambda v: False, max_wait=100, interval=10, factor=2, max_interval=40,
                         sleep=slept.append)
    assert r == waits.Waited(1, 4, 70.0, False) and slept == [10, 20, 40]
