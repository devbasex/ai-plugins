"""待ちとやり直しの包み（#1142 の決定 19・種類 14、#2343 の決定 5〜9）。tenacity を呼ぶのはこのモジュールだけである。

GitHub の上限の待ち（回復の時刻まで）は `gh_quota` が持つ。ここは、それ以外の「条件が揃うまで問い合わせる」
「失敗したらやり直す」を受け持つ。上限・間隔・回数は上限の宣言の方針（`limits.Policy`）で受け取り、数字の既定値を持たない。

- `retry_call(fn, should_retry, policy=...)`: `fn()` の結果（か例外）が `should_retry` に当たる間、方針の `interval` 秒おきに
  やり直す。同じ原因の鍵（`cause_key`）の失敗が方針の `same_cause` に達したら、打った回数が `attempts` に達したら止める
- `wait_until(fn, done, policy=...)`: `done(fn())` が真になるまで問い合わせる。変化が無い間は間隔を伸ばし
  （`factor` 倍・`max_interval` まで）、値が変わったら最初の間隔へ戻す

どちらも期限（開始の単調時計の値 + 方針の `seconds`）で止まる。残りの秒は期限から今の時計の値を引いたもので、
眠りと照会・呼び出しの時間の両方を含む。次の眠りが残りの秒を超えるときは残りの秒だけ眠り、期限の時刻に最後の 1 回を
打ってから止まる（#2224）。`sleep` だけを差し替えたテストでも同じ回数で止まるよう、経った秒は時計の差と眠りの合計の
大きい方で数える。結果は `Waited`（最後の値・回数・待った秒・揃ったか・例外・止まり方・原因の鍵・その回数・理由の文）。

使う側は `deps.require("waits")` を先に呼ぶ。方針が値を欠くときは `limits.LimitsError`。
"""

from __future__ import annotations

import time
from typing import Any, Callable, NamedTuple, Optional, TypeVar

import cause_key
import limits
from tenacity import RetryCallState, Retrying, retry_if_exception, retry_if_result

T = TypeVar("T")


class Waited(NamedTuple):
    value: Any
    attempts: int
    waited: float  # 開始から止めた時点までの秒
    done: bool  # 条件が揃った（retry_call ではやり直しが要らなくなった）
    error: BaseException | None = None
    stop: str = "done"  # 止まり方: done / deadline / same-cause / attempts
    cause: Optional[str] = None  # 最後の失敗の原因の鍵
    same: int = 0  # その鍵の失敗の回数
    reason: str = ""  # 止まった理由の文（揃ったときは空）


def _need(policy: limits.Policy, key: str):
    got = getattr(policy, key)
    if got is None:
        raise limits.LimitsError(f"方針 {policy.name} に {key} が無い（待ちの部品が要る値）")
    return got


class _Run:
    """1 回の呼び出しの期限・経った秒・原因ごとの回数。"""

    def __init__(self, policy: limits.Policy, clock: Callable[[], float]):
        self.policy, self.clock = policy, clock
        self.limit = float(_need(policy, "seconds"))
        self.started = clock()
        self.slept = 0.0
        self.counts: dict[Optional[str], int] = {}
        self.cause: Optional[str] = None
        self.stop = "deadline"

    def elapsed(self) -> float:
        return max(self.clock() - self.started, self.slept)

    def left(self) -> float:
        return self.limit - self.elapsed()

    def count(self, key: Optional[str]) -> int:
        self.cause = key
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def reason(self, attempts: int) -> str:
        name = self.policy.name
        if self.stop == "same-cause":
            return f"同じ原因（{cause_key.brief(self.cause)}）で {self.counts.get(self.cause, 0)} 回失敗した（方針 {name}）"
        if self.stop == "attempts":
            return f"打つ回数の上限 {attempts} 回に達した（方針 {name}・最後の原因 {cause_key.brief(self.cause)}）"
        return f"上限で止めた（方針 {name}）: 待った {self.elapsed():g} 秒・照会 {attempts} 回・上限 {self.limit:g} 秒"

    def waited(self, value, attempts: int, done: bool, error=None) -> Waited:
        if done:
            return Waited(value, attempts, self.elapsed(), True, error, "done", self.cause, self.counts.get(self.cause, 0))
        return Waited(value, attempts, self.elapsed(), False, error, self.stop, self.cause, self.counts.get(self.cause, 0), self.reason(attempts))


def _key_of(rs: RetryCallState, reason: Optional[Callable[[Any], str]]) -> Optional[str]:
    o = rs.outcome
    if o is None:
        return None
    if o.failed:
        return cause_key.of_exception(o.exception())
    v = o.result()
    return cause_key.make("result", reason(v) if reason is not None else str(v))


def _waited_of(retrying: Retrying, out, run: _Run) -> Waited:
    attempts = int(retrying.statistics.get("attempt_number", 1))
    if isinstance(out, RetryCallState):  # 打ち切った
        o = out.outcome
        if o is not None and o.failed:
            return run.waited(None, attempts, False, o.exception())
        return run.waited(o.result() if o is not None else None, attempts, False)
    return run.waited(out, attempts, True)


def _announcer(on_wait: Callable[[float, int], None] | None) -> Callable[[RetryCallState], None]:
    def before_sleep(rs: RetryCallState) -> None:
        if on_wait is not None:
            on_wait(rs.upcoming_sleep, rs.attempt_number + 1)

    return before_sleep


def _sleeper(run: _Run, sleep: Callable[[float], None]) -> Callable[[float], None]:
    def go(seconds: float) -> None:
        run.slept += seconds
        sleep(seconds)

    return go


def retry_call(
    fn: Callable[[], T],
    should_retry: Callable[[T], bool] = lambda _v: False,
    *,
    policy: limits.Policy,
    retry_on: tuple[type[BaseException], ...] = (),
    reason: Callable[[T], str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    on_wait: Callable[[float, int], None] | None = None,
) -> Waited:
    """`fn()` を、結果が `should_retry` に当たるか `retry_on` の例外の間、方針の `interval` 秒ごとにやり直す。

    方針の `interval` が 0 ならやり直さない。打ち切ったときは最後の結果（例外なら `error`）と理由を返し、`done` は偽。
    `reason(値)` は失敗の理由の文で、原因の鍵を作るのに使う（省くと `str(値)`）。
    `on_wait(秒, 次の回)` は眠る前に呼ぶ（待ちの告知を出す側が使う）。
    """
    run = _Run(policy, clock)
    interval = float(_need(policy, "interval"))
    attempts_cap, same_cap = policy.attempts, policy.same_cause
    if interval <= 0:
        try:
            v = fn()
        except retry_on as exc:  # noqa: B030  空のタプルは何も捕まえない
            run.count(cause_key.of_exception(exc))
            return run.waited(None, 1, False, exc)
        if should_retry(v):
            run.count(cause_key.make("result", reason(v) if reason is not None else str(v)))
            return run.waited(v, 1, False)
        return run.waited(v, 1, True)

    def stop(rs: RetryCallState) -> bool:
        same = run.count(_key_of(rs, reason))
        if same_cap is not None and same >= same_cap:
            run.stop = "same-cause"
            return True
        if attempts_cap is not None and rs.attempt_number >= attempts_cap:
            run.stop = "attempts"
            return True
        run.stop = "deadline"
        return run.left() <= 0

    retrying = Retrying(
        retry=retry_if_result(should_retry) | retry_if_exception(lambda e: isinstance(e, retry_on)),
        wait=lambda _rs: max(0.0, min(interval, run.left())),
        stop=stop,
        sleep=_sleeper(run, sleep),
        before_sleep=_announcer(on_wait),
        retry_error_callback=lambda rs: rs,
        reraise=False,
    )
    return _waited_of(retrying, retrying(fn), run)


def wait_until(
    fn: Callable[[], T],
    done: Callable[[T], bool],
    *,
    policy: limits.Policy,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    same: Callable[[Any, Any], bool] = lambda a, b: a == b,
    on_wait: Callable[[float, int], None] | None = None,
) -> Waited:
    """`done(fn())` が真になるまで問い合わせる。前の値と `same` なら間隔を方針の `factor` 倍（`max_interval` まで）に伸ばす。"""
    run = _Run(policy, clock)
    interval = float(_need(policy, "interval"))
    factor = float(policy.factor if policy.factor is not None else 1)
    cap = float(policy.max_interval) if policy.max_interval is not None else float("inf")  # 無ければ期限だけが抑える
    state = {"prev": object(), "gap": interval}

    def next_gap(rs: RetryCallState) -> float:
        value = rs.outcome.result() if rs.outcome is not None and not rs.outcome.failed else None
        if same(value, state["prev"]):
            state["gap"] = min(cap, state["gap"] * factor)
        else:
            state["gap"] = interval
        state["prev"] = value
        return max(0.0, min(state["gap"], run.left()))

    retrying = Retrying(
        retry=retry_if_result(lambda v: not done(v)),
        wait=next_gap,
        stop=lambda _rs: run.left() <= 0,
        sleep=_sleeper(run, sleep),
        before_sleep=_announcer(on_wait),
        retry_error_callback=lambda rs: rs,
        reraise=True,
    )
    return _waited_of(retrying, retrying(fn), run)
