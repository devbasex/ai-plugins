"""release_lib/included.py の pr_fields: GraphQL の別名で PR をまとめて読む（現状固定）。

`gh_call.gh` だけを差し替え、問い合わせの組み立て・ページ分け・応答の整形・失敗の理由を固定する。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import gh_call  # noqa: E402
from release_lib import included  # noqa: E402


def pr(n: int, **extra) -> dict:
    return {"number": n, "title": f"t{n}", "closingIssuesReferences": {"nodes": [{"number": n + 1000}]}, **extra}


class Fake:
    """呼び出しの argv を残し、問い合わせの別名 pN に answer(n) を返す。"""

    def __init__(self, answer=pr, result=None):
        self.answer = answer
        self.result = result
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, args, stdin=None, cwd=None):
        self.calls.append((list(args), cwd))
        if self.result is not None:
            return self.result
        query = args[-1]
        nums = [int(m) for m in re.findall(r"\bp(\d+): pullRequest", query)]
        repo = {f"p{n}": self.answer(n) for n in nums}
        return gh_call.GhResult(0, json.dumps({"data": {"repository": repo}}), "")

    def queries(self) -> list[str]:
        return [a[-1] for a, _ in self.calls]


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(included.gh_call, "gh", f)
    return f


def test_reads_in_one_call_and_flattens_closing_issues(fake):
    out, why = included.pr_fields([3, "5"], cwd="/x")
    assert why == ""
    assert out == {
        3: {"number": 3, "title": "t3", "closingIssuesReferences": [{"number": 1003}]},
        5: {"number": 5, "title": "t5", "closingIssuesReferences": [{"number": 1005}]},
    }
    assert len(fake.calls) == 1
    args, cwd = fake.calls[0]
    assert cwd == "/x"
    assert args[:-1] == ["api", "graphql", "-F", "owner={owner}", "-F", "name={repo}", "-f"]
    q = args[-1]
    assert q.startswith("query=query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { ")
    assert f"p3: pullRequest(number: 3) {{ {included._FIELDS} }}" in q
    assert "comments(last: 100)" not in q


def test_duplicates_are_read_once_in_first_seen_order(fake):
    out, _ = included.pr_fields([7, 2, 7, "2"])
    assert list(out) == [7, 2]
    assert re.findall(r"\bp(\d+):", fake.queries()[0]) == ["7", "2"]
    assert fake.calls[0][1] is None


def test_empty_numbers_make_no_call(fake):
    assert included.pr_fields([]) == ({}, "")
    assert fake.calls == []


def test_pages_by_per_page(fake, monkeypatch):
    monkeypatch.setattr(included, "PER_PAGE", 2)
    out, _ = included.pr_fields([1, 2, 3, 4, 5])
    assert list(out) == [1, 2, 3, 4, 5]
    assert [re.findall(r"\bp(\d+):", q) for q in fake.queries()] == [["1", "2"], ["3", "4"], ["5"]]


def test_comments_are_requested_and_flattened(monkeypatch):
    f = Fake(lambda n: pr(n, comments={"nodes": [{"body": "a"}, {"body": "b"}]}))
    monkeypatch.setattr(included.gh_call, "gh", f)
    out, _ = included.pr_fields([4], comments=True)
    assert out[4]["comments"] == [{"body": "a"}, {"body": "b"}]
    assert f"{included._FIELDS} comments(last: 100) {{ nodes {{ body }} }}" in f.queries()[0]


def test_comments_missing_become_empty_list(monkeypatch):
    f = Fake(lambda n: {"number": n, "closingIssuesReferences": None, "comments": None})
    monkeypatch.setattr(included.gh_call, "gh", f)
    out, _ = included.pr_fields([4], comments=True)
    assert out[4] == {"number": 4, "closingIssuesReferences": [], "comments": []}


def test_without_comments_keeps_comments_key_untouched(monkeypatch):
    f = Fake(lambda n: {"number": n, "comments": {"nodes": [1]}})
    monkeypatch.setattr(included.gh_call, "gh", f)
    out, _ = included.pr_fields([4])
    assert out[4] == {"number": 4, "comments": {"nodes": [1]}, "closingIssuesReferences": []}


def test_closing_issues_without_nodes_become_empty_list(monkeypatch):
    f = Fake(lambda n: {"number": n, "closingIssuesReferences": {"nodes": None}})
    monkeypatch.setattr(included.gh_call, "gh", f)
    out, _ = included.pr_fields([4])
    assert out[4]["closingIssuesReferences"] == []


def test_non_pr_numbers_are_none(monkeypatch):
    f = Fake(lambda n: None if n == 9 else ("x" if n == 8 else pr(n)))
    monkeypatch.setattr(included.gh_call, "gh", f)
    out, why = included.pr_fields([1, 8, 9])
    assert why == ""
    assert out[8] is None and out[9] is None
    assert out[1]["number"] == 1


def test_missing_alias_is_none(monkeypatch):
    f = Fake(result=gh_call.GhResult(0, json.dumps({"data": {"repository": {}}}), ""))
    monkeypatch.setattr(included.gh_call, "gh", f)
    assert included.pr_fields([1]) == ({1: None}, "")


@pytest.mark.parametrize(
    ("result", "why"),
    [
        (gh_call.GhResult(127, "", "not found"), "gh が無い"),
        (gh_call.GhResult(1, "", "  HTTP 502  \n"), "HTTP 502"),
        (gh_call.GhResult(1, "plain out", ""), "plain out"),
        (gh_call.GhResult(1, "", ""), "応答を読めない"),
        (gh_call.GhResult(1, "", "e" * 400), "e" * 300),
        (gh_call.GhResult(0, json.dumps({"data": None}), ""), json.dumps({"data": None})),
        (gh_call.GhResult(0, json.dumps({"data": {"repository": None}}), ""), json.dumps({"data": {"repository": None}})),
        (gh_call.GhResult(0, json.dumps({"errors": []}), ""), json.dumps({"errors": []})),
        (gh_call.GhResult(0, json.dumps({"data": {"repository": [1]}}), ""), json.dumps({"data": {"repository": [1]}})),
    ],
)
def test_unreadable_returns_none_and_reason(monkeypatch, result, why):
    monkeypatch.setattr(included.gh_call, "gh", Fake(result=result))
    assert included.pr_fields([1]) == (None, why)


def test_failure_on_later_page_discards_earlier_pages(monkeypatch):
    calls = []

    def gh(args, stdin=None, cwd=None):
        calls.append(args)
        if len(calls) == 2:
            return gh_call.GhResult(1, "", "boom")
        return Fake()(args)

    monkeypatch.setattr(included, "PER_PAGE", 1)
    monkeypatch.setattr(included.gh_call, "gh", gh)
    assert included.pr_fields([1, 2, 3]) == (None, "boom")
    assert len(calls) == 2
