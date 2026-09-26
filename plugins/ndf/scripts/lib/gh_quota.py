"""GitHub の 2 つの枠（GraphQL と REST）の使い分けと上限の扱い（#1142 の L0・決定 16・不足 g）。

- `is_rate_limited`: 上限の応答か（`projects-common.sh` の `pj_is_rate_limited` と同じ語）
- `rate_limits`: `rate_limit` の端点で、枠ごとの残りと回復の時刻を読む（この端点は上限に数えられない）
- `with_fallback`: 片方の枠が上限なら、代われる操作をもう片方の枠で行う
- `wait_for_reset`: 代われない操作を、回復の時刻まで待ってからやり直す
- `PollInterval` / `poll_until`: 変化が無い間は問い合わせの間隔を 10 秒から 60 秒へ伸ばし、変化があったら縮める

時刻と待ちは引数で差し替えられる（テストは実際には待たない）。
"""
from __future__ import annotations

import time
from typing import Any, Callable, NamedTuple

import gh_call

_RATE_LIMIT_MARKERS = ("rate limit", "RATE_LIMIT", "unknown owner type")
RESOURCES = ("graphql", "core")  # core は REST の枠
MAX_WAIT = 3600


def is_rate_limited(text: str) -> bool:
    """上限の応答か。`projects-common.sh` の `pj_is_rate_limited` と同じ語で見分ける。

    `unknown owner type` は、GraphQL が上限のときに `gh` が返す誤った文言である（実測）。
    """
    text = str(text or "")
    return any(m in text for m in _RATE_LIMIT_MARKERS) or "rate limit" in text.lower()


class Attempt(NamedTuple):
    """1 回の操作の結果。`value` は成功の値、`error` は失敗の文（成功は空）、`via` は使った枠。"""

    value: Any
    error: str = ""
    via: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def limited(self) -> bool:
        return bool(self.error) and is_rate_limited(self.error)


def rate_limits() -> dict[str, dict[str, int]] | None:
    """`{"graphql": {"remaining", "reset"}, "core": {...}}`。読めなければ `None`。"""
    resp = gh_call.rest("rate_limit")
    res = (resp.body or {}).get("resources") if resp is not None and isinstance(resp.body, dict) else None
    if not isinstance(res, dict):
        return None
    out: dict[str, dict[str, int]] = {}
    for name in RESOURCES:
        r = res.get(name) or {}
        try:
            out[name] = {"remaining": int(r["remaining"]), "reset": int(r["reset"])}
        except (KeyError, TypeError, ValueError):
            continue
    return out or None


def with_fallback(primary: Callable[[], Attempt], alternate: Callable[[], Attempt] | None = None) -> Attempt:
    """`primary` を行い、上限で落ちたら `alternate`（もう片方の枠の同じ操作）を行う。上限以外の失敗はそのまま返す。"""
    first = primary()
    if first.ok or not first.limited or alternate is None:
        return first
    second = alternate()
    if second.ok or not second.limited:
        return second
    return Attempt(None, f"{first.error}\n{second.error}", second.via)


def wait_for_reset(op: Callable[[], Attempt], resource: str = "graphql", *,
                   sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.time,
                   limits: Callable[[], dict | None] = rate_limits, max_wait: float = MAX_WAIT) -> Attempt:
    """`op` を行い、上限で落ちたら `resource` の回復の時刻まで待って 1 回やり直す。

    回復の時刻が読めなければ 60 秒待つ。待ちは `max_wait` 秒を超えない（壊れた時刻で待ち続けない）。
    """
    first = op()
    if first.ok or not first.limited:
        return first
    reset = ((limits() or {}).get(resource) or {}).get("reset")
    wait = (reset - now() + 1) if isinstance(reset, (int, float)) else 60
    sleep(max(1.0, min(float(wait), max_wait)))
    return op()


class PollInterval:
    """待ちの問い合わせの間隔。変化が無い間は `factor` 倍で `cap` まで伸ばし、変化があったら `start` へ戻す。"""

    def __init__(self, start: float = 10, cap: float = 60, factor: float = 2) -> None:
        self.start, self.cap, self.factor = start, cap, factor
        self.current = start

    def next(self, changed: bool) -> float:
        """今回の読みで変化があったかを受け、次に待つ秒を返す。"""
        self.current = self.start if changed else min(self.cap, self.current * self.factor)
        return self.current


def poll_until(path: str, done: Callable[[Any], bool], *, timeout: float,
               interval: PollInterval | None = None, sleep: Callable[[float], None] = time.sleep,
               now: Callable[[], float] = time.time,
               read: Callable[[str], gh_call.RestResponse] | None = None) -> tuple[Any, bool]:
    """`path` を ETag 付きで読み直し、`done(本文)` が真になるまで待つ。`(最後の本文, 届いたか)`。

    変わっていない読み（304）は上限に数えられず、間隔を伸ばす。読めない回は変化なしとして数える。
    """
    interval = interval or PollInterval()
    read = read or gh_call.rest_cached
    deadline = now() + timeout
    body: Any = None
    while True:
        resp = read(path)
        changed = resp.ok and not resp.error and not resp.not_modified
        if resp.ok and not resp.error:
            body = resp.body
            if done(body):
                return body, True
        wait = interval.next(changed)
        if now() + wait > deadline:
            return body, False
        sleep(wait)
