"""GitHub の上限の見分けと待ちの秒（#4016 の決定 7・8。`github.py` から分けた）。

- 二次レート制限の見分けと待ちの秒は `limit_of` の 1 か所で決める。REST の 403・429 の応答も、GraphQL の HTTP 200 の誤りも
  ここを通す。GitHub が待ちの秒（`retry-after`、または一次の残量 0 と `x-ratelimit-reset`）を示したときだけ `hinted` を真にし、
  示さなければ `cross-review.secondary-rate-limit` の指数的な待ち（`routing.backoff`）の 1 回目の秒にする
- 応答のフック（`watcher`）は、httpx が応答を返す前（送信のロックを放す前）に二次レート制限を見分けて待ちの終わりを
  記録する。後続の書き込みが古い記録を読んで送らない（HTTP 200 の GraphQL の誤りも）
- githubkit の打ち直しの判断（`RetryWithin`）は、二次レート制限なら打ち直すかに依らず（最後の試行でも）待ちの終わりを
  `WriteGate.block` でマシン全体へ知らせる
- 拒否の理由（`reasons_of`）は応答の本文の全体から読み、切り詰めない
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from http import HTTPStatus
from typing import Any, NamedTuple, Optional

import httpx
from githubkit.exception import GitHubException, RateLimitExceeded, RequestError, RequestFailed, SecondaryRateLimitExceeded
from githubkit.retry import RETRY_DEFAULT, RetryOption

from ndf.adapters import write_gate
from ndf.adapters.write_gate import WriteGate
from ndf.contracts import limits, routing

# 上限に当たったスロット（`GitHubError.quota`）
REST, GRAPHQL = "rest", "graphql"
# 二次レート制限を返す HTTP の状態（REST）
LIMIT_STATUSES = (HTTPStatus.FORBIDDEN, HTTPStatus.TOO_MANY_REQUESTS)
# REST の `message`（JSON でなければ本文）と、GraphQL の誤りの `message` が二次レート制限を表す語（小文字で比べる）。
# REST は githubkit の見分け（`secondary rate`）とそろえる
REST_PHRASES = ("secondary rate",)
GRAPHQL_PHRASES = ("secondary rate limit", "submitted too quickly")
# `errors[]` の辞書から理由の文へつなぐ欄
REASON_KEYS = ("message", "code", "field", "resource")


class Failure(NamedTuple):
    """`github.GitHubError` へ写す失敗（`GitHubError.of(failure)` で作る）。"""

    detail: str
    status: int = 0
    retry_after: Optional[float] = None
    quota: Optional[str] = None
    unreached: bool = False
    secondary: bool = False
    hinted: bool = False
    reasons: tuple[str, ...] = ()


class Limit(NamedTuple):
    """二次レート制限の待ちの秒と、GitHub が待ちの秒を示したか。"""

    seconds: float
    hinted: bool


def backoff(attempt: int) -> float:
    """GitHub が待ちの秒を示さないときの、`attempt` 回目（0 から）の待ちの秒（`interval × factor^attempt`、頭打ちあり）。

    方針は cross-review の打ち直し（`throttle_decide.wait_before_retry`）と共有する `cross-review.secondary-rate-limit`。
    """
    return routing.backoff(limits.policy("cross-review.secondary-rate-limit"), attempt, None, None)


def _number(headers: Mapping[str, str], name: str) -> Optional[float]:
    """ヘッダーの整数の値。無い・数でなければ None（そのヘッダーが無いとして扱う）。"""
    raw = headers.get(name)
    if raw is None or not (raw.isascii() and raw.isdigit()):
        return None
    return float(int(raw))


def _hint(headers: Mapping[str, str], now: float) -> Optional[float]:
    """GitHub が示した待ちの秒。`retry-after`、無ければ一次の残量が 0 のときの `x-ratelimit-reset` までの秒。"""
    retry = _number(headers, "retry-after")
    if retry is not None:
        return retry
    reset = _number(headers, "x-ratelimit-reset")
    if headers.get("x-ratelimit-remaining") == "0" and reset is not None:
        return max(0.0, reset - now)
    return None


def limit_of(status: int, headers: Mapping[str, str], message: str, now: float, known: bool = False) -> Optional[Limit]:
    """二次レート制限なら待ちの秒と `hinted`、ほかは None。

    REST は 403・429 で、`known`（githubkit が `SecondaryRateLimitExceeded` と見分けた）か、数の `retry-after` があるか、
    `message`（JSON の `message`。`rest_message` が取り出す）に `secondary rate` があるもの（一次の残量が正で `retry-after` の
    無い通常の `RequestFailed` も）。GraphQL は HTTP 200 の誤りの `message` に `secondary rate limit` か `submitted too quickly` が
    あるもの。`x-ratelimit-reset` だけでは `hinted` を真にしない（GitHub は一次の上限の情報としてほぼすべての応答に付ける）。
    """
    text = message.lower()
    if status in LIMIT_STATUSES:
        limited = known or _number(headers, "retry-after") is not None or any(p in text for p in REST_PHRASES)
    else:
        limited = status == HTTPStatus.OK and any(p in text for p in GRAPHQL_PHRASES)
    if not limited:
        return None
    hint = _hint(headers, now)
    return Limit(hint, True) if hint is not None else Limit(backoff(0), False)


def _parsed(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:  # 本文が JSON でなければ本文の全体を理由にする
        return text


def _reason(item: Any) -> str:
    if isinstance(item, dict):
        return " ".join(str(item[key]) for key in REASON_KEYS if item.get(key) is not None)
    return str(item)


def reasons_of(text: str) -> tuple[str, ...]:
    """応答の本文から拒否の理由を順に返す（`message` と `errors[]` のそれぞれ）。JSON でなければ本文の全体を 1 つ。切り詰めない。"""
    body = _parsed(text)
    if not isinstance(body, dict):
        return (text,)
    found = [body["message"]] if isinstance(body.get("message"), str) else []
    errors = body.get("errors")
    if isinstance(errors, list):
        found += [_reason(e) for e in errors]
    return tuple(found)


def rest_message(text: str) -> str:
    """REST の拒否の本文のうち、二次レート制限の語を探す文。JSON なら `message`（無ければ空）、JSON でなければ本文の全体。"""
    body = _parsed(text)
    if isinstance(body, dict):
        return body["message"] if isinstance(body.get("message"), str) else ""
    return text if isinstance(body, str) else ""


def graphql_messages(text: str) -> str:
    """GraphQL の応答の本文の誤りの `message` を `; ` でつないだ文。誤りが無い・JSON でなければ空。"""
    body = _parsed(text)
    errors = body.get("errors") if isinstance(body, dict) else None
    if not isinstance(errors, list):
        return ""
    return "; ".join(str(e["message"]) for e in errors if isinstance(e, dict) and e.get("message") is not None)


def limit_of_failure(exc: RequestFailed, now: float) -> Optional[Limit]:
    """githubkit の失敗（`SecondaryRateLimitExceeded` も通常の `RequestFailed` も）の二次レート制限。"""
    response = exc.response
    known = isinstance(exc, SecondaryRateLimitExceeded)
    return limit_of(response.status_code, response.headers, rest_message(response.raw_response.text), now, known)


def limit_of_response(response: httpx.Response, now: float) -> Optional[Limit]:
    """httpx の応答（本文を読んだもの）の二次レート制限。HTTP 200 は GraphQL への POST の誤りだけを見る。"""
    status = response.status_code
    if status in LIMIT_STATUSES:
        return limit_of(status, response.headers, rest_message(response.text), now)
    request = response.request
    if status != HTTPStatus.OK or request.method != "POST" or request.url.path != write_gate.GRAPHQL_PATH:
        return None
    messages = graphql_messages(response.text)
    return limit_of(status, response.headers, messages, now) if messages else None


def _watched(response: httpx.Response) -> bool:
    """本文を読んで見分ける応答か（REST の 403・429 と、GraphQL への POST の 200）。"""
    request = response.request
    if response.status_code in LIMIT_STATUSES:
        return True
    return response.status_code == HTTPStatus.OK and request.method == "POST" and request.url.path == write_gate.GRAPHQL_PATH


def _record(gate: WriteGate, response: httpx.Response) -> None:
    limit = limit_of_response(response, time.time())
    if limit is not None:
        gate.block(limit.seconds)


def watcher(gate: WriteGate) -> Callable[[httpx.Response], None]:
    """httpx の応答のフック。二次レート制限なら、応答を返す前（`WriteGate` の送信のロックを放す前）に待ちの終わりを記録する。"""

    def watch(response: httpx.Response) -> None:
        if _watched(response):
            response.read()
            _record(gate, response)

    return watch


def async_watcher(gate: WriteGate) -> Callable[[httpx.Response], Awaitable[None]]:
    """`watcher` の非同期版。記録は送信のロックを持つ手順と同じ専用のスレッド（`write_gate._FINISHER`）で書く。"""

    async def watch(response: httpx.Response) -> None:
        if _watched(response):
            await response.aread()
            await asyncio.get_running_loop().run_in_executor(write_gate._FINISHER, _record, gate, response)

    return watch


def hooks(gate: Optional[WriteGate]) -> dict[str, Any]:
    """githubkit のクライアントへ渡す応答のフック（`gate` が無ければ空）。"""
    if gate is None:
        return {}
    return {"event_hooks": {"response": [watcher(gate)]}, "async_event_hooks": {"response": [async_watcher(gate)]}}


class RetryWithin:
    """githubkit の再試行の判断（既定の判断に、上限の待ちの残りを足す）。

    githubkit の既定は上限（rate limit）の解除の時刻まで同期で眠るため、待ちの予算を持つ呼び手（CI の監視）が
    数十分戻らない。解除が `remaining`（呼んだ時点で待ちに使える残りの秒）を越える上限は再試行せず、
    `GitHubError` の `retry_after` で呼び手へ返す。残りは呼び手が自分の予算の数え方で返す（`Watch.remaining` は
    ランナーの割り当ての待ちを予算に数えない）。サーバーの誤り（5xx）の再試行は既定のまま。
    二次レート制限なら、打ち直すかに依らず `gate.block` で待ちの終わりをマシン全体へ知らせる（決定 7）。
    """

    def __init__(self, remaining: Callable[[], float], gate: Optional[WriteGate] = None) -> None:
        self.remaining = remaining
        self.gate = gate

    def __call__(self, exc: GitHubException, retry_count: int) -> RetryOption:
        if self.gate is not None and isinstance(exc, RequestFailed):
            limit = limit_of_failure(exc, time.time())
            if limit is not None:
                self.gate.block(limit.seconds if limit.hinted else backoff(retry_count))
        option = RETRY_DEFAULT(exc, retry_count)
        if option.do_retry and isinstance(exc, RateLimitExceeded):
            if exc.retry_after.total_seconds() > self.remaining():
                return RetryOption(False)
        return option


def no_wait() -> float:
    """上限の待ちに使える残りの秒（0。上限を再試行しない）。"""
    return 0.0


def quota_of(label: str) -> str:
    """要求の名前（`GET /path`・`graphql`）から、上限に数えるスロット。"""
    return GRAPHQL if label == GRAPHQL else REST


def failure_of(exc: RequestFailed | RequestError, label: str, detail_body: bool, now: float) -> Failure:
    """githubkit の失敗の写し。上限（`RateLimitExceeded`）と二次レート制限（本文で見分けた通常の 403 も）は回復までの秒を、
    HTTP の拒否は状態を添える。`detail_body` なら拒否の文へ応答の本文の先頭 300 字を足す。理由の全文は `reasons` が持つ。"""
    if not isinstance(exc, RequestFailed):
        return Failure(f"GitHub へ届かない: {label}: {exc}", unreached=True)
    status, text = exc.response.status_code, exc.response.raw_response.text
    reasons, limit = reasons_of(text), limit_of_failure(exc, now)
    if limit is not None:  # 二次の上限は待てば戻る（#3941）
        detail = f"GitHub の上限（{quota_of(label)} のスロット・secondary rate limit）: {label}"
        return Failure(detail, status, limit.seconds, quota_of(label), secondary=True, hinted=limit.hinted, reasons=reasons)
    if isinstance(exc, RateLimitExceeded):
        detail = f"GitHub の上限（{quota_of(label)} のスロット）: {label}"
        return Failure(detail, status, exc.retry_after.total_seconds(), quota_of(label), reasons=reasons)
    body = f": {text[:300]}" if detail_body else ""
    return Failure(f"GitHub が断った（{status}）: {label}{body}", status, reasons=reasons)


def graphql_failure(messages: str, headers: Mapping[str, str]) -> Failure:
    """GraphQL の HTTP 200 の誤り（`messages` は誤りの `message` を `; ` でつないだ文）の写し。二次レート制限なら上限として写す。"""
    limit = limit_of(HTTPStatus.OK, headers, messages, time.time())
    if limit is None:
        return Failure(f"GraphQL の誤り: {messages}")
    detail = f"GitHub の上限（{GRAPHQL} のスロット・secondary rate limit）: {GRAPHQL}: {messages}"
    return Failure(detail, 0, limit.seconds, GRAPHQL, secondary=True, hinted=limit.hinted, reasons=(messages,))


def graphql_limited(exc: RateLimitExceeded) -> Optional[Failure]:
    """githubkit が GraphQL の HTTP 200 の誤り（`RATE_LIMITED`）から作った上限が二次レート制限なら、その写し。ほかは None。"""
    if exc.response.status_code != HTTPStatus.OK:
        return None
    messages = "; ".join(e.message for e in exc.response.parsed_data.errors)
    failure = graphql_failure(messages, exc.response.headers)
    return failure if failure.secondary else None


def tell(gate: Optional[WriteGate], secondary: bool, retry_after: Optional[float]) -> None:
    """二次レート制限なら、待ちの終わりを `gate` へ知らせる（決定 7）。"""
    if secondary and retry_after is not None and gate is not None:
        gate.block(retry_after)
