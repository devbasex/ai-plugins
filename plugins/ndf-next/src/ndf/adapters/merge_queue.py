"""ブランチの merge queue に入っている PR を GitHub の GraphQL で読む（版上げの衝突の確かめが使う）。

通信の形（クエリ・ページの上限・`entries` と `pageInfo` の解釈）はここだけが持ち、呼び手は PR の番号と head の SHA の組を受ける。
"""

from __future__ import annotations

from typing import Any, Protocol

from ndf.adapters.github import GitHubError, text_of

# entries を 1 ページ（100 件）ずつ読むクエリと、読むページの上限
_QUEUE_QUERY = (
    "query($owner: String!, $name: String!, $branch: String!, $after: String) { repository(owner: $owner, name: $name) {"
    " mergeQueue(branch: $branch) { entries(first: 100, after: $after) { pageInfo { hasNextPage endCursor }"
    " nodes { pullRequest { number headRefOid } } } } } }"
)
QUEUE_PAGES = 10


class GraphQLHub(Protocol):
    """読むのに要る `GitHub` の部分（`owner/name` のリポジトリと、GraphQL の 1 回の要求）。"""

    repo: str

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]: ...


def heads(hub: GraphQLHub, branch: str) -> list[tuple[int, str]]:
    """ブランチ `branch` の merge queue の PR の番号と head の SHA（キューの並び）。全ページを先頭から順に読む。

    キューの無いブランチ（`mergeQueue` が null）は空。`QUEUE_PAGES` ページで読み切れなければ `GitHubError`。
    """
    owner, _, name = hub.repo.partition("/")
    nodes: list[dict] = []
    after = None
    for _ in range(QUEUE_PAGES):
        data = hub.graphql(_QUEUE_QUERY, {"owner": owner, "name": name, "branch": branch, "after": after})
        queue = data["repository"]["mergeQueue"]
        if queue is None:
            return []
        entries = queue["entries"]
        nodes += entries["nodes"]
        page = entries["pageInfo"]
        if not page["hasNextPage"]:
            return [(int(n["pullRequest"]["number"]), text_of(n["pullRequest"]["headRefOid"])) for n in nodes]
        after = page["endCursor"]
    raise GitHubError(f"本番ブランチ {branch} の merge queue を {QUEUE_PAGES} ページで読み切れない")
