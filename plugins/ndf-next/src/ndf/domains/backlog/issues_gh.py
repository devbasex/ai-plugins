"""GitHub のアダプター（`ndf.adapters.github`）越しの Issue・マイルストーン・親の読み書き。

親の照会は、親が無いことを HTTP の状態（404）で読む。例外の文の字面では判定しない（#2701）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Optional
from urllib.parse import quote

from ndf.adapters.github import PER_PAGE, GitHub, GitHubError, text_of
from ndf.contracts import limits

ISSUE_FIELDS = "number title body createdAt updatedAt milestone { title } labels(first: 50) { nodes { name } } parent { number }"
_CLOSED_PAGE = (
    "query($owner: String!, $name: String!, $since: DateTime!, $after: String) { repository(owner: $owner, name: $name) {"
    f" issues(states: [CLOSED], filterBy: {{since: $since}}, first: {PER_PAGE}, after: $after) {{"
    " pageInfo { hasNextPage endCursor } nodes { number closedAt milestone { title } } } } }"
)
_MILESTONE_PAGE = (
    "query($owner: String!, $name: String!, $after: String) { repository(owner: $owner, name: $name) {"
    f" milestones(first: {PER_PAGE}, after: $after, states: [OPEN, CLOSED]) {{"
    " pageInfo { hasNextPage endCursor } nodes { number title description } } } }"
)
NOT_FOUND = 404


def _issue(raw: dict[str, Any]) -> dict[str, Any]:
    ms = raw["milestone"]
    parent = raw["parent"] if "parent" in raw else None
    return {
        "number": int(raw["number"]),
        "title": text_of(raw["title"]),
        "body": text_of(raw["body"]),
        "milestone": text_of(ms["title"]) if isinstance(ms, dict) else None,
        "labels": [text_of(n["name"]) for n in raw["labels"]["nodes"]],
        "created_at": text_of(raw["createdAt"]),
        "updated_at": text_of(raw["updatedAt"]),
        "parent": int(parent["number"]) if isinstance(parent, dict) else None,
    }


def open_issues(gh: GitHub) -> list[dict[str, Any]]:
    """open の Issue（PR を除く）を番号の順にすべて読む。"""
    numbers = gh.issue_numbers(state="OPEN")
    got = gh.nodes(numbers, "issue", ISSUE_FIELDS)
    return [_issue(raw) for _, raw in sorted(got.items()) if raw]


def _graphql_pages(gh: GitHub, query: str, variables: dict[str, Any], key: str, what: str) -> Iterator[list[dict[str, Any]]]:
    """GraphQL の接続 `key` を after のカーソルで送り、ページごとの nodes を返す。`github.list-pages` ページを超えたら `GitHubError`。"""
    pages = int(limits.value("github.list-pages", "count"))
    after: Optional[str] = None
    for _ in range(pages):
        conn = gh.repository(query, {**variables, "after": after})[key]
        yield conn["nodes"]
        if not conn["pageInfo"]["hasNextPage"]:
            return
        after = conn["pageInfo"]["endCursor"]
    raise GitHubError(f"{what}が {pages} ページを超えた（github.list-pages）")


def closed_since(gh: GitHub, since: str) -> list[dict[str, Any]]:
    """`since`（ISO 8601）より後に閉じた Issue の番号とマイルストーンの題。`github.list-pages` ページを超えたら `GitHubError`。"""
    out: list[dict[str, Any]] = []
    for nodes in _graphql_pages(gh, _CLOSED_PAGE, {"since": since}, "issues", "閉じた Issue の一覧"):
        for raw in nodes:
            if text_of(raw["closedAt"]) >= since:
                ms = raw["milestone"]
                out.append({"number": int(raw["number"]), "milestone": text_of(ms["title"]) if isinstance(ms, dict) else None})
    return out


def milestone_rows(gh: GitHub) -> dict[str, dict[str, Any]]:
    """マイルストーンの題 → {number, description}（open と closed）。`github.list-pages` ページを超えたら `GitHubError`。"""
    out: dict[str, dict[str, Any]] = {}
    for nodes in _graphql_pages(gh, _MILESTONE_PAGE, {}, "milestones", "マイルストーンの一覧"):
        out.update({text_of(m["title"]): {"number": int(m["number"]), "description": text_of(m.get("description"))} for m in nodes})
    return out


def milestones(gh: GitHub) -> dict[str, int]:
    """マイルストーンの題 → 番号（open と closed）。"""
    return {title: row["number"] for title, row in milestone_rows(gh).items()}


def set_milestone_description(gh: GitHub, number: int, description: str) -> None:
    gh.rest("PATCH", f"/repos/{gh.repo}/milestones/{int(number)}", {"description": description})


def set_milestone(gh: GitHub, n: int, number: Optional[int]) -> None:
    gh.rest("PATCH", f"/repos/{gh.repo}/issues/{int(n)}", {"milestone": number})


def issue(gh: GitHub, n: int) -> dict[str, Any]:
    """Issue の今の題・本文・状態・ラベル・更新の時刻（REST）。"""
    raw = gh.rest("GET", f"/repos/{gh.repo}/issues/{int(n)}")
    labels = raw["labels"] if isinstance(raw.get("labels"), list) else []
    return {
        "number": int(n),
        "title": text_of(raw.get("title")),
        "body": text_of(raw.get("body")),
        "state": text_of(raw.get("state")).lower(),
        "labels": [text_of(lb["name"]) if isinstance(lb, dict) else text_of(lb) for lb in labels],
        "updated_at": text_of(raw.get("updated_at")),
    }


def patch_issue(gh: GitHub, n: int, patch: dict[str, Any]) -> None:
    gh.rest("PATCH", f"/repos/{gh.repo}/issues/{int(n)}", patch)


def add_labels(gh: GitHub, n: int, labels: list[str]) -> None:
    gh.rest("POST", f"/repos/{gh.repo}/issues/{int(n)}/labels", {"labels": labels})


def remove_label(gh: GitHub, n: int, label: str) -> None:
    """ラベルを外す。名前の `/` を別のパスにしないため、パスの 1 区切りとして符号化する。"""
    gh.rest("DELETE", f"/repos/{gh.repo}/issues/{int(n)}/labels/{quote(label, safe='')}")


def create_issue(gh: GitHub, title: str, body: str, labels: list[str]) -> int:
    """Issue を起票し、番号を返す。"""
    return int(gh.rest("POST", f"/repos/{gh.repo}/issues", {"title": title, "body": body, "labels": labels})["number"])


def issue_state(gh: GitHub, n: int) -> str:
    """Issue の状態（`open`・`closed`）。"""
    return text_of(gh.rest("GET", f"/repos/{gh.repo}/issues/{int(n)}")["state"]).lower()


def _issue_id(gh: GitHub, n: int) -> int:
    return int(gh.rest("GET", f"/repos/{gh.repo}/issues/{int(n)}")["id"])


def parent_of(gh: GitHub, n: int) -> Optional[int]:
    """Issue の親の番号。親が無ければ（HTTP 404）None。ほかの失敗は `GitHubError` のまま投げる。"""
    try:
        raw = gh.rest("GET", f"/repos/{gh.repo}/issues/{int(n)}/parent")
    except GitHubError as exc:
        if exc.status != NOT_FOUND:
            raise
        return None
    return int(raw["number"])


def set_parent(gh: GitHub, n: int, parent: Optional[int], current: Optional[int]) -> None:
    """Issue の親を `parent` へ付け直す。None なら今の親から外す。今と同じなら何もしない。"""
    if parent == current:
        return
    sub_id = _issue_id(gh, n)
    if parent is None:
        gh.rest("DELETE", f"/repos/{gh.repo}/issues/{int(current)}/sub_issue", {"sub_issue_id": sub_id})
        return
    gh.rest("POST", f"/repos/{gh.repo}/issues/{int(parent)}/sub_issues", {"sub_issue_id": sub_id, "replace_parent": True})


def close_with_comment(gh: GitHub, n: int, body: str) -> None:
    """証拠をコメントに書いてから Issue を閉じる。"""
    gh.rest("POST", f"/repos/{gh.repo}/issues/{int(n)}/comments", {"body": body})
    gh.rest("PATCH", f"/repos/{gh.repo}/issues/{int(n)}", {"state": "closed", "state_reason": "completed"})
