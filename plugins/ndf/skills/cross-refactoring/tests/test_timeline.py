"""テストと CI の待ちの上限は予算から導かない（#2457 決定 11・I3・受け入れ条件 2・5）。

上限は共通ライブラリの予算なしの式（`3·max(w, m)`・`3·c`・所要が無ければ既定の秒）で決まる。
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture(scope="module")
def timeline(refactor):
    return importlib.import_module("refactor_lib.timeline")


def _state(budget_minutes=60, **baseline):
    from crossref_helpers import strategy_state

    return {
        "budget_minutes": budget_minutes,
        "started_at": "2026-11-10T10:00:00+09:00",
        "strategy": strategy_state("local-full"),
        "baseline_test": {"mode": "whole", **baseline},
    }


def test_the_limits_do_not_follow_the_budget(timeline):
    """I3: 予算を変えても上限の表は同じ（予算から導いてよいのは安全網の上限だけ）。"""
    short = timeline.of_state(_state(1, seconds=100.0, whole_seconds=120.0, ci_seconds=300.0))
    long = timeline.of_state(_state(600, seconds=100.0, whole_seconds=120.0, ci_seconds=300.0))
    assert short == long
    assert short["test_timeout"] == 300 and short["whole_timeout"] == 360 and short["ci_wait_timeout"] == 900


def test_unknown_durations_use_the_fixed_limits(timeline):
    """所要が無ければ共通ライブラリの既定の秒（`UNKNOWN_DURATION_LIMITS`）。"""
    got = timeline.of_state(_state())
    assert (got["test_timeout"], got["whole_timeout"], got["ci_wait_timeout"]) == (900, 1800, 3600)
    assert got["basis"]["unknown_duration"] is True


def test_the_table_has_no_deadlines(timeline):
    """受け入れ条件 5: 締め切りの時刻（`*_end_at`）を持たない。"""
    got = timeline.of_state(_state(seconds=100.0))
    assert not [k for k in got if k.endswith("_end_at")]
    assert "gate_seconds" not in got and "final_fix_seconds" not in got


def test_the_plan_comment_shows_the_inputs(timeline):
    plan = importlib.import_module("refactor_lib.plan")
    limits = timeline.of_state(_state(seconds=100.0, whole_seconds=120.0))
    line = next(text for text in plan.limits_section(limits) if text.startswith("入力:"))
    assert "w 120.0" in line and "x 100.0" in line
