"""GitHub の PR と本文の読み取りの型と、マージ済みの PR の読み取り（型は `ndf.adapters.github` が同じ名前で読み直す）。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, NamedTuple, Optional, Protocol

from ndf.contracts import limits

# 一覧の 1 ページの件数（REST と GraphQL の上限。ページ数の上限は contracts/limits/ の github.check-runs・github.list-pages）
PER_PAGE = 100

PULL_FIELDS = (  # PR を読むときの GraphQL の選択（`Pull.from_graphql` が読む項目）
    "number state merged headRefOid headRefName baseRefName body mergeStateStatus author { login } authorAssociation mergeCommit { oid }"
    " title isInMergeQueue isCrossRepository"
)


def text_of(value: Any) -> str:
    """文字列ならそのまま、ほかの値（None など）は空文字。"""
    return value if isinstance(value, str) else ""


def _login(user: Any) -> str:
    """書き手の login。消えたアカウント（REST の `user`・GraphQL の `author` が null）は空文字。"""
    return text_of(user["login"]) if isinstance(user, dict) else ""


def account_id_of(raw: Any) -> int:
    """GitHub が発行するアカウント ID（REST の `user.id`・GraphQL の `databaseId`）。無い・読めなければ 0。"""
    if not isinstance(raw, dict):
        return 0
    got = raw["id"] if "id" in raw else raw["databaseId"] if "databaseId" in raw else None
    return got if isinstance(got, int) and not isinstance(got, bool) and got > 0 else 0


def target_of(node: dict) -> tuple[str, str]:
    """GraphQL の PR の節点のマージ先のブランチと head の SHA（無ければ空文字）。"""
    return text_of(node.get("baseRefName")), text_of(node.get("headRefOid"))


def graphql_string(value: str) -> str:
    """GraphQL の文字列の値（JSON の文字列と同じ書き方）。"""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _from_fork(head: dict, base: dict) -> bool:
    """REST の PR の head のブランチがほかのリポジトリ（フォーク）にあるか。head の `repo` のキーが無ければ偽。

    head のリポジトリが null（消されたフォーク）なら真。
    """
    if "repo" not in head:
        return False
    if not isinstance(head["repo"], dict):
        return True
    ours = base.get("repo")
    return isinstance(ours, dict) and text_of(head["repo"].get("full_name")).lower() != text_of(ours.get("full_name")).lower()


class Authored(NamedTuple):
    """書き手の付いた本文（Issue・PR・コメント）。`association` は GitHub の author_association（大文字）。

    信用の区分は対象リポジトリでの権限で決まるため、`GitHub.trusts` が判定する（#3364）。
    """

    author: str
    association: str
    body: str
    # コメントの URL（REST の `html_url`。迂回の記録が照らした証跡を指す。#3506）。無ければ空
    url: str = ""
    # 作成の後に編集されたか（REST の `updated_at` が `created_at` と違う）。編集された証跡は承認に数えない（#3506）
    edited: bool = False
    # 書き手のアカウント ID（REST の `user.id`）。同じ名前の別のアカウントを分けるのに使う（#4409）。無ければ 0
    author_id: int = 0

    @classmethod
    def from_rest(cls, raw: dict) -> Authored:
        url = text_of(raw["html_url"]) if isinstance(raw.get("html_url"), str) else ""
        created, updated = raw.get("created_at"), raw.get("updated_at")
        edited = isinstance(created, str) and isinstance(updated, str) and updated != created
        user = raw["user"]
        return cls(_login(user), text_of(raw["author_association"]).upper(), text_of(raw["body"]), url, edited, account_id_of(user))


class Pull(NamedTuple):
    """PR の状態。`state` は `open`・`closed`、`merge_state` は GitHub の mergeStateStatus の小文字。"""

    number: int
    state: str
    merged: bool
    head_sha: str
    head_ref: str
    base_ref: str
    body: str
    merge_state: str
    author: str
    association: str
    merge_sha: str = ""  # マージで本流へ入ったコミット（squash・rebase のマージでは head と別）。マージされていなければ空
    queued: bool = False  # マージキューに入っているか（GraphQL で読んだときだけ分かる。REST では偽。#4409 の決定 8）
    title: str = ""  # PR の題（承認の質問の文を作る。#4446）
    cross_repository: bool = False  # head のブランチがフォークにあるか

    @property
    def authored(self) -> Authored:
        """PR の本文と書き手（信用の区分は `GitHub.trusts`）。"""
        return Authored(self.author, self.association, self.body)

    @classmethod
    def from_rest(cls, raw: dict) -> Pull:
        head, base = raw["head"], raw["base"]
        return cls(
            number=int(raw["number"]), state=text_of(raw["state"]).lower(), merged=bool(raw["merged"]), head_sha=text_of(head["sha"]),
            head_ref=text_of(head["ref"]), base_ref=text_of(base["ref"]), body=text_of(raw["body"]), title=text_of(raw.get("title")),
            merge_state=text_of(raw["mergeable_state"]).lower(), author=_login(raw["user"]),
            association=text_of(raw["author_association"]).upper(), merge_sha=text_of(raw.get("merge_commit_sha")) if raw["merged"] else "",
            cross_repository=_from_fork(head, base),
        )  # fmt: skip

    @classmethod
    def from_graphql(cls, raw: dict) -> Pull:
        return cls(
            number=int(raw["number"]), state="open" if text_of(raw["state"]).lower() == "open" else "closed", merged=bool(raw["merged"]),
            head_sha=text_of(raw["headRefOid"]), head_ref=text_of(raw["headRefName"]), base_ref=text_of(raw["baseRefName"]),
            body=text_of(raw["body"]), title=text_of(raw.get("title")), merge_state=text_of(raw["mergeStateStatus"]).lower(),
            author=_login(raw["author"]), association=text_of(raw["authorAssociation"]).upper(), queued=raw.get("isInMergeQueue") is True,
            merge_sha=text_of(raw["mergeCommit"].get("oid")) if isinstance(raw.get("mergeCommit"), dict) else "",
            cross_repository=bool(raw.get("isCrossRepository")),
        )  # fmt: skip


class _Reader(Protocol):
    """`merged_pulls` が使う読み取り（`ndf.adapters.github.GitHub` が満たす。import の輪を作らないための形）。"""

    def aliased(self, selections: dict[str, str]) -> dict[str, Any]: ...
    def repository(self, query: str, variables: dict[str, Any]) -> dict[str, Any]: ...
    def too_many_pages(self, what: str, pages: int, name: str) -> Exception: ...


# マージ済みの PR の 1 ページの中身（番号と、フォークから出たか）
_MERGED_FIELDS = "pageInfo { hasNextPage endCursor } nodes { number isCrossRepository }"
# head とマージ先で絞ったマージ済みの PR の続きのページ（新しい順）
_MERGED_PAGE = (
    "query($owner: String!, $name: String!, $head: String!, $base: String!, $after: String) {"
    f" repository(owner: $owner, name: $name) {{ pullRequests(headRefName: $head, baseRefName: $base, states: [MERGED],"
    f" first: {PER_PAGE}, after: $after, orderBy: {{field: CREATED_AT, direction: DESC}}) {{ {_MERGED_FIELDS} }} }} }}"
)


def _merged_selection(head: str, base: str) -> str:
    """`repository` の下の、最初のページの選択（`GitHub.aliased` へ渡す）。"""
    return (
        f"pullRequests(headRefName: {graphql_string(head)}, baseRefName: {graphql_string(base)}, states: [MERGED],"
        f" first: {PER_PAGE}, orderBy: {{field: CREATED_AT, direction: DESC}}) {{ {_MERGED_FIELDS} }}"
    )


def _own_number(hub: _Reader, head: str, base: str, conn: dict) -> Optional[int]:
    """最初のページ `conn` から、このリポジトリのブランチから出た PR が見つかるか末尾に着くまで読んだ番号（無ければ None）。"""
    pages = int(limits.value("github.list-pages", "count"))
    for page in range(1, pages + 1):
        number = next((int(n["number"]) for n in conn["nodes"] if not n["isCrossRepository"]), None)
        if number is not None or not conn["pageInfo"]["hasNextPage"]:
            return number
        if page < pages:
            variables = {"head": head, "base": base, "after": conn["pageInfo"]["endCursor"]}
            conn = hub.repository(_MERGED_PAGE, variables)["pullRequests"]
    raise hub.too_many_pages(f"ブランチ {head} のマージ済みの PR の一覧が", pages, "github.list-pages")


def merged_pulls(hub: _Reader, heads: Iterable[str], base: str) -> dict[str, int]:
    """head のブランチごとの、マージ先が `base` でフォークでないマージ済み PR の番号（新しいもの）。無い head はキーを持たない。

    最初のページは head をまとめて読む。一覧はリポジトリで絞れないので、フォークの PR しか無い head は続きを読み、
    `github.list-pages` ページを超えたら、未マージとは扱わずに失敗（`GitHubError`）にする。
    """
    first = hub.aliased({head: _merged_selection(head, base) for head in sorted(set(heads))})
    found = {head: _own_number(hub, head, base, conn) for head, conn in first.items() if conn}
    return {head: number for head, number in found.items() if number is not None}
