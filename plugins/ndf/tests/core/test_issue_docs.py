"""課題の文書の名前（`ndf.core.issue_docs`）のテスト（#4337 の詳細設計の「課題の文書の名前」）。"""

from __future__ import annotations

import pytest

from ndf.core import issue_docs


@pytest.mark.parametrize(
    ("path", "numbers"),
    [
        ("issues/issue-75-design.md", {75}),
        ("issues/issue-7-8-design-detail.md", {7, 8}),
        ("issues/sub/issue-1-design.md", set()),
        ("issues/issue-1-requirements.md", set()),
        ("docs/issue-1-design.md", set()),
    ],
)
def test_design_issues_reads_the_numbers_in_the_name(path: str, numbers: set[int]) -> None:
    assert issue_docs.design_issues(path) == frozenset(numbers)


def test_design_issues_compares_numbers_not_prefixes() -> None:
    assert 7 not in issue_docs.design_issues("issues/issue-75-design.md")


def test_plan_file_formats_the_issue_number() -> None:
    assert issue_docs.PLAN_FILE.format(number=12) == "issues/issue-12-plan.md"


@pytest.mark.parametrize(
    ("path", "numbers"),
    [
        ("issues/issue-7-8-plan.md", {7, 8}),
        ("issues/issue-78-plan.md", {78}),
        ("issues/issue-7-design.md", set()),
        ("issues/sub/issue-7-plan.md", set()),
        ("issues/issue-7-plan-detail.md", set()),
    ],
)
def test_plan_issues_reads_the_numbers_in_the_name(path: str, numbers: set[int]) -> None:
    assert issue_docs.plan_issues(path) == frozenset(numbers)


@pytest.mark.parametrize(
    ("path", "numbers"),
    [
        ("issues/issue-7-8-requirements.md", {7, 8}),
        ("issues/issue-78-requirements.md", {78}),
        ("issues/issue-7-design.md", set()),
        ("issues/sub/issue-7-requirements.md", set()),
        ("issues/issue-7-requirements-detail.md", set()),
    ],
)
def test_requirements_issues_reads_the_numbers_in_the_name(path: str, numbers: set[int]) -> None:
    assert issue_docs.requirements_issues(path) == frozenset(numbers)


def test_requirements_path_orders_the_numbers_and_names_the_representative() -> None:
    path = issue_docs.requirements_path([10, 9])
    assert path == "issues/issue-9-10-requirements.md"
    assert issue_docs.representative_issue(path) == 9


def test_representative_issue_rejects_a_path_without_numbers() -> None:
    with pytest.raises(ValueError):
        issue_docs.representative_issue("issues/issue-7-design.md")


def _origin(number: int, repo: str = "o/r") -> str:
    return f"元の Issue: https://github.com/{repo}/issues/{number}"


def test_origin_issues_reads_every_line_before_the_first_section() -> None:
    text = f"# 題\r\n\r\n{_origin(7)}\r\n{_origin(8, 'O/R')}  \r\n\r\n## 種類\r\n{_origin(9)}\r\n"
    assert issue_docs.origin_issues(text, "o/r") == frozenset({7, 8})


@pytest.mark.parametrize(
    "line",
    [
        _origin(7, "o/other"),
        "元の Issue: https://github.com/o/r/pull/7",
        "- 元の Issue: https://github.com/o/r/issues/7",
        _origin(7) + " ほか",
        "元の Issue: #7",
        f"```\n{_origin(7)}\n```",
    ],
)
def test_origin_issues_ignores_other_repositories_and_other_forms(line: str) -> None:
    assert issue_docs.origin_issues(f"# 題\n\n{line}\n", "o/r") == frozenset()
