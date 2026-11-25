"""承認の前の確認が 1 回の取得で読む PR の値と、承認資料のコメントの追記（#4446）。

`GitHub.repository` の 1 回の問い合わせで、題・本文・head・変更量・コメントの本文を読む。`ndf document-rebuild gate` と
`ndf records reading check` が同じ口を使う。
"""

from __future__ import annotations

import json
from typing import NamedTuple, Optional

from ndf.adapters.github import GitHub, text_of

# 読むコメントの件数（1 回の GraphQL の問い合わせで返る上限）と、読む PR の値
VIEW_COMMENTS = 100
VIEW_FIELDS = (
    "number url title body headRefOid headRefName baseRefName changedFiles additions deletions commits { totalCount } "
    f"comments(last: {VIEW_COMMENTS}) {{ nodes {{ body }} }}"
)
QUERY = "query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ found: {selection} }} }}"
BY_HEAD = "pullRequests(headRefName: {branch}, states: OPEN, first: 1, orderBy: {{field: CREATED_AT, direction: DESC}}) {{ nodes {{ {fields} }} }}"


class PullView(NamedTuple):
    """PR の題・本文・head・変更量・コメントの本文（古い順。最後の `VIEW_COMMENTS` 件まで）。"""

    number: int
    url: str
    title: str
    body: str
    head_sha: str
    head_ref: str
    base_ref: str
    commits: int
    files: int
    additions: int
    deletions: int
    comments: tuple[str, ...]

    @classmethod
    def from_graphql(cls, raw: dict) -> PullView:
        comments = tuple(text_of(c["body"]) for c in raw["comments"]["nodes"] if isinstance(c, dict))
        return cls(
            int(raw["number"]), text_of(raw["url"]), text_of(raw["title"]), text_of(raw["body"]), text_of(raw["headRefOid"]),
            text_of(raw["headRefName"]), text_of(raw["baseRefName"]), int(raw["commits"]["totalCount"]), int(raw["changedFiles"]),
            int(raw["additions"]), int(raw["deletions"]), comments,
        )  # fmt: skip


def pull_view(hub: GitHub, number: Optional[int], branch: Optional[str]) -> Optional[PullView]:
    """PR を 1 回の問い合わせで読む。`number` が無ければ `branch` の開いた最新の PR。無ければ None。失敗は `GitHubError`。"""
    if number is not None:
        selection = f"pullRequest(number: {int(number)}) {{ {VIEW_FIELDS} }}"
    elif branch:
        selection = BY_HEAD.format(branch=json.dumps(branch, ensure_ascii=False), fields=VIEW_FIELDS)
    else:
        raise ValueError("PR の番号かブランチの名前が要る")
    got = hub.repository(QUERY.format(selection=selection))["found"]
    if number is None and isinstance(got, dict):
        nodes = got["nodes"]
        got = nodes[0] if nodes else None
    return PullView.from_graphql(got) if isinstance(got, dict) else None


def add_comment(hub: GitHub, number: int, body: str) -> None:
    """Issue か PR にコメントを 1 件足す。失敗は `GitHubError`。"""
    hub.rest("POST", f"/repos/{hub.repo}/issues/{int(number)}/comments", {"body": body})
