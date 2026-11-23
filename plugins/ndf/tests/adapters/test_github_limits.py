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

from ndf import slot_pool
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


def test_the_phrase_outside_the_json_message_is_not_a_secondary_limit() -> None:
    # PR 4265 の r1-3: JSON の応答は `message` だけを見て判定する
    body = {"message": "Resource not accessible by integration", "errors": [SECONDARY], "documentation_url": "secondary rate limit"}
    error, gate = _rest_failure(httpx.Response(403, json=body))
    assert (error.secondary, error.retry_after) == (False, None)
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


def test_graphql_secondary_limit_on_http_200_reads_the_retry_after_of_the_response() -> None:
    # PR 4265 の r1-2: githubkit が `GraphQLFailed` に変える前の応答のヘッダーで待ちの秒を決める
    gate = _Gate()
    answer = {"data": None, "errors": [{"type": "FORBIDDEN", "message": SECONDARY}]}
    response = httpx.Response(200, json=answer, headers={"retry-after": "120"})
    hub = github.GitHub(REPO, offline_client(lambda request: response), gate=gate)  # type: ignore[arg-type]
    with pytest.raises(github.GitHubError) as err:
        hub.graphql("mutation { addComment(input: {}) { x } }", {})
    assert (err.value.secondary, err.value.hinted, err.value.retry_after) == (True, True, 120.0)
    assert gate.blocked == [120.0]


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


def test_a_secondary_limit_told_by_githubkit_is_shared_without_the_phrase() -> None:
    # PR 4265 の r1-3: githubkit が `SecondaryRateLimitExceeded` と見分けた応答は、語や数の `retry-after` が無くても二次レート制限
    limit = github_limits.limit_of_failure(_failed(403, {"retry-after": "soon"}, "forbidden", True), NOW)
    assert limit == Limit(FIRST_WAIT, False)


def test_retry_within_does_not_share_a_primary_limit() -> None:
    gate = _Gate()
    decide = github.RetryWithin(remaining=lambda: 0.0, gate=gate)  # type: ignore[arg-type]
    reset = str(int(time.time()) + 600)
    decide(_failed(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}, "API rate limit exceeded", False), 0)
    assert gate.blocked == []


# スロットの読み替え（PR 4265 の r1-4）


def _counted(handler) -> tuple[github.GitHub, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return github.GitHub(REPO, offline_client(record), gate=_Gate()), seen  # type: ignore[arg-type]


def test_pulls_does_not_read_again_through_rest_on_a_secondary_limit() -> None:
    answer = {"data": None, "errors": [{"type": "FORBIDDEN", "message": SECONDARY}]}
    hub, seen = _counted(lambda request: httpx.Response(200, json=answer))
    with pytest.raises(github.GitHubError) as err:
        hub.pulls([1, 2, 3])
    assert err.value.secondary and [r.url.path for r in seen] == ["/graphql"]


def test_pull_does_not_read_again_through_graphql_on_a_secondary_limit() -> None:
    hub, seen = _counted(lambda request: httpx.Response(403, json={"message": SECONDARY}, headers={"x-ratelimit-remaining": "12"}))
    with pytest.raises(github.GitHubError) as err:
        hub.pull(7)
    assert err.value.secondary and [r.url.path for r in seen] == [f"/repos/{REPO}/pulls/7"]


# 応答のフック（PR 4265 の r1-6）


class _Watching(write_gate.WriteGate):
    """`block` の時点で送信のロックを持たれているかを覚える `WriteGate`。"""

    def __init__(self, path: Path) -> None:
        super().__init__(path, lambda: NOW, lambda seconds: None)
        self.held_at_block: list[bool] = []

    def block(self, seconds: float) -> None:
        probe = slot_pool.try_lock(self.path.with_name(write_gate.SEND_LOCK_FILE))
        self.held_at_block.append(probe is None)
        if probe is not None:
            probe.release()
        super().block(seconds)


@pytest.mark.parametrize(
    ("path", "response"),
    [
        (f"/repos/{REPO}/issues", httpx.Response(403, json={"message": SECONDARY}, headers={"x-ratelimit-remaining": "12"})),
        ("/graphql", httpx.Response(200, json={"data": None, "errors": [{"type": "FORBIDDEN", "message": SECONDARY}]})),
    ],
)
def test_the_wait_is_recorded_before_the_send_lock_is_released(tmp_path: Path, path: str, response: httpx.Response) -> None:
    gate = _Watching(tmp_path / write_gate.GATE_FILE)
    api = github.client(auth="t", transport=httpx.MockTransport(lambda request: response), gate=gate)
    with pytest.raises(github.GitHubError):
        if path == "/graphql":
            github.GitHub(REPO, api).graphql("mutation { addComment(input: {}) { x } }", {})
        else:
            github.GitHub(REPO, api).rest("POST", path, {"title": "t"})
    # フック・打ち直しの判断・失敗の写しが同じ応答の待ちを書き直さない（PR 4265 の r2-2）
    assert gate.held_at_block == [True]


def test_a_graphql_read_is_shared_by_the_translation_not_the_hook(tmp_path: Path) -> None:
    # PR 4265 の r2-1: フックは送信のロックを持たない読み取りの 200 の本文を読まない。待ちは失敗の写しが 1 回だけ知らせる
    gate = _Watching(tmp_path / write_gate.GATE_FILE)
    answer = {"data": None, "errors": [{"type": "FORBIDDEN", "message": SECONDARY}]}
    api = github.client(auth="t", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=answer)), gate=gate)
    with pytest.raises(github.GitHubError) as err:
        github.GitHub(REPO, api).graphql("query { viewer { login } }", {})
    assert err.value.secondary and gate.held_at_block == [False]


def test_the_hook_reuses_the_classification_made_before_sending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # PR 4265 の r3-1: 成功した GraphQL の要求の分類（要求の JSON の解析）は送信前の 1 回だけ
    seen: list[str] = []
    classify = write_gate.write_of
    monkeypatch.setattr(write_gate, "write_of", lambda request: seen.append(request.method) or classify(request))
    gate = write_gate.WriteGate(tmp_path / write_gate.GATE_FILE, lambda: NOW, lambda seconds: None)
    answer = {"data": {"viewer": {"login": "x"}}}
    api = github.client(auth="t", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=answer)), gate=gate)
    github.GitHub(REPO, api).graphql("query { viewer { login } }", {})
    assert seen == ["POST"]


def test_a_retry_shares_again_only_when_the_backoff_grows() -> None:
    # PR 4265 の r2-2: フックが知らせた待ちより長くなる打ち直しの回数だけ、同じ応答について知らせ直す
    gate = _Gate()
    decide = github.RetryWithin(remaining=lambda: 0.0, gate=gate)  # type: ignore[arg-type]
    failed = _failed(403, {"x-ratelimit-remaining": "12"}, SECONDARY, False)
    github_limits.share(gate, failed.response.raw_response, FIRST_WAIT)  # type: ignore[arg-type]
    decide(failed, 0)
    decide(failed, 1)
    assert gate.blocked == [FIRST_WAIT, github_limits.backoff(1)]
    error = github.GitHubError.of(github_limits.failure_of(failed, "POST /issues", False, time.time()))
    assert error.recorded
