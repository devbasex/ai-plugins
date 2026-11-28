"""マージキューの項目の読み取りと取り外し（`GitHub.queue_holder`・`GitHub.dequeue`。#4520）。ネットワークへ出ない。"""

from __future__ import annotations

import httpx
import pytest

from ._github_helpers import RouteServer, hub

REPO = "devbasex/ai-plugins"


@pytest.mark.parametrize(
    ("entry", "expected"), [({"id": "e1", "enqueuer": {"databaseId": 5}}, 5), ({"id": "e1", "enqueuer": {}}, 0), (None, None)]
)
def test_queue_holder_reads_the_enqueuer_and_the_target(entry: dict | None, expected: int | None) -> None:
    pr = {"baseRefName": "main", "headRefOid": "abc", "mergeQueueEntry": entry}
    server = RouteServer({("POST", "/graphql"): [httpx.Response(200, json={"data": {"repository": {"pullRequest": pr}}})]})
    assert hub(REPO, server).queue_holder(7) == (None if expected is None else (expected, "main", "abc", "e1"))


@pytest.mark.parametrize(("entry", "left"), [("e1", True), ("e0", False), ("", True)])
def test_dequeue_leaves_only_the_judged_entry(entry: str, left: bool) -> None:
    pr = {"id": "PR_7", "isInMergeQueue": True, "mergeQueueEntry": {"id": "e1"}}
    server = RouteServer({("POST", "/graphql"): [
        httpx.Response(200, json={"data": {"repository": {"pullRequest": pr}}}),
        httpx.Response(200, json={"data": {"dequeuePullRequest": {}}}),
    ]})  # fmt: skip
    assert hub(REPO, server).dequeue(7, entry) is left
    assert len(server.sent("POST")) == (2 if left else 1)
