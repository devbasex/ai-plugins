"""単位のファイルの読み書き（`ndf.core.plan.units_files`）のテスト（issues/issue-3702-design-detail.md の「単位のファイルの読み書き」）。"""

from __future__ import annotations

import json
from pathlib import Path

import git_sandbox
import pytest

from ndf.core.plan import review_units_decide
from ndf.core.plan import units_decide as ud
from ndf.core.plan import units_files as uf

from . import _plan_helpers as H

LIMITS = ud.Limits(target=400, max=1000)


def _task(tid: str, *, lines: int = 10, files=(), after=(), criteria=(), **extra):
    return {
        "id": tid,
        "title": f"作業 {tid}",
        "files": list(files),
        "lines": lines,
        "criteria": list(criteria),
        "after": list(after),
        **extra,
    }


def _unit(name: str, *tasks: str, after=(), **extra):
    return {"name": name, "tasks": list(tasks), "after": list(after), **extra}


def _doc(issues=(7,), tasks=None, units=None):
    tasks = tasks if tasks is not None else [_task("T1")]
    units = units if units is not None else [_unit("A", *(t["id"] for t in tasks))]
    return {"version": 1, "issues": list(issues), "tasks": tasks, "units": units}


def _put(root: Path, name: str, content) -> str:
    path = root / "issues" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return f"issues/{name}"


def _units(root: Path, key: str, **doc) -> str:
    doc.setdefault("issues", [int(n) for n in key.split("-")])
    return _put(root, f"issue-{key}-plan.units.json", _doc(**doc))


def _design(root: Path, key: str, *rows: str) -> None:
    table = "\n".join(["| 課題 | 扱い | 統合先 |", "| --- | --- | --- |", *rows])
    _put(root, f"issue-{key}-design.md", f"# 設計\n\n## 設計の結果\n\n{table}\n")


def _plan(*numbers: int) -> str:
    tasks = "".join(f"### Task {n}: 作業 {n}\n\n本文。\n\n" for n in numbers)
    return f"# 実装計画\n\n## タスク分解\n\n{tasks}## リスクと対処\n\n無し\n"


# --- find・load ------------------------------------------------------------------------------


def test_find_looks_up_issues_by_the_numbers_in_the_name(tmp_path):
    joined = _units(tmp_path, "7-8")
    other = _units(tmp_path, "78")
    tree = uf.Tree(tmp_path)

    assert uf.find(tree, [7, 8]) == {7: joined, 8: joined}
    assert uf.find(tree, [78]) == {78: other}
    assert uf.find(tree, [9]) == {}


def test_find_refuses_one_issue_in_two_files(tmp_path):
    _units(tmp_path, "7")
    _units(tmp_path, "7-8")

    with pytest.raises(uf.UnitsFileError) as stopped:
        uf.find(uf.Tree(tmp_path), [7])

    assert "#7" in str(stopped.value)


def test_load_parses_a_file_that_matches_the_schema(tmp_path):
    path = _units(tmp_path, "7", tasks=[_task("T1", lines=5, criteria=["7:1"])])

    got = uf.load(uf.Tree(tmp_path), path)

    assert got.path == path
    assert got.issues == (7,)
    assert got.tasks[0].criteria == ("7:1",)


@pytest.mark.parametrize(
    "content, word",
    [
        ("{\n  壊れた", "2 行目"),
        (_doc(tasks=[{k: v for k, v in _task("T1").items() if k != "lines"}]), "タスク T1"),
        (_doc(tasks=[_task("T1", files=["plugins/"])]), "タスク T1"),
        (_doc(units=[_unit("A", "T1", "T1")]), "units/0/tasks"),
        ({**_doc(), "order": 1}, "additionalProperties"),
        (_doc(issues=(8,)), "ファイル名の番号"),
    ],
)
def test_load_refuses_a_broken_file_with_its_name_and_place(tmp_path, content, word):
    path = _put(tmp_path, "issue-7-plan.units.json", content)

    with pytest.raises(uf.UnitsFileError) as stopped:
        uf.load(uf.Tree(tmp_path), path)

    assert path in str(stopped.value)
    assert word in str(stopped.value)


def test_load_refuses_a_missing_path_a_directory_and_a_symlink(tmp_path):
    real = _units(tmp_path, "7")
    (tmp_path / "issues" / "issue-8-plan.units.json").mkdir()
    (tmp_path / "issues" / "issue-9-plan.units.json").symlink_to(tmp_path / real)
    tree = uf.Tree(tmp_path)

    for number, word in ((8, "ディレクトリ"), (9, "シンボリックリンク"), (10, "無い")):
        with pytest.raises(uf.UnitsFileError) as stopped:
            uf.load(tree, f"issues/issue-{number}-plan.units.json")
        assert word in str(stopped.value)


def test_commit_tree_reads_the_commit_and_not_the_working_tree(tmp_path):
    root = H.make_repo(tmp_path / "repo")
    path = _units(root, "7")
    (root / "issues" / "issue-8-plan.units.json").symlink_to("issue-7-plan.units.json")
    git_sandbox.sh(root, "add", "issues", identity=True)
    git_sandbox.sh(root, "commit", "-q", "-m", "units", identity=True)
    (root / path).write_text("壊れた", encoding="utf-8")
    _units(root, "9")
    tree = uf.Tree(root, "HEAD")

    assert uf.find(tree, [7, 9]) == {7: path}
    assert uf.load(tree, path).issues == (7,)
    with pytest.raises(uf.UnitsFileError) as stopped:
        uf.load(tree, "issues/issue-8-plan.units.json")
    assert "シンボリックリンク" in str(stopped.value)


# --- limits_of -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "project, expected",
    [
        ({}, (400, 1000)),
        ({"workflow": {}}, (400, 1000)),
        ({"workflow": {"review_units": {"target_lines": 200}}}, (200, 1000)),
        ({"workflow": {"review_units": {"max_lines": 3000}}}, (400, 3000)),
        ({"workflow": {"review_units": {"target_lines": 5, "max_lines": 6}}}, (5, 6)),
    ],
)
def test_limits_of_prefers_the_declaration_over_the_defaults(project, expected):
    assert uf.limits_of(project) == ud.Limits(*expected)


@pytest.mark.parametrize(
    "declared, word",
    [
        ({"target_lines": 1000}, "workflow.review_units"),
        ({"target_lines": 500, "max_lines": 500}, "500"),
        ({"max_lines": "1000"}, "max_lines"),
        ({"target_lines": 0}, "target_lines"),
        ({"limit": 1}, "limit"),
    ],
)
def test_limits_of_refuses_a_wrong_declaration(declared, word):
    with pytest.raises(uf.UnitsFileError) as stopped:
        uf.limits_of({"workflow": {"review_units": declared}})

    assert word in str(stopped.value)


# --- collect・check --------------------------------------------------------------------------


def test_collect_sorts_issues_into_files_omitted_excluded_and_missing(tmp_path):
    path = _units(tmp_path, "7")
    _design(tmp_path, "8-9", "| #8 | マージする | #7 |", "| #9 | 閉じる | 無し |")
    _design(tmp_path, "10", "| #10 | 実装する | 無し |")

    got = uf.collect(uf.Tree(tmp_path), [7, 8, 9, 10, 11], [11])

    assert [f.path for f in got.files] == [path]
    assert got.omitted == (11,)
    assert got.excluded == (8, 9)
    assert got.missing == (10,)


def test_collect_ignores_the_detail_design_for_handlings(tmp_path):
    _put(tmp_path, "issue-8-design-detail.md", "## 設計の結果\n\n| 課題 | 扱い |\n| --- | --- |\n| #8 | 閉じる |\n")

    got = uf.collect(uf.Tree(tmp_path), [8], [])

    assert got.excluded == ()
    assert got.missing == (8,)


def test_check_treats_an_omitted_issue_as_one_unit_without_size_findings(tmp_path):
    got = uf.check(uf.Tree(tmp_path), [7, 8], [7, 8], LIMITS, {7: 1, 8: 1}, False)

    assert got.missing == ()
    assert [v.name for v in got.judgement.views] == ["issue-7", "issue-8"]
    assert got.judgement.violations == () and got.judgement.warnings == ()


def test_check_reports_a_missing_file_and_still_judges_the_rest(tmp_path):
    _units(tmp_path, "7", tasks=[_task("T1", lines=1001)])

    got = uf.check(uf.Tree(tmp_path), [7, 8], [], LIMITS, {7: 1, 8: 1}, False)

    assert got.missing == (8,)
    assert [v.rule for v in got.judgement.violations] == ["U6"]
    assert got.task_gaps == () and got.stale_tables == ()


def test_check_applies_the_given_limits(tmp_path):
    _units(tmp_path, "7", tasks=[_task("T1", lines=30)])
    limits = uf.limits_of({"workflow": {"review_units": {"target_lines": 10, "max_lines": 20}}})

    got = uf.check(uf.Tree(tmp_path), [7], [], limits, {7: 0}, False)

    assert [v.rule for v in got.judgement.violations] == ["U6"]


def test_check_docs_passes_after_the_tables_are_rendered(tmp_path):
    _units(tmp_path, "7", tasks=[_task("T1"), _task("T2")])
    tree = uf.Tree(tmp_path)
    _put(tmp_path, "issue-7-plan.md", _plan(1, 2))
    before = uf.check(tree, [7], [], LIMITS, {7: 0}, True)
    _put(tmp_path, "issue-7-plan.md", uf.render_tables(_plan(1, 2), before.judgement.views))

    after = uf.check(tree, [7], [], LIMITS, {7: 0}, True)

    assert before.stale_tables == ("issues/issue-7-plan.md",)
    assert after.stale_tables == () and after.task_gaps == ()


def test_check_docs_reports_a_hand_edited_table(tmp_path):
    _units(tmp_path, "7")
    tree = uf.Tree(tmp_path)
    views = uf.check(tree, [7], [], LIMITS, {7: 0}, False).judgement.views
    rendered = uf.render_tables(_plan(1), views)
    _put(tmp_path, "issue-7-plan.md", rendered.replace("| A | Task 1 |", "| A | Task 1・2 |"))

    assert uf.check(tree, [7], [], LIMITS, {7: 0}, True).stale_tables == ("issues/issue-7-plan.md",)


def test_check_docs_reports_tasks_on_one_side_and_duplicate_headings(tmp_path):
    _units(tmp_path, "7", tasks=[_task("T1"), _task("T3")])
    _put(tmp_path, "issue-7-plan.md", _plan(1, 2, 2) + "```\n### Task 9: 例\n```\n")

    gaps = uf.check(uf.Tree(tmp_path), [7], [], LIMITS, {7: 0}, True).task_gaps

    assert gaps == (uf.TaskGap("issues/issue-7-plan.md", duplicated=("T2",), only_plan=("T2",), only_units=("T3",)),)


def test_check_docs_reports_a_units_file_without_its_plan(tmp_path):
    _units(tmp_path, "7")

    got = uf.check(uf.Tree(tmp_path), [7], [], LIMITS, {7: 0}, True)

    assert got.task_gaps == (uf.TaskGap("issues/issue-7-plan.md", duplicated=(), only_plan=(), only_units=("T1",)),)


# --- render_tables・differs ------------------------------------------------------------------


def _two_files():
    first = ud.parse(
        _doc(
            tasks=[_task("T1", lines=3, criteria=["7:2", "7:1"]), _task("T2", lines=4), _task("T3", lines=5)],
            units=[
                _unit("形", "T1", "T2", actual_lines=12),
                _unit("読む|側", "T3", after=[{"unit": "形", "reason": "形を読む"}]),
            ],
        ),
        "issues/issue-7-plan.units.json",
    )
    second = ud.parse(
        _doc(issues=(8,), units=[_unit("形", "T1", after=[{"unit": "形", "issue": 7, "reason": "共有の型"}])]),
        "issues/issue-8-plan.units.json",
    )
    return first, ud.judge([first, second], LIMITS, {7: 2, 8: 0}).views


def _rows(text: str, heading: str) -> list[str]:
    section = text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
    return [line for line in section.split("\n") if line.startswith("|")][2:]


def test_render_tables_writes_both_tables_for_the_units_of_the_file():
    first, views = _two_files()

    text = uf.render_tables(_plan(1, 2, 3), views, first.path)

    assert _rows(text, "レビューの単位") == [
        "| 形 | Task 1・2 | 7:1・7:2 | 7 | 1 | 無し | 1 | 12 |",
        "| 読む\\|側 | Task 3 | 無し | 5 | 2 | 形（形を読む） | 2 |  |",
    ]
    assert _rows(text, "並列と直列") == [
        "| 1 | 形 | 形: 無し | 無し |",
        "| 2 | 読む\\|側 | 読む\\|側: 形（#8） | 形 → 読む\\|側: 形を読む |",
    ]
    assert text.index("## タスク分解") < text.index("## レビューの単位") < text.index("## 並列と直列") < text.index("## リスクと対処")


def test_render_tables_names_a_dependency_in_another_file_with_its_issue():
    _, views = _two_files()

    text = uf.render_tables(_plan(1), views, "issues/issue-8-plan.units.json")

    assert _rows(text, "レビューの単位") == ["| 形 | Task 1 | 無し | 10 | 2 | 形（#7。共有の型） | 3 |  |"]
    assert _rows(text, "並列と直列") == ["| 2 | 形 | 形: 読む\\|側（#7） | 形（#7） → 形: 共有の型 |"]


def test_render_tables_is_stable_and_keeps_the_prose_around_the_tables():
    first, views = _two_files()
    plan = (
        "# 実装計画\n\n## レビューの単位\n\n前の説明。\n\n| 単位 | 含むタスク | 満たす受け入れ条件 | 実際の行数 |\n"
        "| --- | --- | --- | --- |\n| 形 | Task 1 | 無し | 999 |\n\n後の説明。\n\n"
        "| 別の表 |\n| --- |\n| 残る |\n\n## 並列と直列\n\n## 完了の定義\n\n- [ ] 済\n"
    )

    once = uf.render_tables(plan, views, first.path)

    assert uf.render_tables(once, views, first.path) == once
    assert "999" not in once and "| 12 |" in once
    for kept in ("前の説明。\n\n| 単位 |", "後の説明。\n\n| 別の表 |\n| --- |\n| 残る |", "## 完了の定義\n\n- [ ] 済\n"):
        assert kept in once
    assert once.count("## レビューの単位") == 1 and once.count("## 並列と直列") == 1


def test_render_tables_keeps_other_tables_when_there_is_no_generated_table():
    first, views = _two_files()
    note = "| 単位 | 備考 |\n| --- | --- |\n| 形 | 残る |"
    plan = f"# 実装計画\n\n## レビューの単位\n\n{note}\n\n## 並列と直列\n\n### 補足\n\n{note}\n"

    once = uf.render_tables(plan, views, first.path)

    assert once.count(note) == 2
    assert once.index("| 単位 | 含むタスク |") < once.index(note)
    assert once.index("| 実行順 |") < once.index("### 補足")
    assert uf.render_tables(once, views, first.path) == once


def test_render_tables_leaves_units_that_touch_the_same_file_out_of_the_parallel_column():
    unit_file = ud.parse(
        _doc(tasks=[_task("T1", files=["a.py"]), _task("T2", files=["a.py"])], units=[_unit("A", "T1"), _unit("B", "T2")]),
        "issues/issue-7-plan.units.json",
    )

    text = uf.render_tables(_plan(1, 2), ud.judge([unit_file], LIMITS, {7: 0}).views)

    assert _rows(text, "並列と直列") == ["| 1 | A、B | A: 無し<br>B: 無し | 無し |"]


def test_the_reader_picks_the_generated_table_after_a_note_table():
    first, views = _two_files()
    note = "| 単位 | 備考 |\n| --- | --- |\n| 形 | 残る |"
    units = "| 単位 | 含むタスク | 満たす受け入れ条件 |\n| --- | --- | --- |\n| 旧 | Task 1 | 無し |"
    plan = _plan(1, 2, 3) + f"\n## レビューの単位\n\n{note}\n\n{units}\n"

    once = uf.render_tables(plan, views, first.path)
    read = review_units_decide.lineup([review_units_decide.PlanDoc("p", frozenset({7}), once)], {7: 2}, [7])

    assert [u.name for u in read] == ["形", "読む|側"]


def test_render_tables_keeps_an_omitted_issue_out_of_the_parallel_column():
    unit_file = ud.parse(_doc(), "issues/issue-7-plan.units.json")
    views = ud.judge([unit_file, ud.single(8)], LIMITS, {7: 0, 8: 0}).views

    assert _rows(uf.render_tables(_plan(1), views, unit_file.path), "並列と直列") == ["| 1 | A | A: 無し | 無し |"]
    assert _rows(uf.render_tables(_plan(1), views), "並列と直列") == ["| 1 | A、issue-8 | A: 無し<br>issue-8: 無し | 無し |"]


def test_read_refuses_a_path_outside_the_repository(tmp_path):
    outside = _units(tmp_path, "7")
    tree = uf.Tree(tmp_path / "repo")

    for path in (str(tmp_path / outside), f"../{outside}"):
        with pytest.raises(uf.UnitsFileError) as stopped:
            uf.load(tree, path)
        assert "相対パス" in str(stopped.value)


def test_load_does_not_copy_the_content_into_a_schema_error(tmp_path):
    path = _put(tmp_path, "issue-7-plan.units.json", {"token-key": "s3cret", **_doc(tasks=[_task("s3cret", lines="s3cret")])})

    with pytest.raises(uf.UnitsFileError) as stopped:
        uf.load(uf.Tree(tmp_path), path)

    assert "s3cret" not in str(stopped.value) and "token-key" not in str(stopped.value)


def test_differs_ignores_only_the_actual_lines_and_the_path():
    base = ud.parse(_doc(tasks=[_task("T1"), _task("T2")], units=[_unit("A", "T1"), _unit("B", "T2")]), "a")
    written = base._replace(path="b", units=(base.units[0]._replace(actual_lines=5), base.units[1]))

    assert uf.differs(base, written) is False
    assert uf.differs(base, base._replace(units=base.units[::-1])) is True
    assert uf.differs(base, base._replace(tasks=(base.tasks[0]._replace(lines=11), base.tasks[1]))) is True
