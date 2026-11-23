"""GitHub の二次レート制限の見分けと、待ちの終わりの共有（#4016 の決定 7・8。`github_limits` と `github` の口）。

githubkit へ `httpx.MockTransport` を渡し、ネットワークへ出ない。書き込みの順番の記録は `tmp_path` に置き、本物のホームの下
（`~/.local/state/ndf-github/`）を書かない（`transport` を渡す `client()` は `gate` を省けば順番を取らない）。
"""

from __future__ import annotations

import json
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from githubkit.exception import RequestFailed, SecondaryRateLimitExceeded
from githubkit.response import Response
from githubkit.throttling import LocalThrottler

from ndf.adapters import github, github_limits, write_gate
from ndf.adapters.github_limits import Limit, limit_of, reasons_of

from ._github_helpers import offline_client

REPO = "devbasex/ai-plugins"
NOW = 1_000_000.0
SECONDARY = "You have exceeded a secondary rate limit."
FIRST_WAIT = github_limits.backoff(0)


class _Gate:
    """`WriteGate.block` の呼び出しを覚える偽物。"""

    def __init__(self) -> None:
        self.blocked: list[float] = []

    def block(self, seconds: float) -> None:
        self.blocked.append(seconds)


# limit_of


@pytest.mark.parametrize(
    ("status", "headers", "message", "expected"),
    [
        (403, {"retry-after": "30"}, "", Limit(30.0, True)),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 45)}, SECONDARY, Limit(45.0, True)),
        (200, {}, SECONDARY, Limit(FIRST_WAIT, False)),
        (200, {}, "was submitted too quickly", Limit(FIRST_WAIT, False)),
        # 一次の上限の情報として付く reset だけでは、GitHub が待ちを示したとみなさない（r1-14）
        (403, {"x-ratelimit-remaining": "12", "x-ratelimit-reset": str(int(NOW) + 45)}, SECONDARY, Limit(FIRST_WAIT, False)),
        (429, {}, SECONDARY, Limit(FIRST_WAIT, False)),
        (403, {"retry-after": "soon"}, SECONDARY, Limit(FIRST_WAIT, False)),
    ],
)
def test_limit_of_tells_the_secondary_limit_and_its_wait(status, headers, message, expected) -> None:
    assert limit_of(status, headers, message, NOW) == expected


@pytest.mark.parametrize(
    ("status", "headers", "message"),
    [
        (403, {}, "Resource not accessible by integration"),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 45)}, "API rate limit exceeded"),
        (403, {"retry-after": "soon"}, "forbidden"),
        (403, {}, "was submitted too quickly"),
        (500, {}, SECONDARY),
        (200, {}, "Could not resolve to a node"),
    ],
)
def test_limit_of_is_none_for_other_failures(status, headers, message) -> None:
    assert limit_of(status, headers, message, NOW) is None


# reasons_of


def test_reasons_of_reads_the_message_and_every_error() -> None:
    body = {"message": "Validation Failed", "errors": ["pull_request_review_thread.line must be part of the diff"]}
    assert reasons_of(json.dumps(body)) == ("Validation Failed", "pull_request_review_thread.line must be part of the diff")


def test_reasons_of_keeps_a_reason_past_the_first_300_characters() -> None:
    errors = ["x" * 400, {"message": "could not be resolved", "code": "custom", "field": "path", "resource": "PullRequestReviewComment"}]
    text = json.dumps({"message": "Validation Failed", "errors": errors})
    assert text.index("could not be resolved") > 300
    assert reasons_of(text)[-1] == "could not be resolved custom path PullRequestReviewComment"


def test_reasons_of_returns_the_whole_body_when_it_is_not_json() -> None:
    assert reasons_of("<html>bad gateway</html>") == ("<html>bad gateway</html>",)


# client


def test_client_with_a_transport_takes_no_gate_unless_given(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    assert isinstance(github.client(auth="t", transport=transport).config.throttler, LocalThrottler)

    gate = write_gate.WriteGate(tmp_path / write_gate.GATE_FILE)
    api = github.client(auth="t", transport=transport, gate=gate)
    assert api.config.throttler is gate and github.GitHub(REPO, api).gate is gate


def test_client_without_a_transport_uses_the_machine_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = write_gate.WriteGate(tmp_path / write_gate.GATE_FILE)
    monkeypatch.setattr(write_gate, "machine_gate", lambda: gate)
    assert github.client(auth="t").config.throttler is gate


# _translated（GitHub.rest）


def _rest_failure(response: httpx.Response) -> tuple[github.GitHubError, _Gate]:
    gate = _Gate()
    hub = github.GitHub(REPO, offline_client(lambda request: response), gate=gate)  # type: ignore[arg-type]
    with pytest.raises(github.GitHubError) as err:
        hub.rest("POST", f"/repos/{REPO}/issues", {"title": "t"})
    return err.value, gate


def test_a_secondary_limit_with_retry_after_is_hinted_and_shared() -> None:
    error, gate = _rest_failure(httpx.Response(403, json={"message": SECONDARY}, headers={"retry-after": "30"}))
    assert (error.secondary, error.hinted, error.retry_after, error.quota) == (True, True, 30.0, github.REST)
    assert gate.blocked == [30.0]


@pytest.mark.parametrize("status", [403, 429])
def test_a_secondary_limit_told_only_by_the_body_waits_the_first_backoff(status: int) -> None:
    # 一次の残量が正で `retry-after` の無い 403 は githubkit の通常の `RequestFailed` になる
    error, gate = _rest_failure(httpx.Response(status, json={"message": SECONDARY}, headers={"x-ratelimit-remaining": "12"}))
    assert (error.secondary, error.hinted, error.retry_after) == (True, False, FIRST_WAIT)
    assert gate.blocked == [FIRST_WAIT]


def test_a_refusal_without_the_phrase_is_not_a_secondary_limit() -> None:
    error, gate = _rest_failure(httpx.Response(403, json={"message": "Resource not accessible by integration"}))
    assert (error.secondary, error.retry_after, error.reasons) == (False, None, ("Resource not accessible by integration",))
    assert gate.blocked == []


def test_a_refusal_keeps_every_reason_while_the_message_is_cut() -> None:
    body = {"message": "Validation Failed", "errors": ["y" * 400, "line must be part of the diff"]}
    error, _ = _rest_failure(httpx.Response(422, json=body))
    assert "line must be part of the diff" not in str(error)
    assert error.reasons[-1] == "line must be part of the diff"


# GitHub.graphql


def _graphql_failure(answer: dict) -> tuple[github.GitHubError, _Gate]:
    gate = _Gate()
    hub = github.GitHub(REPO, offline_client(lambda request: httpx.Response(200, json=answer)), gate=gate)  # type: ignore[arg-type]
    with pytest.raises(github.GitHubError) as err:
        hub.graphql("mutation { addComment(input: {}) { x } }", {})
    return err.value, gate


@pytest.mark.parametrize("kind", ["FORBIDDEN", "RATE_LIMITED"])
def test_graphql_secondary_limit_on_http_200_is_shared(kind: str) -> None:
    error, gate = _graphql_failure({"data": None, "errors": [{"type": kind, "message": SECONDARY}]})
    assert (error.secondary, error.hinted, error.quota) == (True, False, github.GRAPHQL)
    assert gate.blocked == [error.retry_after]


def test_graphql_other_errors_stay_as_they_are() -> None:
    error, gate = _graphql_failure({"data": None, "errors": [{"type": "FORBIDDEN", "message": "nope"}]})
    assert (str(error), error.secondary, error.retry_after) == ("GraphQL の誤り: nope", False, None)
    assert gate.blocked == []


def test_graphql_primary_limit_stays_a_primary_limit() -> None:
    error, gate = _graphql_failure({"data": None, "errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]})
    assert (error.secondary, error.quota) == (False, github.GRAPHQL) and error.retry_after is not None
    assert gate.blocked == []


# RetryWithin


def _failed(status: int, headers: dict[str, str], message: str, secondary: bool) -> RequestFailed:
    request = httpx.Request("POST", f"https://api.github.com/repos/{REPO}/issues")
    response = Response(httpx.Response(status, json={"message": message}, headers=headers, request=request), object)
    return SecondaryRateLimitExceeded(response, timedelta(seconds=30)) if secondary else RequestFailed(response)


def test_retry_within_shares_the_secondary_limit_even_when_it_gives_up() -> None:
    gate = _Gate()
    decide = github.RetryWithin(remaining=lambda: 0.0, gate=gate)  # type: ignore[arg-type]
    assert not decide(_failed(403, {"retry-after": "30"}, SECONDARY, True), 0).do_retry
    # 本文だけで見分けた 403（githubkit は打ち直さない）。待ちの秒は打ち直しの回数で延ばす
    assert not decide(_failed(403, {"x-ratelimit-remaining": "12"}, SECONDARY, False), 1).do_retry
    assert gate.blocked == [30.0, github_limits.backoff(1)]


def test_retry_within_does_not_share_a_primary_limit() -> None:
    gate = _Gate()
    decide = github.RetryWithin(remaining=lambda: 0.0, gate=gate)  # type: ignore[arg-type]
    reset = str(int(time.time()) + 600)
    decide(_failed(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}, "API rate limit exceeded", False), 0)
    assert gate.blocked == []
