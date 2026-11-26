"""`github.Pull` の組み立て（`from_rest`・`from_graphql`）の現状固定（I-005）。期待値は今の実装の出力である。

項目の取り違えが分かるよう、各項目へ互いに違う値を入れて `Pull` の全項目を比べる。
"""

from __future__ import annotations

import pytest

from ndf.adapters import github

_REST = {
    "number": "7",
    "state": "OPEN",
    "merged": True,
    "head": {"sha": "head-sha", "ref": "head-ref"},
    "base": {"ref": "base-ref"},
    "body": "本文",
    "mergeable_state": "CLEAN",
    "user": {"login": "writer"},
    "author_association": "member",
    "merge_commit_sha": "merge-sha",
    "title": "題",
}
_GRAPHQL = {
    "number": "7",
    "state": "OPEN",
    "merged": True,
    "headRefOid": "head-sha",
    "headRefName": "head-ref",
    "baseRefName": "base-ref",
    "body": "本文",
    "mergeStateStatus": "CLEAN",
    "author": {"login": "writer"},
    "authorAssociation": "member",
    "mergeCommit": {"oid": "merge-sha"},
    "title": "題",
    "isInMergeQueue": True,
    "isCrossRepository": True,
}
_EXPECTED = {
    "number": 7,
    "state": "open",
    "merged": True,
    "head_sha": "head-sha",
    "head_ref": "head-ref",
    "base_ref": "base-ref",
    "body": "本文",
    "merge_state": "clean",
    "author": "writer",
    "association": "MEMBER",
    "merge_sha": "merge-sha",
    "queued": False,
    "title": "題",
    "cross_repository": False,
}
# 文字列でない値（null）は空文字になる
_EMPTIED = {"head_sha": "", "head_ref": "", "base_ref": "", "body": "", "merge_state": "", "author": "", "association": "", "title": ""}


def test_from_rest_fills_every_field() -> None:
    assert github.Pull.from_rest(_REST)._asdict() == _EXPECTED


def test_from_rest_ignores_cross_repository_keys() -> None:
    assert github.Pull.from_rest({**_REST, "isCrossRepository": True}).cross_repository is False


def test_from_rest_empties_null_values_and_allows_missing_optional_keys() -> None:
    raw = {
        "number": 7,
        "state": None,
        "merged": False,
        "head": {"sha": None, "ref": None},
        "base": {"ref": None},
        "body": None,
        "mergeable_state": None,
        "user": None,
        "author_association": None,
    }
    expected = {**_EXPECTED, **_EMPTIED, "state": "", "merged": False, "merge_sha": ""}
    assert github.Pull.from_rest(raw)._asdict() == expected


def test_from_graphql_fills_every_field() -> None:
    assert github.Pull.from_graphql(_GRAPHQL)._asdict() == {**_EXPECTED, "queued": True, "cross_repository": True}


@pytest.mark.parametrize(("state", "expected"), [("OPEN", "open"), ("CLOSED", "closed"), ("MERGED", "closed"), (None, "closed")])
def test_from_graphql_reduces_state_to_open_or_closed(state: str | None, expected: str) -> None:
    assert github.Pull.from_graphql({**_GRAPHQL, "state": state}).state == expected


def test_from_graphql_empties_null_values_and_allows_missing_optional_keys() -> None:
    raw = {
        "number": 7,
        "state": "OPEN",
        "merged": False,
        "headRefOid": None,
        "headRefName": None,
        "baseRefName": None,
        "body": None,
        "mergeStateStatus": None,
        "author": None,
        "authorAssociation": None,
    }
    expected = {**_EXPECTED, **_EMPTIED, "merged": False, "merge_sha": ""}
    assert github.Pull.from_graphql(raw)._asdict() == expected
