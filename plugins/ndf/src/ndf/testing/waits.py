"""待たずに戻ることの確かめ（#1967・#2758・#2964）。

テストは本物の時間を待たない（`tdd-cycle` の「テストに書かないもの」）。「待たずに戻る」ことを確かめるときは、
上限を待った場合にかかる秒に合わせる（`returns_before`）。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager


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
