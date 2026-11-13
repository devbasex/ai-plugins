"""揺れない待ち（#1967・#2758・#2964）。

テストは壁時計の短い上限で結果を決めない。条件（ファイルが現れた・値がそろった）を短い間隔で確かめて待ち、
上限は待ちが壊れたときだけ効く長さにする。「待たずに戻る」ことを確かめるときは、上限を待った場合にかかる秒に
合わせる（`returns_before`）。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")

# 条件が成り立たないときに諦めるまでの秒。高負荷の全件テストでも届く長さにする（成り立てばすぐ戻る）
DEFAULT_TIMEOUT = 60.0
DEFAULT_INTERVAL = 0.01


class WaitTimeout(AssertionError):
    """条件が上限の秒までに成り立たなかった。"""


def wait_until(predicate: Callable[[], T], *, timeout: float = DEFAULT_TIMEOUT, interval: float = DEFAULT_INTERVAL, what: str = "条件") -> T:
    """`predicate` が真の値を返すまで待ち、その値を返す。上限を超えたら `WaitTimeout`。"""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise WaitTimeout(f"{what} が {timeout:.0f} 秒のうちに成り立たなかった")
        time.sleep(interval)


def wait_for_file(path: Path, *, timeout: float = DEFAULT_TIMEOUT, interval: float = DEFAULT_INTERVAL) -> Path:
    """ファイルが現れるまで待つ（シグナルや起動を、ファイルで知らせ合う形で待つ）。"""
    return wait_until(lambda: path if path.exists() else None, timeout=timeout, interval=interval, what=f"{path} の作成")


@contextmanager
def returns_before(would_wait: float) -> Iterator[None]:
    """本文が `would_wait` 秒（待った場合にかかる秒）より前に戻ることを確かめる。

    上限を「待たなかった場合の短い秒」でなく「待った場合の秒」に置くので、高負荷で遅れても落ちず、
    打ち切り・早い戻りが壊れて待ってしまったときだけ落ちる。
    """
    start = time.monotonic()
    yield
    elapsed = time.monotonic() - start
    if elapsed >= would_wait:
        raise AssertionError(f"待たずに戻るはずが {elapsed:.1f} 秒かかった（待った場合の秒: {would_wait:.1f}）")
