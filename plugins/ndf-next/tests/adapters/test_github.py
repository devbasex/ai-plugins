"""GitHub のアダプター（`ndf.adapters.github`）。githubkit へ `httpx.MockTransport` を渡し、ネットワークへ出ない。"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from datetime import timedelta

import httpx
import pytest
from githubkit import GitHub as Client
from githubkit.exception import RateLimitExceeded, RequestFailed
from githubkit.response import Response

from ndf.adapters import github

REPO = "devbasex/ai-plugins"
HEAD = "2bb66a6c14a618cbac108b61737ad49cfb1ac48b"
OTHER = "0" * 40


def _hub(handler: Callable[[httpx.Request], httpx.Response]) -> github.GitHub:
    api = Client("t", transport=httpx.MockTransport(handler), http_cache=False, auto_retry=False)
    return github.GitHub(REPO, api)


def _pull(head: str = HEAD, body: str = "", merged: bool = False, mergeable_state: str = "clean") -> dict:
    return {
        "number": 7, "state": "open", "merged": merged, "body": body, "mergeable_state": mergeable_state,
        "head": {"sha": head, "ref": "feat/x"}, "base": {"ref": "develop"},
    }  # fmt: skip


class Server:
    """要求を記録し、(メソッド, パス) ごとに応答を順に返す。"""

    def __init__(self, routes: dict[tuple[str, str], list[httpx.Response]]) -> None:
        self.routes = routes
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        queue = self.routes[(request.method, request.url.path)]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def sent(self, method: str) -> list[httpx.Request]:
        return [r for r in self.seen if r.method == method]


def test_pull_reads_rest_fields() -> None:
    server = Server({("GET", f"/repos/{REPO}/pulls/7"): [httpx.Response(200, json=_pull(body=None))]})  # type: ignore[arg-type]
    pull = _hub(server).pull(7)
    assert (pull.number, pull.head_sha, pull.head_ref, pull.base_ref, pull.body, pull.merge_state) == (
        7,
        HEAD,
        "feat/x",
        "develop",
        "",
        "clean",
    )


def test_merge_resolves_short_expected_head_and_sends_full_sha() -> None:
    # #2041: 同じ先端の短い SHA で止まらない。要求には完全な SHA を添える
    server = Server({
        ("GET", f"/repos/{REPO}/pulls/7"): [httpx.Response(200, json=_pull())],
        ("PUT", f"/repos/{REPO}/pulls/7/merge"): [httpx.Response(200, json={"merged": True})],
    })  # fmt: skip
    out = _hub(server).merge(7, expect_head="2bb66a6c1")
    assert out.merged and out.kind is None
    assert json.loads(server.sent("PUT")[0].content) == {"sha": HEAD, "merge_method": "squash"}


def test_merge_refuses_moved_head_without_sending_merge() -> None:
    server = Server({("GET", f"/repos/{REPO}/pulls/7"): [httpx.Response(200, json=_pull())]})
    out = _hub(server).merge(7, expect_head="deadbeef1")
    assert (out.merged, out.kind) == (False, "refused")
    assert server.sent("PUT") == []


def test_merge_refuses_undecided_premerge_risk() -> None:
    # #2602: 本文の「マージの前に決める」残リスクを、決めた記録なしにマージしない
    body = "## 残リスク\n\n- [ ] 残リスク: テストの照合が止まっている。マージの前に決める\n"
    server = Server({("GET", f"/repos/{REPO}/pulls/7"): [httpx.Response(200, json=_pull(body=body))]})
    out = _hub(server).merge(7)
    assert (out.merged, out.kind) == (False, "refused")
    assert "マージの前に決める" in out.detail
    assert server.sent("PUT") == []


def test_merge_proceeds_when_premerge_risk_is_checked() -> None:
    body = "- [x] 残リスク: テストの照合が止まっている。マージの前に決める（続けると決めた）\n"
    server = Server({
        ("GET", f"/repos/{REPO}/pulls/7"): [httpx.Response(200, json=_pull(body=body))],
        ("PUT", f"/repos/{REPO}/pulls/7/merge"): [httpx.Response(200, json={"merged": True})],
    })  # fmt: skip
    assert _hub(server).merge(7).merged


@pytest.mark.parametrize(
    ("status", "after", "kind"),
    [
        (405, _pull(mergeable_state="dirty"), "base_conflict"),
        (405, _pull(mergeable_state="blocked"), "not_ci"),
        (405, _pull(mergeable_state="clean"), "unknown"),
        (409, _pull(head=OTHER), "refused"),
    ],
)
def test_merge_refusal_is_classified_from_merge_state(status: int, after: dict, kind: str) -> None:
    # SF4: 拒否の理由は読み直した mergeStateStatus で決め、標準エラーの文を読まない
    server = Server({
        ("GET", f"/repos/{REPO}/pulls/7"): [httpx.Response(200, json=_pull()), httpx.Response(200, json=after)],
        ("PUT", f"/repos/{REPO}/pulls/7/merge"): [httpx.Response(status, json={"message": "no"})],
    })  # fmt: skip
    out = _hub(server).merge(7)
    assert (out.merged, out.kind) == (False, kind)


def test_merge_raises_on_server_error() -> None:
    server = Server({
        ("GET", f"/repos/{REPO}/pulls/7"): [httpx.Response(200, json=_pull())],
        ("PUT", f"/repos/{REPO}/pulls/7/merge"): [httpx.Response(500, json={"message": "boom"})],
    })  # fmt: skip
    with pytest.raises(github.GitHubError) as err:
        _hub(server).merge(7)
    assert err.value.status == 500


def test_rate_limit_carries_retry_after() -> None:
    limited = httpx.Response(403, json={"message": "rate"}, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"})
    server = Server({("GET", f"/repos/{REPO}/pulls/7"): [limited]})
    with pytest.raises(github.GitHubError) as err:
        _hub(server).pull(7)
    assert err.value.status == 403 and err.value.retry_after is not None and err.value.retry_after > 0


def _rate_limited(retry_after: float) -> RateLimitExceeded:
    request = httpx.Request("GET", f"https://api.github.com/repos/{REPO}/pulls/7")
    response = Response(httpx.Response(403, request=request), object)
    return RateLimitExceeded(response, timedelta(seconds=retry_after))


@pytest.mark.parametrize(("retry_after", "retry"), [(30.0, True), (120.0, False)])
def test_retry_within_waits_only_within_the_remaining_budget(retry_after: float, retry: bool) -> None:
    # 解除が待ちの残り（60 秒）を越える上限は githubkit の中で眠らず、呼び手へ返す
    decide = github.RetryWithin(remaining=lambda: 60.0)
    assert decide(_rate_limited(retry_after), 0).do_retry is retry


def test_retry_within_keeps_default_server_error_retries() -> None:
    request = httpx.Request("GET", "https://api.github.com/x")
    failed = RequestFailed(Response(httpx.Response(502, request=request), object))
    assert github.RetryWithin(remaining=lambda: 0.0)(failed, 0).do_retry


def test_check_runs_reads_pages_until_total() -> None:
    pages = [httpx.Response(200, json={"total_count": 150, "check_runs": [{"id": i} for i in range(n)]}) for n in (100, 50)]
    server = Server({("GET", f"/repos/{REPO}/commits/{HEAD}/check-runs"): pages})
    assert len(_hub(server).check_runs(HEAD)) == 150
    assert [r.url.params["page"] for r in server.seen] == ["1", "2"]


def test_check_runs_refuses_more_pages_than_the_limit() -> None:
    full = httpx.Response(200, json={"total_count": 100000, "check_runs": [{"id": 1}] * 100})
    server = Server({("GET", f"/repos/{REPO}/commits/{HEAD}/check-runs"): [full]})
    with pytest.raises(github.GitHubError, match="github.check-runs"):
        _hub(server).check_runs(HEAD)


def _pr_node(number: int) -> dict:
    return {
        "number": number, "state": "OPEN", "merged": False, "headRefOid": HEAD, "headRefName": f"b{number}",
        "baseRefName": "develop", "body": None, "mergeStateStatus": "CLEAN",
    }  # fmt: skip


def _hub_graphql(answer: Callable[[str], dict]) -> tuple[github.GitHub, list[str]]:
    """GraphQL の要求ごとにクエリの本文を記録し、`answer(query)` を応答にする。"""
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        queries.append(query)
        return httpx.Response(200, json=answer(query))

    return _hub(handler), queries


def _aliases(query: str) -> list[str]:
    return re.findall(r"\b(a\d+): ", query)


def test_pulls_batches_numbers_into_one_query_per_hundred() -> None:
    # A2: 番号ごとの読み取りを 1 回のクエリ（100 件ごと）へまとめる
    def answer(query: str) -> dict:
        nums = [int(n) for n in re.findall(r"pullRequest\(number: (\d+)\)", query)]
        return {"data": {"repository": {a: _pr_node(n) for a, n in zip(_aliases(query), nums)}}}

    hub, queries = _hub_graphql(answer)
    got = hub.pulls(range(1, 151))
    assert len(queries) == 2
    assert [len(_aliases(q)) for q in queries] == [100, 50]
    assert got[150] is not None and got[150].head_ref == "b150" and got[150].state == "open"


def test_nodes_keeps_read_numbers_when_only_not_found_errors() -> None:
    def answer(query: str) -> dict:
        return {
            "data": {"repository": {"a0": _pr_node(1), "a1": None}},
            "errors": [{"type": "NOT_FOUND", "message": "Could not resolve to a PullRequest with the number of 2."}],
        }

    hub, _ = _hub_graphql(answer)
    got = hub.pulls([1, 2])
    assert got[1] is not None and got[2] is None


def test_graphql_other_errors_raise() -> None:
    hub, _ = _hub_graphql(lambda q: {"data": None, "errors": [{"type": "FORBIDDEN", "message": "no"}]})
    with pytest.raises(github.GitHubError, match="no"):
        hub.pulls([1])


def test_pulls_by_head_batches_branches() -> None:
    def answer(query: str) -> dict:
        return {"data": {"repository": {a: {"nodes": [_pr_node(i)]} for i, a in enumerate(_aliases(query))}}}

    hub, queries = _hub_graphql(answer)
    got = hub.pulls_by_head(["feat/a", 'feat/"b"'])
    assert len(queries) == 1
    assert 'headRefName: "feat/\\"b\\""' in queries[0]
    assert sorted(got) == sorted(["feat/a", 'feat/"b"']) and all(len(v) == 1 for v in got.values())


def test_nodes_refuses_unknown_node() -> None:
    hub, _ = _hub_graphql(lambda q: {})
    with pytest.raises(ValueError):
        hub.nodes([1], "repository", "id")


def test_job_log_returns_text() -> None:
    server = Server(
        {("GET", f"/repos/{REPO}/actions/jobs/5/logs"): [httpx.Response(200, text="line\n", headers={"content-type": "text/plain"})]}
    )
    assert _hub(server).job_log(5) == "line\n"


@pytest.mark.parametrize(
    ("body", "risks"),
    [
        ("残リスク: 照合が止まっている。マージの前に決める", ["残リスク: 照合が止まっている。マージの前に決める"]),
        ("- [ ] 残リスク: A。マージの前に決める", ["- [ ] 残リスク: A。マージの前に決める"]),
        ("- [x] 残リスク: A。マージの前に決める", []),
        ("残リスク: リリースの後に確かめる", []),
    ],
)
def test_premerge_risks(body: str, risks: list[str]) -> None:
    assert github.premerge_risks(body) == risks


@pytest.mark.parametrize(
    ("origin", "target", "expected"),
    [
        ("issue #12", "example-owner/other-repo", "issue example-owner/example-repo#12"),
        ("PR #3", "example-owner/other-repo", "PR example-owner/example-repo#3"),
        ("issue #12", "example-owner/example-repo", "issue #12"),
        ("振り返り", "example-owner/other-repo", "振り返り"),
    ],
)
def test_qualified_ref_names_the_source_repo_for_other_repos(origin: str, target: str, expected: str) -> None:
    # #3077: 別のリポジトリへ書く由来の参照は、起票先の別の課題を指さない
    assert github.qualified_ref(origin, "example-owner/example-repo", target) == expected


def test_token_prefers_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "from-env")
    assert github.token() == "from-env"


def test_token_without_any_source_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(github.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "not logged in"))
    with pytest.raises(github.GitHubError, match="認証"):
        github.token()


# 現状固定（I-002）: `GitHub.rest` と `GitHub.graphql` の失敗の写し方。期待値は今の実装の出力である
def _rate_limited_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(403, json={"message": "rate"}, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"})


def _server_error(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"message": "boom"})


def _unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("down", request=request)


@pytest.mark.parametrize(
    ("handler", "rest_detail", "graphql_detail", "status", "has_retry"),
    [
        (_rate_limited_response, "GitHub の上限: GET /x", "GitHub の上限: graphql", 403, True),
        (_server_error, 'GitHub が断った（500）: GET /x: {"message":"boom"}', "GitHub が断った（500）: graphql", 500, False),
        (_unreachable, "GitHub へ届かない: GET /x: down", "GitHub へ届かない: graphql: down", 0, False),
    ],
)
def test_rest_and_graphql_map_transport_failures(
    handler: Callable[[httpx.Request], httpx.Response], rest_detail: str, graphql_detail: str, status: int, has_retry: bool
) -> None:
    for call, detail in ((lambda h: h.rest("GET", "/x"), rest_detail), (lambda h: h.graphql("q", {}), graphql_detail)):
        with pytest.raises(github.GitHubError) as err:
            call(_hub(handler))
        assert str(err.value) == detail
        assert err.value.status == status
        assert (err.value.retry_after is not None and err.value.retry_after > 0) == has_retry


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"data": {"x": 1}}, {"x": 1}),
        ({"data": {"x": None}, "errors": [{"type": "NOT_FOUND", "message": "a"}]}, {"x": None}),
    ],
)
def test_graphql_returns_data(answer: dict, expected: dict) -> None:
    hub, _ = _hub_graphql(lambda q: answer)
    assert hub.graphql("q", {}) == expected


@pytest.mark.parametrize(
    ("answer", "detail"),
    [
        ({"data": None, "errors": [{"type": "NOT_FOUND", "message": "nf"}]}, "GraphQL の誤り: nf"),
        (
            {"data": {"x": 1}, "errors": [{"type": "NOT_FOUND", "message": "a"}, {"type": "FORBIDDEN", "message": "b"}]},
            "GraphQL の誤り: a; b",
        ),
    ],
)
def test_graphql_errors_raise_with_joined_messages(answer: dict, detail: str) -> None:
    hub, _ = _hub_graphql(lambda q: answer)
    with pytest.raises(github.GitHubError) as err:
        hub.graphql("q", {})
    assert (str(err.value), err.value.status, err.value.retry_after) == (detail, 0, None)
