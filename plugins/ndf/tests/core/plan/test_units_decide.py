"""単位の判定（`ndf.core.plan.units_decide`）のテスト（issues/issue-3702-design-detail.md の「単位の判定」）。"""

from __future__ import annotations

import pytest

from ndf.core.plan import units_decide as ud

LIMITS = ud.Limits(target=400, max=1000)


def _task(tid: str, *, lines: int = 10, files: tuple[str, ...] = (), after: tuple[str, ...] = (), criteria=(), **extra):
    return {
        "id": tid,
        "title": f"作業 {tid}",
        "files": list(files),
        "lines": lines,
        "criteria": list(criteria),
        "after": list(after),
        **extra,
    }


def _unit(name: str, *tasks: str, after=()):
    edges = [e if isinstance(e, dict) else {"unit": e, "reason": f"{e} の後"} for e in after]
    return {"name": name, "tasks": list(tasks), "after": edges}


def _file(tasks, units, *, issues=(7,), path="issues/issue-7-plan.units.json"):
    return ud.parse({"version": 1, "issues": list(issues), "tasks": tasks, "units": units}, path)


def _judge(*files, criteria=None):
    keys = {i for f in files for i in f.issues}
    return ud.judge(files, LIMITS, criteria if criteria is not None else {i: 9 for i in keys})


def _views(j):
    return {v.name: v for v in j.views}


def _rules(findings):
    return sorted(f.rule for f in findings)


def test_parse_fills_the_sole_issue_and_keeps_edge_issue():
    f = _file([_task("T1")], [_unit("A", "T1", after=[{"unit": "X", "reason": "r", "issue": 9}])])
    assert f.tasks[0].issue == 7
    assert f.units[0].after == (ud.Edge("X", "r", 9),)
    assert f.whole is False


def test_order_and_merge_follow_the_dependency_depth():
    tasks = [_task(t) for t in ("T1", "T2", "T3", "T4")]
    units = [_unit("A", "T1"), _unit("B", "T2", after=["A"]), _unit("C", "T3", after=["B"]), _unit("D", "T4")]
    j = _judge(_file(tasks, units))
    v = _views(j)
    assert (j.violations, j.warnings) == ((), ())
    assert {n: v[n].order for n in v} == {"A": 1, "B": 2, "C": 3, "D": 1}
    assert {n: v[n].merge for n in v} == {"A": 1, "D": 2, "B": 3, "C": 4}
    assert v["B"].after == ((_file(tasks, units).path, "A", "A の後"),)


def test_merge_breaks_ties_by_the_smallest_issue_across_files():
    late = _file([_task("T1")], [_unit("X", "T1")], issues=(20,), path="b.json")
    early = _file([_task("T1")], [_unit("Y", "T1")], issues=(10,), path="a.json")
    v = _views(_judge(late, early))
    assert (v["Y"].merge, v["X"].merge) == (1, 2)


def test_edge_to_another_file_resolves_by_issue_and_same_task_ids_pass():
    first = _file([_task("T1", files=("a.py",))], [_unit("A", "T1")], issues=(10,), path="a.json")
    edge = {"unit": "A", "reason": "A の関数を使う", "issue": 10}
    second = _file([_task("T1", files=("a.py",))], [_unit("B", "T1", after=[edge])], issues=(20,), path="b.json")
    j = _judge(first, second)
    v = _views(j)
    assert j.violations == ()
    assert v["B"].order == 2
    assert v["B"].after == (("a.json", "A", "A の関数を使う"),)


def test_view_carries_derived_values():
    tasks = [
        _task("T1", lines=30, files=("b.py", "a.py"), criteria=("7:1",)),
        _task("T2", lines=12, files=("a.py",), criteria=("7:2",), after=("T1",)),
    ]
    v = _views(_judge(_file(tasks, [_unit("A", "T1", "T2")])))["A"]
    assert (v.lines, v.touches, v.criteria, v.tasks, v.issues) == (42, ("a.py", "b.py"), ("7:1", "7:2"), ("T1", "T2"), (7,))


def test_single_makes_one_whole_unit_without_size_findings():
    f = ud.single(3672)
    assert (f.whole, f.issues, [u.name for u in f.units]) == (True, (3672,), ["issue-3672"])
    j = _judge(f)
    v = _views(j)["issue-3672"]
    assert (v.lines, v.touches, v.order, v.merge) == (None, (), 1, 1)
    assert (j.violations, j.warnings) == ((), ())


@pytest.mark.parametrize(
    ("lines", "violations", "warnings"),
    [(400, [], []), (401, [], ["W1"]), (1000, [], ["W1"]), (1001, ["U6"], []), (16757, ["U6"], []), (24008, ["U6"], [])],
)
def test_size_check_by_lines(lines, violations, warnings):
    j = _judge(_file([_task("T1", lines=lines)], [_unit("A", "T1")]))
    assert (_rules(j.violations), _rules(j.warnings)) == (violations, warnings)


def test_size_sums_the_tasks_of_a_unit():
    j = _judge(_file([_task("T1", lines=600), _task("T2", lines=401)], [_unit("A", "T1", "T2")]))
    assert _rules(j.violations) == ["U6"]


@pytest.mark.parametrize(
    ("tasks", "units"),
    [
        ([_task("T1"), _task("T1")], [_unit("A", "T1")]),
        ([_task("T1"), _task("T2")], [_unit("A", "T1"), _unit("A", "T2")]),
        ([_task("T1", after=("T9",))], [_unit("A", "T1")]),
        ([_task("T1")], [_unit("A", "T1", "T9")]),
        ([_task("T1")], [_unit("A", "T1", after=["Z"])]),
        ([_task("T1", after=("T2",)), _task("T2", after=("T1",))], [_unit("A", "T1", "T2")]),
        ([_task("T1"), _task("T2")], [_unit("A", "T1", after=["B"]), _unit("B", "T2", after=["A"])]),
    ],
    ids=["dup-task", "dup-unit", "task-after-missing", "unit-task-missing", "unit-after-missing", "task-cycle", "unit-cycle"],
)
def test_u1(tasks, units):
    assert "U1" in _rules(_judge(_file(tasks, units)).violations)


def test_u1_issue_in_two_files():
    a = _file([_task("T1")], [_unit("A", "T1")], path="a.json")
    b = _file([_task("T1")], [_unit("B", "T1")], path="b.json")
    assert "U1" in _rules(_judge(a, b).violations)


def test_u1_edge_to_unknown_issue():
    f = _file([_task("T1")], [_unit("A", "T1", after=[{"unit": "X", "reason": "r", "issue": 99}])])
    assert "U1" in _rules(_judge(f).violations)


def test_cycle_keeps_judging_the_rest():
    tasks = [_task("T1"), _task("T2"), _task("T3", lines=1001)]
    units = [_unit("A", "T1", after=["B"]), _unit("B", "T2", after=["A"]), _unit("C", "T3")]
    j = _judge(_file(tasks, units))
    assert _rules(j.violations) == ["U1", "U6"]
    assert _views(j)["C"].order == 1


@pytest.mark.parametrize(
    ("tasks", "units", "criteria"),
    [
        ([_task("T1"), _task("T2")], [_unit("A", "T1")], None),
        ([_task("T1")], [_unit("A", "T1"), _unit("B", "T1")], None),
        ([_task("T1", criteria=("8:1",))], [_unit("A", "T1")], None),
        ([_task("T1", criteria=("7:0",))], [_unit("A", "T1")], None),
        ([_task("T1", criteria=("7:3",))], [_unit("A", "T1")], {7: 2}),
        ([], [], None),
        ([_task("T1")], [_unit("A", "T1"), _unit("B")], None),
    ],
    ids=["no-unit", "two-units", "outside-issue", "zero", "beyond-count", "empty-plan", "unit-without-tasks"],
)
def test_u2(tasks, units, criteria):
    assert "U2" in _rules(_judge(_file(tasks, units), criteria=criteria).violations)


def test_u2_task_issue_outside_the_file():
    f = _file([_task("T1", issue=8), _task("T2", issue=7)], [_unit("A", "T1", "T2")], issues=(7, 9))
    assert "U2" in _rules(_judge(f).violations)


def test_criteria_of_a_merged_issue_pass_when_in_the_criteria_keys():
    f = _file([_task("T1", criteria=("8:1",))], [_unit("A", "T1")])
    assert _judge(f, criteria={7: 1, 8: 1}).violations == ()


def test_u3_task_dependency_not_covered_by_units():
    tasks = [_task("T1"), _task("T2", after=("T1",))]
    assert _rules(_judge(_file(tasks, [_unit("A", "T1"), _unit("B", "T2")])).violations) == ["U3"]


def test_u3_passes_with_indirect_unit_dependency_or_same_unit():
    tasks = [_task("T1"), _task("T2"), _task("T3", after=("T1",))]
    chained = [_unit("A", "T1"), _unit("B", "T2", after=["A"]), _unit("C", "T3", after=["B"])]
    assert _judge(_file(tasks, chained)).violations == ()
    assert _judge(_file(tasks, [_unit("A", "T1", "T2", "T3")])).violations == ()


def test_u4_independent_units_share_a_file_even_across_files():
    a = _file([_task("T1", files=("x.py",))], [_unit("A", "T1")], issues=(10,), path="a.json")
    b = _file([_task("T1", files=("x.py",))], [_unit("B", "T1")], issues=(20,), path="b.json")
    assert _rules(_judge(a, b).violations) == ["U4"]


def test_u4_ignores_ordered_units_with_different_orders():
    tasks = [_task("T1", files=("x.py",)), _task("T2"), _task("T3", files=("x.py",))]
    units = [_unit("A", "T1"), _unit("B", "T2", after=["A"]), _unit("C", "T3", after=["B"])]
    assert _judge(_file(tasks, units)).violations == ()
    units[2] = _unit("C", "T3")
    assert _rules(_judge(_file(tasks, units)).violations) == ["U4"]


def test_u4_reports_a_pair_once_with_all_shared_files():
    tasks = [_task("T1", files=("x.py", "y.py")), _task("T2", files=("y.py", "x.py", "z.py"))]
    j = _judge(_file(tasks, [_unit("A", "T1"), _unit("B", "T2")]))
    assert [(f.rule, f.unit) for f in j.violations] == [("U4", "A")]
    assert "x.py・y.py" in j.violations[0].reason


def test_units_after_a_cycle_get_order_zero_and_keep_their_closure():
    tasks = [_task(t, files=("x.py",) if t in ("T3", "T4") else ()) for t in ("T1", "T2", "T3", "T4")]
    units = [_unit("A", "T1", after=["B"]), _unit("B", "T2", after=["A"]), _unit("C", "T3", after=["A"]), _unit("D", "T4", after=["C"])]
    j = _judge(_file(tasks, units))
    assert {n: v.order for n, v in _views(j).items()} == {"A": 0, "B": 0, "C": 0, "D": 0}
    assert _rules(j.violations) == ["U1"]


def test_u5_blank_reason():
    units = [_unit("A", "T1"), _unit("B", "T2", after=[{"unit": "A", "reason": "  "}])]
    assert _rules(_judge(_file([_task("T1"), _task("T2")], units)).violations) == ["U5"]
