"""GitHub のアダプターのテスト（`test_github.py`・`test_publish.py`・`test_issue_reproductions.py`）が共有する、
ネットワークへ出ないクライアントと PR の生の辞書の作り方。GraphQL の PR の節点も持つ。"""
# `offline_client` は `test_ci_cli.py` の `_serve` とレート制限の応答も使う。

from __future__ import annotations

from collections.abc import Callable

import httpx
from githubkit import GitHub as Client

from ndf.adapters import github

Handler = Callable[[httpx.Request], httpx.Response]


def offline_client(handler: Handler) -> Client:
    """要求を `handler` へ渡す githubkit のクライアント（キャッシュと再試行なし）。"""
    return Client("t", transport=httpx.MockTransport(handler), http_cache=False, auto_retry=False)


def hub(repo: str, handler: Handler) -> github.GitHub:
    """`repo` への読み書きを `handler` が答える `GitHub`。"""
    return github.GitHub(repo, offline_client(handler))


def pull_json(head: str, body: str = "", merged: bool = False, mergeable_state: str = "clean") -> dict:
    """REST の PR 7 番の生の辞書（`develop` 宛て、書き手は OWNER）。"""
    return {
        "number": 7, "state": "open", "merged": merged, "body": body, "mergeable_state": mergeable_state,
        "head": {"sha": head, "ref": "feat/x"}, "base": {"ref": "develop"},
        "user": {"login": "octocat"}, "author_association": "OWNER",
    }  # fmt: skip


def graphql_pull_node(number: int, state: str, merged: bool, head: str, ref: str, body: str | None) -> dict:
    """GraphQL の PR の節点（`github.PULL_FIELDS` の形。`develop` 宛て、書き手は OWNER）。"""
    return {
        "number": number, "state": state, "merged": merged, "headRefOid": head, "headRefName": ref,
        "baseRefName": "develop", "body": body, "mergeStateStatus": "CLEAN",
        "author": {"login": "octocat"}, "authorAssociation": "OWNER",
    }  # fmt: skip
