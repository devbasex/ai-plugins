"""現状固定: issues_gh のページ送り（closed_since・milestone_rows）。リファクタリングの前の振る舞いをそのまま記録する。"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from ndf.adapters.github import GitHubError
from ndf.contracts import limits
from ndf.domains.backlog import issues_gh

PAGES = int(limits.value("github.list-pages", "count"))


class PagedRepo:
    """`repository(query, variables)` だけを持つ偽物。`key` の下へページを順に返し、渡された変数を記録する。"""

    def __init__(self, key: str, pages: list[list[dict[str, Any]]], endless: bool = False) -> None:
        self.key, self.pages, self.endless = key, pages, endless
        self.calls: list[dict[str, Any]] = []

    def repository(self, query: str, variables: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        self.calls.append(dict(variables or {}))
        i = len(self.calls) - 1
        nodes = self.pages[i] if i < len(self.pages) else []
        more = self.endless or i + 1 < len(self.pages)
        return {self.key: {"pageInfo": {"hasNextPage": more, "endCursor": f"c{i + 1}"}, "nodes": nodes}}


SINCE = "2026-11-01T00:00:00Z"


def test_closed_since_filters_by_closed_at_and_reads_milestone():
    gh = PagedRepo(
        "issues",
        [
            [
                {"number": 1, "closedAt": "2026-10-31T23:59:59Z", "milestone": {"title": "01 a"}},
                {"number": "2", "closedAt": SINCE, "milestone": {"title": "01 a"}},
                {"number": 3, "closedAt": "2026-11-02T00:00:00Z", "milestone": None},
                {"number": 4, "closedAt": None, "milestone": {"title": "x"}},
            ]
        ],
    )
    got = issues_gh.closed_since(gh, SINCE)
    # 閉じた時刻が since と同じものは含む。closedAt が null（空文字）は since より前として外れる
    assert got == [{"number": 2, "milestone": "01 a"}, {"number": 3, "milestone": None}]
    assert gh.calls == [{"since": SINCE, "after": None}]


def test_closed_since_follows_pages_with_end_cursor():
    gh = PagedRepo("issues", [[{"number": n, "closedAt": "2026-11-05T00:00:00Z", "milestone": None}] for n in (1, 2, 3)])
    got = issues_gh.closed_since(gh, SINCE)
    assert [r["number"] for r in got] == [1, 2, 3]
    assert gh.calls == [{"since": SINCE, "after": None}, {"since": SINCE, "after": "c1"}, {"since": SINCE, "after": "c2"}]


def test_closed_since_empty_page():
    assert issues_gh.closed_since(PagedRepo("issues", [[]]), SINCE) == []


def test_closed_since_raises_after_page_limit():
    gh = PagedRepo("issues", [], endless=True)
    with pytest.raises(GitHubError, match=f"閉じた Issue の一覧が {PAGES} ページを超えた"):
        issues_gh.closed_since(gh, SINCE)
    assert len(gh.calls) == PAGES


def test_milestone_rows_follows_pages_and_reads_description():
    gh = PagedRepo(
        "milestones",
        [
            [{"number": 1, "title": "01 a", "description": "d1"}, {"number": "2", "title": "02 b", "description": None}],
            [{"number": 3, "title": "03 c"}, {"number": 4, "title": "01 a", "description": "後勝ち"}],
        ],
    )
    got = issues_gh.milestone_rows(gh)
    assert got == {
        "01 a": {"number": 4, "description": "後勝ち"},
        "02 b": {"number": 2, "description": ""},
        "03 c": {"number": 3, "description": ""},
    }
    assert gh.calls == [{"after": None}, {"after": "c1"}]


def test_milestones_maps_title_to_number():
    gh = PagedRepo("milestones", [[{"number": 1, "title": "01 a"}, {"number": 2, "title": "02 b"}]])
    assert issues_gh.milestones(gh) == {"01 a": 1, "02 b": 2}


def test_milestone_rows_raises_after_page_limit():
    gh = PagedRepo("milestones", [], endless=True)
    with pytest.raises(GitHubError, match=f"マイルストーンの一覧が {PAGES} ページを超えた"):
        issues_gh.milestone_rows(gh)
    assert len(gh.calls) == PAGES
