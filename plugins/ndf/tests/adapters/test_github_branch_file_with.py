"""`GitHub.branch_file_with` の要求の形と結果（I-006 の前に今の振る舞いを固定する）。

要求は `GitHub.graphql` の口で受け、送った問い合わせ文と変数の並びを確かめる。問い合わせ文は空白をまとめて比べる
（空白の並びは GitHub の解釈を変えないため）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import pytest

from ndf.adapters import github
from ndf.adapters.github import FileAt, GitHubError

REPO = "o/r"
PATH = ".ndf/project.json"
VIEWER = {"login": "octocat", "databaseId": 1}
FIRST_HEAD = (
    "query($owner: String!, $name: String!, $expr: String!) { viewer { login databaseId } repository(owner: $owner, name: $name) {"
    " nameWithOwner object(expression: $expr) { ... on Blob { oid text } }"
)


def _flat(query: str) -> str:
    return " ".join(query.split())


def _first_query(body: str) -> str:
    return _flat(f"{FIRST_HEAD} {body} }} }}")


def _aliased_query(body: str) -> str:
    return _flat(f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {body} }} }}")


class Wire:
    """`graphql` の偽物。送った問い合わせ文と変数を `seen` に残し、i 回目の応答（または例外）を `answers` の i 番目で決める。"""

    def __init__(self, answers: list[Any]) -> None:
        self.seen: list[tuple[str, dict[str, Any]]] = []
        self.answers = answers

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.seen.append((_flat(query), dict(variables)))
        got = self.answers[len(self.seen) - 1]
        if isinstance(got, Exception):
            raise got
        return got


def _hub(wire: Wire) -> github.GitHub:
    from githubkit import GitHub as Client

    hub = github.GitHub(REPO, Client("t", http_cache=False, auto_retry=False))
    object.__setattr__(hub, "graphql", wire.graphql)
    return hub


def _first(extra: Optional[dict[str, Any]] = None, viewer: Any = VIEWER) -> dict[str, Any]:
    repo = {"nameWithOwner": REPO, "object": {"oid": "c1", "text": "{}"}, **(extra or {})}
    return {"viewer": viewer, "repository": repo}


def _later(values: dict[str, Any]) -> dict[str, Any]:
    return {"repository": values}


@pytest.fixture
def batch(monkeypatch) -> Callable[[int], None]:
    """`github.graphql-batch` の件数を差し替える。"""
    original = github.limits.value

    def set_size(size: int) -> None:
        monkeypatch.setattr(github.limits, "value", lambda name, key: size if name == "github.graphql-batch" else original(name, key))

    return set_size


VARS = {"owner": "o", "name": "r", "expr": f"main:{PATH}"}
ALIAS_VARS = {"owner": "o", "name": "r"}


def test_without_selections_sends_the_same_query_as_branch_file():
    wire = Wire([_first()])
    got = _hub(wire).branch_file_with("main", PATH, {})
    assert got == (FileAt(REPO, "{}", "c1"), {}, [])
    plain = Wire([_first()])
    _hub(plain).branch_file("main", PATH)
    assert wire.seen == plain.seen == [(_flat(github._BRANCH_FILE), VARS)]
    assert wire.seen[0][0] == _first_query("")


def test_selections_ride_on_the_first_request_with_aliases(batch):
    batch(5)
    wire = Wire([_first({"a0": {"x": 1}, "a1": None})])
    got = _hub(wire).branch_file_with("main", PATH, {"p": "pullRequest(number: 1) { x }", "q": 'ref(qualifiedName: "q") { y }', "r": "z"})
    assert got == (FileAt(REPO, "{}", "c1"), {"p": {"x": 1}, "q": None, "r": None}, [])
    assert wire.seen == [(_first_query('a0: pullRequest(number: 1) { x } a1: ref(qualifiedName: "q") { y } a2: z'), VARS)]


def test_the_rest_is_split_by_the_batch_size(batch):
    batch(2)
    sel = {k: f"s{k}" for k in "abcd"}
    wire = Wire([_first({"a0": "A"}), _later({"a0": "B", "a1": "C"}), _later({"a0": "D"})])
    found, got, unread = _hub(wire).branch_file_with("main", PATH, sel)
    assert found == FileAt(REPO, "{}", "c1")
    assert got == {"a": "A", "b": "B", "c": "C", "d": "D"}
    assert unread == []
    assert wire.seen == [
        (_first_query("a0: sa"), VARS),
        (_aliased_query("a0: sb a1: sc"), ALIAS_VARS),
        (_aliased_query("a0: sd"), ALIAS_VARS),
    ]


def test_batch_of_one_puts_no_selection_on_the_first_request(batch):
    batch(1)
    wire = Wire([_first(), _later({"a0": "A"})])
    _, got, unread = _hub(wire).branch_file_with("main", PATH, {"a": "sa"})
    assert got == {"a": "A"} and unread == []
    assert wire.seen == [(_first_query(""), VARS), (_aliased_query("a0: sa"), ALIAS_VARS)]


def test_a_refused_later_request_is_recorded_and_the_rest_continue(batch):
    batch(2)
    refused = GitHubError("GraphQL の誤り")
    wire = Wire([_first({"a0": "A"}), refused, _later({"a0": "D"})])
    _, got, unread = _hub(wire).branch_file_with("main", PATH, {k: f"s{k}" for k in "abcd"})
    assert got == {"a": "A", "b": None, "c": None, "d": "D"}
    assert unread == [github.Unread(("b", "c"), refused)]
    assert len(wire.seen) == 3


@pytest.mark.parametrize(
    "exc",
    [
        GitHubError("届かない", unreached=True),
        GitHubError("5xx", status=502),
        GitHubError("上限", status=403, retry_after=10.0, quota="graphql"),
        GitHubError("認証", status=401),
        GitHubError("権限", status=403),
    ],
    ids=["unreached", "5xx", "quota", "401", "403"],
)
def test_a_failure_that_blocks_the_rest_stops_and_gathers_one_unread(batch, exc):
    batch(2)
    wire = Wire([_first({"a0": "A"}), exc])
    _, got, unread = _hub(wire).branch_file_with("main", PATH, {k: f"s{k}" for k in "abcde"})
    assert got == {"a": "A", "b": None, "c": None, "d": None, "e": None}
    assert unread == [github.Unread(("b", "c", "d", "e"), exc)]
    assert len(wire.seen) == 2


def test_a_failure_of_the_first_request_raises(batch):
    batch(2)
    failed = GitHubError("GraphQL の誤り")
    wire = Wire([failed])
    with pytest.raises(GitHubError) as caught:
        _hub(wire).branch_file_with("main", PATH, {"a": "sa", "b": "sb"})
    assert caught.value is failed
    assert len(wire.seen) == 1


def test_a_null_repository_raises():
    wire = Wire([{"viewer": VIEWER, "repository": None}])
    with pytest.raises(GitHubError):
        _hub(wire).branch_file_with("main", PATH, {})


@pytest.mark.parametrize(
    ("viewer", "want"),
    [(VIEWER, github.Viewer("octocat", 1)), ({}, github.Viewer("", 0)), (None, github.Viewer("", 0))],
    ids=["read", "empty", "null"],
)
def test_viewer_after_branch_file_with_sends_no_request(viewer, want):
    wire = Wire([_first(viewer=viewer)])
    hub = _hub(wire)
    hub.branch_file_with("main", PATH, {})
    assert hub.viewer() == want
    assert len(wire.seen) == 1
