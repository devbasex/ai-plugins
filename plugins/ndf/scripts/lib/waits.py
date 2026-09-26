"""待ちとやり直しの包み（#1142 の決定 19・種類 14）。tenacity を呼ぶのはこのモジュールだけである。

GitHub の上限の待ち（回復の時刻まで）は `gh_quota` が持つ。ここは、それ以外の「条件が揃うまで問い合わせる」
「失敗したらやり直す」を受け持つ。

- `retry_call(fn, should_retry, ...)`: `fn()` の結果（か例外）が `should_retry` に当たる間、間隔を置いてやり直す
- `wait_until(fn, done, ...)`: `done(fn())` が真になるまで問い合わせる。変化が無い間は間隔を伸ばし
  （`factor` 倍・`max_interval` まで）、値が変わったら最初の間隔へ戻す

どちらも待ちの合計（予定した眠りの合計）が `max_wait` を超える前に打ち切る。実時間ではなく眠りの合計で
数えるため、`sleep` を差し替えたテストでも同じ回数で止まる。結果は `Waited`（最後の値・回数・待った秒・揃ったか）。

使う側は `deps.require("waits")` を先に呼ぶ。
"""
from __future__ import annotations

import time
from typing import Any, Callable, NamedTuple, TypeVar

from tenacity import RetryCallState, Retrying, retry_if_exception, retry_if_result

T = TypeVar("T")


class Waited(NamedTuple):
    value: Any
    attempts: int
    waited: float     # 眠った秒の合計
    done: bool        # 条件が揃った（retry_call ではやり直しが要らなくなった）
    error: BaseException | None = None


def _stop_by_idle(max_wait: float) -> Callable[[RetryCallState], bool]:
    def stop(rs: RetryCallState) -> bool:
        return rs.idle_for + (rs.upcoming_sleep or 0.0) > max_wait
    return stop


def retry_call(fn: Callable[[], T], should_retry: Callable[[T], bool] = lambda _v: False, *,
               retry_on: tuple[type[BaseException], ...] = (), max_wait: float = 900.0, interval: float = 30.0,
               sleep: Callable[[float], None] = time.sleep,
               on_wait: Callable[[float, int], None] | None = None) -> Waited:
    """`fn()` を、結果が `should_retry` に当たるか `retry_on` の例外の間、`interval` 秒ごとにやり直す。

    `interval <= 0` はやり直さない。打ち切ったときは最後の結果（例外なら `error`）を返し、`done` は偽。
    `on_wait(秒, 次の回)` は眠る前に呼ぶ（待ちの告知を出す側が使う）。
    """
    if interval <= 0:
        try:
            v = fn()
        except retry_on as exc:  # noqa: B030  空のタプルは何も捕まえない
            return Waited(None, 1, 0.0, False, exc)
        return Waited(v, 1, 0.0, not should_retry(v))

    def before_sleep(rs: RetryCallState) -> None:
        if on_wait is not None:
            on_wait(rs.upcoming_sleep, rs.attempt_number + 1)

    retrying = Retrying(
        retry=retry_if_result(should_retry) | retry_if_exception(lambda e: isinstance(e, retry_on)),
        wait=lambda _rs: interval, stop=_stop_by_idle(max_wait), sleep=sleep,
        before_sleep=before_sleep, retry_error_callback=lambda rs: rs, reraise=False)
    out = retrying(fn)
    stats = retrying.statistics
    attempts, waited = int(stats.get("attempt_number", 1)), float(stats.get("idle_for", 0.0))
    if isinstance(out, RetryCallState):  # 打ち切った
        o = out.outcome
        if o is not None and o.failed:
            return Waited(None, attempts, waited, False, o.exception())
        return Waited(o.result() if o is not None else None, attempts, waited, False)
    return Waited(out, attempts, waited, True)


def wait_until(fn: Callable[[], T], done: Callable[[T], bool], *, max_wait: float, interval: float = 10.0,
               factor: float = 1.5, max_interval: float = 120.0, sleep: Callable[[float], None] = time.sleep,
               same: Callable[[Any, Any], bool] = lambda a, b: a == b,
               on_wait: Callable[[float, int], None] | None = None) -> Waited:
    """`done(fn())` が真になるまで問い合わせる。前の値と `same` なら間隔を `factor` 倍に伸ばす。"""
    state = {"prev": object(), "gap": float(interval)}

    def next_gap(rs: RetryCallState) -> float:
        value = rs.outcome.result() if rs.outcome is not None and not rs.outcome.failed else None
        if same(value, state["prev"]):
            state["gap"] = min(max_interval, state["gap"] * factor)
        else:
            state["gap"] = float(interval)
        state["prev"] = value
        return state["gap"]

    def before_sleep(rs: RetryCallState) -> None:
        if on_wait is not None:
            on_wait(rs.upcoming_sleep, rs.attempt_number + 1)

    retrying = Retrying(retry=retry_if_result(lambda v: not done(v)), wait=next_gap,
                        stop=_stop_by_idle(max_wait), sleep=sleep, before_sleep=before_sleep,
                        retry_error_callback=lambda rs: rs, reraise=True)
    out = retrying(fn)
    stats = retrying.statistics
    attempts, waited = int(stats.get("attempt_number", 1)), float(stats.get("idle_for", 0.0))
    if isinstance(out, RetryCallState):
        return Waited(out.outcome.result() if out.outcome is not None else None, attempts, waited, False)
    return Waited(out, attempts, waited, True)
