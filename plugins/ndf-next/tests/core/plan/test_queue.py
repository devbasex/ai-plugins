"""キューの判断（queue_decide）: 前提・再開の前置き・触るファイルの重なり・同時に流す組（RF5a・N2b）。"""

from __future__ import annotations

import pytest

from ndf.core.plan.queue_decide import Queued, blocked_reason, contains, overlaps, resume_prelude, waves


def test_blocked_reason_is_none_when_every_dep_is_ok():
    assert blocked_reason("b", ["a"], ["a", "b"], {"a": "ok"}) is None


@pytest.mark.parametrize(
    ("queued", "results", "expected"),
    [
        (["b", "a"], {}, "前提のプラン a の結果が無い（後ろにある）"),
        (["b"], {}, "前提のプラン a が見つからない"),
        (["a", "b"], {"a": "gate"}, "前提のプランが完了でない: a（gate）"),
    ],
)
def test_blocked_reason_names_the_dep(queued, results, expected):
    assert blocked_reason("b", ["a"], queued, results) == expected


def test_resume_prelude_keeps_the_always_steps_before_the_start():
    order = ["deep-due", "base-sync", "impl", "retro"]
    assert resume_prelude(order, "retro") == ("deep-due", "base-sync")
    assert resume_prelude(order, "base-sync") == ("deep-due",)
    assert resume_prelude(order, "deep-due") == ()


def test_resume_prelude_refuses_an_unknown_start():
    with pytest.raises(ValueError):
        resume_prelude(["a"], "b")


def test_a_plan_without_touches_overlaps_every_plan():
    """触るファイルの宣言の無いプランは、同時に流す別のプランと重なるものとして直列にする（#2165）。"""
    assert overlaps(None, ["x.py"])
    assert overlaps(["x.py"], None)
    assert waves([Queued("a", None), Queued("b", ("y.py",))]) == [["a"], ["b"]]


@pytest.mark.parametrize(
    ("a", "b", "hit"),
    [
        ("src/a.py", "./src/a.py", True),
        ("src/", "src/a.py", True),
        ("src/a.py", "src/", True),
        ("src/a.py", "src/ab.py", False),
        ("tests/plan/", "tests/planner/x.py", False),
    ],
)
def test_contains_matches_files_under_a_directory(a, b, hit):
    assert contains(a, b) is hit


def test_waves_put_disjoint_plans_together_and_respect_needs():
    items = [
        Queued("a", ("src/a.py",)),
        Queued("b", ("src/b.py",)),
        Queued("c", ("src/a.py",)),
        Queued("d", ("docs/d.md",), needs=("b",)),
    ]
    assert waves(items) == [["a", "b"], ["c", "d"]]
