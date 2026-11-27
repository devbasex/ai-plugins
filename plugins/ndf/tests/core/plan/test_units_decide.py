"""単位の判定（`ndf.core.plan.units_decide`）のテスト（issues/issue-3702-design-detail.md の「単位の判定」）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import contracts
from ndf.core.plan import units_decide as ud

LIMITS = ud.Limits(target=400, max=1000)
FIXTURE_3672 = Path(__file__).parent / "fixtures" / "units" / "issue-3672.units.json"


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
    # タスクが 1 つの単位は区切れないので、上限を超えるときだけ切り直すタスクを返す
    expected = [ud.Proposal("issues/issue-7-plan.units.json", "A", (), ("T1",), (), ())] if violations else []
    assert list(j.proposals) == expected


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


def test_u1_duplicate_task_in_a_unit_counts_once():
    j = _judge(_file([_task("T1", lines=600)], [_unit("A", "T1", "T1")]))
    assert _rules(j.violations) == ["U1"]
    assert "T1 が重なる" in j.violations[0].reason
    v = _views(j)["A"]
    assert (v.lines, v.tasks) == (600, ("T1",))


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


def _split_names(proposal):
    return [(u.name, u.tasks, tuple((e.unit, e.reason) for e in u.after)) for u in proposal.units]


def test_split_cuts_in_dependency_order_before_the_target():
    tasks = [
        _task("T1", lines=300, after=("T10",)),
        _task("T2", lines=100),
        _task("T10", lines=300),
        _task("T3", lines=450),
        _task("T4", lines=250, after=("T3",)),
    ]
    j = _judge(_file(tasks, [_unit("A", "T1", "T2", "T10", "T3", "T4")]))
    (p,) = j.proposals
    # 依存の深さ、同じ深さはタスクの番号の順（T2・T3・T10・T1・T4）。目安を超える T3 はそれだけで 1 区切り
    reason = "分け方の案: 依存の順"
    assert _split_names(p) == [
        ("A 1", ("T2",), ()),
        ("A 2", ("T3",), (("A 1", reason),)),
        ("A 3", ("T10",), (("A 2", reason),)),
        ("A 4", ("T1",), (("A 3", reason),)),
        ("A 5", ("T4",), (("A 4", reason),)),
    ]
    assert (p.unit, p.resplit, p.remaining) == ("A", (), ())


def test_split_moves_edges_to_the_first_and_the_last_piece():
    base = _file([_task("T1")], [_unit("X", "T1")], issues=(10,), path="a.json")
    big = _file(
        [_task("T1", lines=400), _task("T2", lines=400)],
        [_unit("A", "T1", "T2", after=[{"unit": "X", "reason": "X の型", "issue": 10}])],
        issues=(20,),
        path="b.json",
    )
    later = _file([_task("T1")], [_unit("Y", "T1", after=[{"unit": "A", "reason": "r", "issue": 20}])], issues=(30,), path="c.json")
    p = ud.split((base, big, later), ("b.json", "A"), LIMITS)
    assert (p.file, p.unit, p.repoint) == ("b.json", "A", (("c.json", "Y", 20),))
    assert p.units[0].after == (ud.Edge("X", "X の型", 10),)
    assert p.units[1].after == (ud.Edge("A 1", "分け方の案: 依存の順"),)
    # 元の単位を指していた依存は最後の区切りへ付け替わるので、当てた後に違反は残らない
    assert p.remaining == ()


def _apply_proposal(files, p):
    """返り値の欄だけを使って案を適用する。"""

    def edges(f, u):
        return tuple(
            e._replace(unit=p.units[-1].name) if e.unit == p.unit and (f.path, u.name, e.issue) in p.repoint else e for e in u.after
        )

    applied = []
    for f in files:
        units = []
        for u in f.units:
            units += p.units if (f.path, u.name) == (p.file, p.unit) else [u._replace(after=edges(f, u))]
        applied.append(f._replace(units=tuple(units)))
    return applied


def test_split_proposal_is_enough_to_apply_across_files():
    big = _file(
        [_task("T1", lines=400), _task("T2", lines=400), _task("T3")],
        [_unit("A", "T1", "T2"), _unit("B", "T3", after=["A"])],
        issues=(20,),
        path="b.json",
    )
    later = _file([_task("T1")], [_unit("Y", "T1", after=[{"unit": "A", "reason": "r", "issue": 20}])], issues=(30,), path="c.json")
    (p,) = _judge(big, later).proposals
    assert p.repoint == (("b.json", "B", None), ("c.json", "Y", 20))
    applied = _judge(*_apply_proposal((big, later), p))
    assert (applied.violations, applied.warnings) == ((), ())


def test_split_repoints_only_the_edge_to_the_split_unit_among_same_names():
    other = _file([_task("T1")], [_unit("A", "T1")], issues=(10,), path="a.json")
    big = _file([_task("T1", lines=400), _task("T2", lines=400)], [_unit("A", "T1", "T2")], issues=(20,), path="b.json")
    edges = [{"unit": "A", "reason": "r", "issue": 10}, {"unit": "A", "reason": "r", "issue": 20}]
    later = _file([_task("T1")], [_unit("Y", "T1", after=edges)], issues=(30,), path="c.json")
    (p,) = _judge(other, big, later).proposals
    assert p.repoint == (("c.json", "Y", 20),)
    applied = _judge(*_apply_proposal((other, big, later), p))
    assert applied.violations == ()
    assert _views(applied)["Y"].after == (("a.json", "A", "r"), ("b.json", "A 2", "r"))


def test_split_keeps_the_criteria_violation_left_after_applying():
    tasks = [_task("T1", lines=400, criteria=("7:3",)), _task("T2", lines=400)]
    j = _judge(_file(tasks, [_unit("A", "T1", "T2")]), criteria={7: 2})
    (p,) = j.proposals
    assert p.remaining == j.violations
    assert _rules(p.remaining) == ["U2"]


def test_split_returns_tasks_over_the_max_to_be_cut_again():
    f = _file([_task("T1", lines=1001), _task("T2", lines=10), _task("T3", lines=2000)], [_unit("A", "T1", "T2", "T3")])
    assert ud.split((f,), (f.path, "A"), LIMITS) == ud.Proposal(f.path, "A", (), ("T1", "T3"), (), ())


def test_split_of_a_whole_unit_without_lines_has_no_units():
    f = ud.single(7)
    assert ud.split((f,), (f.path, "issue-7"), LIMITS) == ud.Proposal(f.path, "issue-7", (), (), (), ())


def test_split_with_one_piece_has_no_units():
    f = _file([_task("T1", lines=100), _task("T2", lines=100)], [_unit("A", "T1", "T2")])
    assert ud.split((f,), (f.path, "A"), LIMITS) == ud.Proposal(f.path, "A", (), (), (), ())


def test_split_reports_the_overlap_left_after_applying():
    tasks = [_task("T1", lines=400, files=("x.py",)), _task("T2", lines=400), _task("T3", files=("x.py",))]
    (p,) = _judge(_file(tasks, [_unit("A", "T1", "T2"), _unit("B", "T3")])).proposals
    assert len(p.units) == 2
    assert [(f.rule, f.unit) for f in p.remaining] == [("U4", "A 1")]


def test_split_survives_a_task_cycle():
    tasks = [_task("T1", lines=400, after=("T2",)), _task("T2", lines=400, after=("T1",))]
    (p,) = _judge(_file(tasks, [_unit("A", "T1", "T2")])).proposals
    assert [u.tasks for u in p.units] == [("T1",), ("T2",)]
    assert _rules(p.remaining) == ["U1", "U3"]  # 後の区切りを先に要る T1 は U3


def test_proposals_only_for_oversize_units():
    tasks = [_task("T1", lines=200), _task("T2", lines=201), _task("T3", lines=401), _task("T4", lines=1)]
    j = _judge(_file(tasks, [_unit("A", "T1", "T2"), _unit("B", "T3"), _unit("C", "T4")]))
    # 目安を超える単位はタスクが 2 つ以上のときだけ案を作る
    assert [p.unit for p in j.proposals] == ["A"]


def test_issue_3672_design_splits_into_units_in_dependency_order():
    doc = json.loads(FIXTURE_3672.read_text(encoding="utf-8"))
    assert contracts.schema_errors(doc, "workflow/review-units.schema.json") == []
    f = ud.parse(doc, FIXTURE_3672.name)
    j = _judge(f)
    assert _rules(j.violations) == ["U6"]
    (p,) = j.proposals
    assert (p.resplit, p.remaining) == ((), ())
    assert len(p.units) > 1

    applied = _judge(f._replace(units=p.units))
    assert (applied.violations, applied.warnings, applied.proposals) == ((), (), ())
    assert [v.order for v in applied.views] == list(range(1, len(p.units) + 1))

    # 群の名前はタスクの題の先頭にある。実行順に並べた群が、依存の向きの順になる
    group = {t.id: t.title.partition(":")[0] for t in f.tasks}
    seen = [group[tid] for v in sorted(applied.views, key=lambda v: v.order) for tid in v.tasks]
    assert list(dict.fromkeys(seen)) == [
        "コントラクト",
        "設定を読むプログラムと、設定を解析して書くコマンド",
        "工程のプログラムの読み方の切り替え",
        "旧いものの削除とテストの移し替え",
    ]
    assert seen == sorted(seen, key=list(dict.fromkeys(seen)).index)
    assert sorted(tid for u in p.units for tid in u.tasks) == sorted(group)
