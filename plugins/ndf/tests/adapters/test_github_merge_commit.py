"""マージの応答からマージコミットを返すこと（#4369）。単位を流す処理は、この値までスプリントの作業場所を進める。"""

from __future__ import annotations

import httpx

from .test_github import _PULL_ID_ANSWER, _PULL_PATH, HEAD, REPO, Server, _graphql_sent, _hub, _pull


def test_merge_returns_the_merge_commit_from_the_response() -> None:
    landed = "c" * 40
    server = Server({
        ("GET", f"/repos/{REPO}/pulls/7"): [httpx.Response(200, json=_pull())],
        ("PUT", f"/repos/{REPO}/pulls/7/merge"): [httpx.Response(200, json={"merged": True, "sha": landed})],
    })  # fmt: skip
    assert _hub(server).merge(7).merge_sha == landed


def test_merge_as_admin_returns_the_merge_commit_from_the_mutation() -> None:
    landed = {"data": {"mergePullRequest": {"pullRequest": {"merged": True, "mergeCommit": {"oid": "d" * 40}}}}}
    server = Server({
        _PULL_PATH: [httpx.Response(200, json=_pull())],
        ("POST", "/graphql"): [httpx.Response(200, json=_PULL_ID_ANSWER), httpx.Response(200, json=landed)],
    })  # fmt: skip
    assert _hub(server).merge_as_admin(7, HEAD).merge_sha == "d" * 40
    assert "mergeCommit { oid }" in _graphql_sent(server)[1]["query"]
