"""ブランチの merge queue の読み取り（`ndf.adapters.merge_queue`）の通信の形。"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from ndf.adapters import github, merge_queue

from ._github_helpers import hub

REPO = "devbasex/ai-plugins"
HEAD = "2bb66a6c14a618cbac108b61737ad49cfb1ac48b"
OTHER = "0" * 40


def _hub(handler: Callable[[httpx.Request], httpx.Response]) -> github.GitHub:
    return hub(REPO, handler)


def test_heads_reads_every_page_then_stops_at_the_limit() -> None:
    """merge queue を `after` のカーソルで先頭から順に全ページ読み、番号と head の SHA をキューの並びで返す。
    キューが無ければ空で、`QUEUE_PAGES` ページで読み切れなければ GitHubError。"""
    seen: list[dict] = []

    def handler_of(pages: dict) -> Callable[[httpx.Request], httpx.Response]:
        def handler(request: httpx.Request) -> httpx.Response:
            variables = json.loads(request.content)["variables"]
            seen.append(variables)
            queue = pages[variables["after"]]
            return httpx.Response(200, json={"data": {"repository": {"mergeQueue": queue}}})

        return handler

    def page(nodes: list[tuple], cursor: str | None) -> dict:
        info = {"hasNextPage": cursor is not None, "endCursor": cursor}
        return {"entries": {"nodes": [{"pullRequest": {"number": n, "headRefOid": h}} for n, h in nodes], "pageInfo": info}}

    pages = {None: page([(8, HEAD)], "c1"), "c1": page([("9", OTHER)], None)}
    assert merge_queue.heads(_hub(handler_of(pages)), "main") == [(8, HEAD), (9, OTHER)]
    owner, name = REPO.split("/")
    assert seen == [
        {"owner": owner, "name": name, "branch": "main", "after": None},
        {"owner": owner, "name": name, "branch": "main", "after": "c1"},
    ]
    assert merge_queue.heads(_hub(handler_of({None: None})), "main") == []
    seen.clear()
    with pytest.raises(github.GitHubError, match=f"merge queue を {merge_queue.QUEUE_PAGES} ページで読み切れない"):
        merge_queue.heads(_hub(handler_of({None: page([], "c1"), "c1": page([], "c1")})), "main")
    assert len(seen) == merge_queue.QUEUE_PAGES
