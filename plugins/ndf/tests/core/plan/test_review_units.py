"""実装計画の単位の表の読み手（`ndf.core.plan.review_units_decide`）のテスト（#4369 の詳細設計の「単位の表の読み手」）。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ndf.core.plan import review_units_decide as units

SKILL = Path(__file__).resolve().parents[3] / "skills" / "implementation-plan" / "SKILL.md"
HEAD = "| 単位 | 含むタスク | 満たす受け入れ条件 |"
ORDERED = "| 単位 | 実行順 | 含むタスク | 満たす受け入れ条件 |"


def _table(head: str, *rows: str) -> str:
    rule = "|" + " --- |" * (head.count("|") - 1)
    return "\n".join([head, rule, *rows])


def _text(table: str, *, tasks: int = 2, before: str | None = None, heading: str = "## レビューの単位") -> str:
    parts = ["# 7: 計画", "", "## タスク分解", ""]
    parts += [f"### Task {n}: 作業 {n}\n" for n in range(1, tasks + 1)]
    parts += [heading, "", table, ""]
    if before is not None:
        timing = _table("| 時機 | 対象 | 理由 |", f"| 実装の前（準備のリファクタリング） | {before} | 測った |")
        parts += ["## リファクタリングの時機", "", timing, ""]
    return "\n".join(parts)


def _plan(text: str, *numbers: int) -> units.PlanDoc:
    numbers = numbers or (7,)
    name = "-".join(str(n) for n in numbers)
    return units.PlanDoc(f"issues/issue-{name}-plan.md", frozenset(numbers), text)


def _one(task_cell: str, criteria_cell: str, *, tasks: int = 4, numbers: tuple[int, ...] = (7,)) -> units.Unit:
    text = _text(_table(HEAD, f"| 本体 | {task_cell} | {criteria_cell} |"), tasks=tasks)
    (unit,) = units.lineup([_plan(text, *numbers)], {n: 9 for n in numbers}, list(numbers))
    return unit


def _names(*plans: units.PlanDoc, issues: tuple[int, ...] = (7,)) -> list[str]:
    return [u.name for u in units.lineup(plans, {n: 9 for n in issues}, list(issues))]


def _reasons(text: str, *numbers: int) -> tuple[str, ...]:
    plan = _plan(text, *numbers)
    with pytest.raises(units.UnitsUnreadable) as caught:
        units.lineup([plan], {n: 3 for n in plan.issues}, sorted(plan.issues))
    assert all(r.startswith(f"{plan.path}: ") for r in caught.value.reasons)
    return caught.value.reasons


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("## レビューの単位\n", True),
        ("### 2. レビューの単位（案）\n", True),
        ("# レビューの単位\n", False),
        ("```markdown\n## レビューの単位\n```\n", False),
        ("~~~\n## レビューの単位\n~~~\n", False),
        ("本文のレビューの単位\n", False),
        ("   ## レビューの単位 ##\n", True),
        ("  ```markdown\n## レビューの単位\n  ```\n", False),
        ("````markdown\n```\n## レビューの単位\n```\n````\n", False),
        ("```\n~~~\n## レビューの単位\n```\n", False),
    ],
)
def test_has_units_looks_for_a_heading_outside_code_blocks(text: str, found: bool) -> None:
    assert units.has_units(text) is found


def test_lineup_reads_a_row_as_a_unit() -> None:
    assert _one("Task 1・2・3・4", "1・2") == units.Unit("本体", 7, "issues/issue-7-plan.md", (1, 2, 3, 4), ((7, 1), (7, 2)), False)


@pytest.mark.parametrize("cell", ["Task 1〜4", "T1〜T4", "Task 1〜Task 4", "T1・2・3・4", "Task 1、2, 3・4", "Task 1〜4（本体・テスト）"])
def test_lineup_reads_the_task_cell_forms(cell: str) -> None:
    assert _one(cell, "無し").tasks == (1, 2, 3, 4)


@pytest.mark.parametrize(
    ("cell", "numbers", "expected"),
    [
        ("無し", (7,), ()),
        ("無し（準備）", (7,), ()),
        ("3", (7,), ((7, 3),)),
        ("1〜3", (7,), ((7, 1), (7, 2), (7, 3))),
        ("1(一部)、3", (7,), ((7, 1), (7, 3))),
        ("7:1, 2〜3・8:4・5", (7, 8), ((7, 1), (7, 2), (7, 3), (8, 4), (8, 5))),
        ("8:2〜3", (7, 8), ((8, 2), (8, 3))),
    ],
)
def test_lineup_reads_the_criteria_cell_forms(cell: str, numbers: tuple[int, ...], expected: tuple) -> None:
    assert _one("Task 1〜4", cell, numbers=numbers).criteria == expected


def test_lineup_keeps_the_table_order_without_the_order_column() -> None:
    table = _table(HEAD, "| 後半 | Task 2 | 無し |", "| 準備 | Task 1 | 無し |")
    assert _names(_plan(_text(table))) == ["後半", "準備"]


@pytest.mark.parametrize(
    ("before", "expected"),
    [("`a.py` を分ける", ["準備", "後半"]), ("しない（触る行が少ない）", ["後半", "準備"]), (None, ["後半", "準備"])],
)
def test_lineup_moves_the_first_task_unit_to_the_front_when_pre_refactoring(before: str | None, expected: list[str]) -> None:
    table = _table(HEAD, "| 後半 | Task 2 | 無し |", "| 準備 | Task 1 | 無し |")
    assert _names(_plan(_text(table, before=before))) == expected


def test_lineup_follows_the_order_column_even_when_pre_refactoring() -> None:
    table = _table(ORDERED, "| C | 2 | Task 3 | 無し |", "| A | 2 | Task 1 | 無し |", "| B | 1 | Task 2 | 無し |")
    assert _names(_plan(_text(table, tasks=3, before="`a.py` を分ける"))) == ["B", "C", "A"]


def test_lineup_orders_groups_by_issue_number_with_supplemented_units() -> None:
    table = _table(HEAD, "| A | Task 1 | 無し |", "| B | Task 2 | 無し |")
    plans = [_plan(_text(table), 20, 40), _plan("# 見出しの無い計画\n", 30)]
    found = units.lineup(plans, {n: 1 for n in (10, 20, 30, 40)}, [40, 30, 20, 10])
    assert [u.name for u in found] == ["issue-10", "A", "B", "issue-30"]
    assert [u.issue for u in found] == [10, 20, 20, 30]
    assert found[0] == units.Unit("issue-10", 10, "", (), (), True)


def test_lineup_reads_only_the_first_table_of_a_deeper_section() -> None:
    table = _table(HEAD, "| 本体 | Task 1・2 | 無し |") + "\n\n本文\n\n" + _table("| 別 |", "| 表 |")
    assert _names(_plan(_text(table, heading="### レビューの単位"))) == ["本体"]


@pytest.mark.parametrize(
    "table",
    [
        "単位 | 含むタスク | 満たす受け入れ条件\n--- | --- | ---\nA | Task 1 | 無し\nB | Task 2 | 無し",
        "\n".join([HEAD, "--- | --- | ---", "| A | Task 1 | 無し |", "B | Task 2 | 無し |"]),
    ],
)
def test_lineup_reads_a_table_without_the_outer_pipes(table: str) -> None:
    assert _names(_plan(_text(table))) == ["A", "B"]


@pytest.mark.parametrize("note", ["補足です。", "a \\| b は区切りでない。", "確認には次のコマンドを使う。\n`git diff | less`"])
def test_lineup_ends_the_table_at_a_line_without_a_cell_separator(note: str) -> None:
    table = _table(HEAD, "| A | Task 1・2 | 無し |") + "\n" + note
    assert _names(_plan(_text(table))) == ["A"]


@pytest.mark.parametrize("first", [["A | Task 1 | 無し"], []])
def test_lineup_rejects_rows_after_a_line_without_a_cell_separator(first: list[str]) -> None:
    table = "\n".join([HEAD, "--- | --- | ---", *first, "B", "C | Task 2 | 無し"])
    text = _text(table)
    line = text.split("\n").index("B") + 1
    assert f"{line} 行目" in "\n".join(_reasons(text))


@pytest.mark.parametrize("row", ["| C | Tasks 1 | 無し |", "| C | Task 2 |"])
def test_lineup_rejects_a_unit_like_row_after_a_line_without_a_cell_separator(row: str) -> None:
    table = _table(HEAD, "| A | Task 1・2 | 無し |", "B", row)
    assert "表の途中" in "\n".join(_reasons(_text(table)))


def test_lineup_reads_indented_and_closed_headings() -> None:
    text = _text(_table(HEAD, "| A | Task 1・2 | 無し |"), heading="  ## レビューの単位 ##")
    assert _names(_plan(text.replace("## タスク分解", "## タスク分解 ##"))) == ["A"]


GOOD = "| A | Task 1・2 | 1 |"


@pytest.mark.parametrize(
    ("table", "part"),
    [
        ("本文だけ", "表が無い"),
        (_table(HEAD), "行が無い"),
        (_table("| 単位 | 含むタスク |", "| A | Task 1・2 |"), "満たす受け入れ条件"),
        (_table(HEAD, "|  | Task 1・2 | 1 |"), "名前が空"),
        (_table(HEAD, "| A | Task 1 | 1 |", "| A | Task 2 | 2 |"), "名前が重なる"),
        (_table(HEAD, "| A |  | 1 |"), "含むタスク"),
        (_table(HEAD, "| A | 1・2 | 1 |"), "含むタスク"),
        (_table(HEAD, "| A | Task 1・5a | 1 |"), "含むタスク"),
        (_table(HEAD, "| A | Task 2〜1 | 1 |"), "含むタスク"),
        (_table(HEAD, "| A | Task 1・2・3 | 1 |"), "Task 3"),
        (_table(HEAD, "| A | Task 1〜999999999 | 1 |"), "Task 3"),
        (_table(HEAD, "| A | Task 1 | 1 |"), "Task 2"),
        (_table(HEAD, "| A | Task 1・2 | 1 |", "| B | Task 2 | 2 |"), "Task 2"),
        (_table(HEAD, "| A | Task 1・2 |  |"), "受け入れ条件"),
        (_table(HEAD, "| A | Task 1・2 | 5a |"), "5a"),
        (_table(HEAD, "| A | Task 1・2 | 無し・1 |"), "無し"),
        (_table(HEAD, "| A | Task 1・2 | 3〜1 |"), "3〜1"),
        (_table(HEAD, "| A | Task 1・2 | 4 |"), "4"),
        (_table(HEAD, "| A | Task 1・2 | 0 |"), "0"),
        (_table(HEAD, "| A | Task 1・2 | 1〜999999999 |"), "999999999"),
        (_table(HEAD, "| A | Task 1・2 | 9:1 |"), "#9"),
        (_table(f"{HEAD} 満たす受け入れ条件 |", "| A | Task 1・2 | 1 | 無し |"), "同じ列"),
        (_table(f"{ORDERED} 実行順 |", "| A | 1 | Task 1・2 | 1 | 2 |"), "同じ列"),
        (_table(ORDERED, "| A |  | Task 1・2 | 1 |"), "実行順"),
        (_table(ORDERED, "| A | 0 | Task 1・2 | 1 |"), "実行順"),
        (_table(ORDERED, "| A | -1 | Task 1・2 | 1 |"), "実行順"),
    ],
)
def test_lineup_rejects_unreadable_tables(table: str, part: str) -> None:
    assert part in "\n".join(_reasons(_text(table)))


def test_lineup_requires_the_issue_when_the_plan_has_two() -> None:
    assert "課題" in "\n".join(_reasons(_text(_table(HEAD, GOOD)), 7, 8))


def test_lineup_rejects_two_unit_headings() -> None:
    text = _text(_table(HEAD, GOOD)) + "\n## レビューの単位（続き）\n"
    assert "2 つ以上" in "\n".join(_reasons(text))


def test_lineup_does_not_read_a_table_of_a_later_section() -> None:
    text = _text("表はありません。\n\n## 別のセクション\n\n" + _table(HEAD, GOOD))
    assert "表が無い" in "\n".join(_reasons(text))


def test_lineup_collects_the_reasons_of_all_plans() -> None:
    plans = [_plan(_text("本文だけ"), 7), _plan(_text(_table(HEAD)), 8)]
    with pytest.raises(units.UnitsUnreadable) as caught:
        units.lineup(plans, {7: 1, 8: 1}, [7, 8])
    assert [r.split(": ")[0] for r in caught.value.reasons] == [p.path for p in plans]


def test_lineup_rejects_units_that_share_the_issue_and_the_name() -> None:
    plans = [_plan(_text(_table(HEAD, GOOD)), 7), _plan(_text(_table(HEAD, "| A | Task 1・2 | 7:1 |")), 7, 8)]
    with pytest.raises(units.UnitsUnreadable) as caught:
        units.lineup(plans, {7: 1, 8: 1}, [7, 8])
    assert "A" in "\n".join(caught.value.reasons)


@pytest.mark.parametrize(
    ("unit", "args"),
    [
        (
            units.Unit("A", 7, "p", (1,), ((8, 2), (7, 1)), False),
            ["--issue", "7", "--issue", "8", "--criteria", "8:2", "--criteria", "7:1"],
        ),
        (units.Unit("A", 7, "p", (1,), (), False), ["--issue", "7", "--no-criteria"]),
        (units.Unit("issue-9", 9, "", (), (), True), ["--issue", "9"]),
    ],
)
def test_acceptance_args_follow_the_unit(unit: units.Unit, args: list[str]) -> None:
    assert units.acceptance_args(unit) == args


def test_the_example_of_the_implementation_plan_skill_is_readable_once_named() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    template = re.search(r"^```markdown\n(.*?)^```$", skill, re.M | re.S).group(1)
    named, count = re.subn(r"^\| \| (Task )", r"| 本体 | \1", template, flags=re.M)
    assert count == 1
    (unit,) = units.lineup([_plan(named)], {7: 2}, [7])
    assert (unit.name, unit.tasks, unit.criteria) == ("本体", (1, 2), ((7, 1), (7, 2)))
