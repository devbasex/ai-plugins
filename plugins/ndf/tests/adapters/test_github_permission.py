"""書き手の権限の厳しい照会（`GitHub.permission_strict`・`trusts_strict`）と、閉じる手順が使うコメントの ID・closing keywords（#4580）。

githubkit へ `httpx.MockTransport` を渡し、ネットワークへ出ない。
"""

from __future__ import annotations

import httpx
import pytest

from ndf.adapters import github, github_pulls

from ._github_helpers import RouteServer, hub

REPO = "devbasex/ai-plugins"


def _hub(server: RouteServer) -> github.GitHub:
    return hub(REPO, server)


def _rate_limited_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(403, json={"message": "rate"}, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"})


def _permission_server(*answers: httpx.Response) -> RouteServer:
    return RouteServer({("GET", f"/repos/{REPO}/collaborators/someone/permission"): list(answers)})


def _asked(server: RouteServer) -> int:
    return sum(r.url.path.endswith("/permission") for r in server.seen)


def test_permission_strict_reads_once_and_keeps_a_none_github_returned() -> None:
    # #4580: 200 の権限はそのまま返し、同じ書き手を照会し直さない。200 で返った none は照会できた結果として none
    for answer in ("write", "none"):
        server = _permission_server(httpx.Response(200, json={"permission": answer}))
        hub = _hub(server)
        assert [hub.permission_strict("someone"), hub.permission_strict("someone"), hub.permission("someone")] == [answer] * 3
        assert _asked(server) == 1


@pytest.mark.parametrize("status", [403, 404])
def test_permission_strict_refuses_an_unreadable_writer(status: int) -> None:
    # #4580: 照会できない書き手を権限の無い書き手として除かず、名前を持つ PermissionUnknown で止める
    server = _permission_server(httpx.Response(status, json={"message": "no"}))
    hub = _hub(server)
    with pytest.raises(github.PermissionUnknown) as caught:
        hub.permission_strict("someone")
    assert caught.value.login == "someone" and "someone" in str(caught.value)
    assert isinstance(caught.value, github.GitHubError)


def test_permission_strict_does_not_take_an_earlier_unreadable_none_as_answered() -> None:
    # #4580: 先に permission が 403 で none と読んだ書き手は、照会し直さずに PermissionUnknown
    server = _permission_server(httpx.Response(403, json={"message": "no"}))
    hub = _hub(server)
    assert hub.permission("someone") == "none"
    with pytest.raises(github.PermissionUnknown):
        hub.permission_strict("someone")
    assert hub.permission("someone") == "none"
    assert _asked(server) == 1


def test_permission_strict_keeps_the_rate_limit_as_it_is() -> None:
    server = RouteServer({("GET", f"/repos/{REPO}/collaborators/someone/permission"): [_rate_limited_response(httpx.Request("GET", "/"))]})
    with pytest.raises(github.GitHubError) as caught:
        _hub(server).permission_strict("someone")
    assert not isinstance(caught.value, github.PermissionUnknown)


@pytest.mark.parametrize(
    ("association", "answer", "trusted"),
    [("OWNER", None, True), ("MEMBER", "admin", True), ("COLLABORATOR", "write", True), ("MEMBER", "read", False),
     ("CONTRIBUTOR", None, False), ("NONE", None, False)],
)  # fmt: skip
def test_trusts_strict_asks_only_members_and_collaborators(association: str, answer: str | None, trusted: bool) -> None:
    server = _permission_server(httpx.Response(200, json={"permission": answer or "admin"}))
    assert _hub(server).trusts_strict(github.Authored("someone", association, "x")) is trusted
    assert _asked(server) == (0 if answer is None else 1)


@pytest.mark.parametrize("association", ["MEMBER", "COLLABORATOR"])
def test_trusts_strict_refuses_instead_of_distrusting_an_unreadable_writer(association: str) -> None:
    server = _permission_server(httpx.Response(404, json={"message": "no"}))
    with pytest.raises(github.PermissionUnknown):
        _hub(server).trusts_strict(github.Authored("someone", association, "x"))


def test_closing_issues_take_only_this_repository() -> None:
    # #4580: 閉じる手順は信用する本文の closing keywords から課題を集める。ほかのリポジトリの課題と重複は除く
    body = "Closes #3, fixes devbasex/ai-plugins#4\nFix: https://github.com/devbasex/ai-plugins/issues/5 resolves x/y#6 closes #3 (#7)"
    assert github_pulls.closing_keyword_issues(body, REPO) == [3, 4, 5]


def test_comments_carry_their_id() -> None:
    # #4580: 自分が書いたコメントを消すために REST の id を持つ
    comment = {"id": 42, "user": {"login": "someone"}, "author_association": "OWNER", "body": "x"}
    server = RouteServer({("GET", f"/repos/{REPO}/issues/7/comments"): [httpx.Response(200, json=[comment])]})
    assert [c.comment_id for c in _hub(server).comments(7)] == [42]
    assert github.Authored("someone", "OWNER", "x").comment_id == 0
