"""課題の文書の名前（`ndf.core.issue_docs`）のテスト（#4337 の詳細設計の「課題の文書の名前」）。"""

from __future__ import annotations

import pytest

from ndf.core import issue_docs


@pytest.mark.parametrize(
    ("path", "numbers"),
    [
        ("issues/issue-4337-design.md", {4337}),
        ("issues/issue-7-8-design-detail.md", {7, 8}),
        ("issues/sub/issue-1-design.md", set()),
        ("issues/issue-1-requirements.md", set()),
        ("docs/issue-1-design.md", set()),
    ],
)
def test_design_issues_reads_the_numbers_in_the_name(path: str, numbers: set[int]) -> None:
    assert issue_docs.design_issues(path) == frozenset(numbers)


def test_design_issues_compares_numbers_not_prefixes() -> None:
    assert 433 not in issue_docs.design_issues("issues/issue-4337-design.md")


def test_plan_file_formats_the_issue_number() -> None:
    assert issue_docs.PLAN_FILE.format(number=12) == "issues/issue-12-plan.md"
