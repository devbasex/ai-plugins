"""`github_pulls.merged_pulls` と、REST の PR のフォークの見分け（`Pull.from_rest`）。単位の完了の判定に使う。"""

from __future__ import annotations

import json

import httpx
import pytest

from ndf.adapters import github, github_pulls
from ndf.contracts import limits

from ._github_helpers import hub, pull_json

REPO = "o/r"
BASE = "sprint/x"


def _conn(*nodes: tuple[int, bool], after: str | None = None) -> dict:
    """マージ済みの PR の 1 ページ。節点は (番号, フォークから出たか)。`after` があれば次のページがある。"""
    return {
        "pageInfo": {"hasNextPage": after is not None, "endCursor": after},
        "nodes": [{"number": n, "isCrossRepository": fork} for n, fork in nodes],
    }


class _Server:
    """最初のページ（エイリアスのクエリ）と続きのページ（変数 `after` を持つ）を順に返し、要求の本文を残す。"""

    def __init__(self, first: dict[str, dict], rest: list[dict] | None = None) -> None:
        self.first, self.rest, self.sent = first, list(rest or []), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.sent.append(body)
        if "after" in body["variables"]:
            repo = {"pullRequests": self.rest.pop(0) if len(self.rest) > 1 else self.rest[0]}
        else:
            repo = {f"a{i}": self.first[head] for i, head in enumerate(sorted(self.first))}
        return httpx.Response(200, json={"data": {"repository": repo}})


def test_merged_pulls_reads_the_heads_at_once_and_drops_forks_and_unmerged_heads() -> None:
    server = _Server({"a": _conn((12, True), (11, False), (9, False)), "b": _conn((13, True)), "c": _conn()})
    assert github_pulls.merged_pulls(hub(REPO, server), ["c", "b", "a", "a"], BASE) == {"a": 11}
    (sent,) = server.sent
    # マージ先と状態は GitHub 側で絞る
    assert sent["query"].count(f'baseRefName: "{BASE}", states: [MERGED]') == 3
    assert 'headRefName: "a"' in sent["query"]


def test_merged_pulls_of_no_heads_sends_nothing() -> None:
    server = _Server({})
    assert github_pulls.merged_pulls(hub(REPO, server), [], BASE) == {}
    assert server.sent == []


def test_merged_pulls_reads_on_until_a_pull_of_this_repository() -> None:
    server = _Server({"a": _conn((20, True), after="p2")}, [_conn((19, True), after="p3"), _conn((5, False), after="p4")])
    assert github_pulls.merged_pulls(hub(REPO, server), ["a"], BASE) == {"a": 5}
    assert [s["variables"].get("after") for s in server.sent] == [None, "p2", "p3"]
    assert server.sent[1]["variables"]["head"] == "a" and server.sent[1]["variables"]["base"] == BASE


def test_merged_pulls_over_the_page_cap_is_an_error_not_unmerged() -> None:
    server = _Server({"a": _conn((20, True), after="p2")}, [_conn((19, True), after="more")])
    with pytest.raises(github.GitHubError, match="github.list-pages"):
        github_pulls.merged_pulls(hub(REPO, server), ["a"], BASE)
    assert len(server.sent) == int(limits.value("github.list-pages", "count"))


def _rest(head_repo: object, base_repo: str = REPO) -> dict:
    raw = pull_json("a" * 40)
    raw["head"]["repo"] = {"full_name": head_repo} if isinstance(head_repo, str) else head_repo
    raw["base"]["repo"] = {"full_name": base_repo}
    return raw


@pytest.mark.parametrize(("head_repo", "cross"), [(REPO, False), ("O/R", False), ("fork/r", True), (None, True)])
def test_from_rest_tells_a_pull_from_a_fork(head_repo: object, cross: bool) -> None:
    assert github.Pull.from_rest(_rest(head_repo)).cross_repository is cross


def test_from_rest_without_the_repository_keys_is_not_cross_repository() -> None:
    assert github.Pull.from_rest(pull_json("a" * 40)).cross_repository is False
