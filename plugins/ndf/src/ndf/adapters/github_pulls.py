"""GitHub の PR と本文の読み取りの型（`ndf.adapters.github` が同じ名前で読み直す）。"""

from __future__ import annotations

from typing import Any, NamedTuple

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
    cross_repository: bool = False  # head のブランチがフォークにあるか（GraphQL で読んだときだけ埋める）

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
