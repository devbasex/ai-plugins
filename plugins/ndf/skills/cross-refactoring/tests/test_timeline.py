"""時間の上限を想定最大時間から逆算する（#933 決定 23・24、実装計画 I15 I16）。

予算を変えると、各上限が式どおりに変わることを固定する。式は
`docs/02-plan-and-implement.md` の「締め切り」の節にある。
"""
from __future__ import annotations

import datetime as dt
import importlib

import pytest


@pytest.fixture(scope="module")
def timeline(refactor):
    return importlib.import_module("refactor_lib.timeline")


START = dt.datetime(2026, 9, 24, 10, 0, 0).astimezone()
M = dt.timedelta(minutes=1)
RESERVE = {"danger_whole_test": 1.0, "final_whole_test": 1.0, "fix": 5.5, "final_fix": 5.5}


def _items(start_offsets):
    """着手の締め切り（開始からの分）と見積り（分）を持つ項目。"""
    return [{"start_deadline": (START + a * M).isoformat(),
             "test_start_deadline": (START + t * M).isoformat() if t is not None else None,
             "estimate": {"test": 2.7 if t is not None else 0.0, "implement": 1.3, "verify": 0.2}}
            for a, t in start_offsets]


@pytest.mark.parametrize("budget_minutes", [10, 60])
def test_every_limit_follows_the_budget(timeline, budget_minutes):
    b = budget_minutes
    items = _items([(b * 0.5, b * 0.4), (b * 0.6, None)])
    got = timeline.compute(START, b, 20.0, items, RESERVE)

    assert got["margin_seconds"] == round(b * 60 * 0.05)
    assert got["init_test_timeout"] == round(b * 60 * 0.10)
    assert got["test_timeout"] == max(60, round(b * 60 * 0.01))  # 3 × 20 秒と 0.01·B の大きい方
    assert got["propose_end_at"] == (START + b * 0.2 * M).isoformat(timespec="seconds")
    assert got["plan_end_at"] == (START + b * 0.3 * M).isoformat(timespec="seconds")
    # 手順の終わり = 最後の項目の完了の締め切り（着手の締め切り + 見積り）
    assert got["add_tests_end_at"] == (START + (b * 0.4 + 2.7) * M).isoformat(timespec="seconds")
    assert got["implement_end_at"] == (START + (b * 0.6 + 1.3) * M).isoformat(timespec="seconds")
    # 直しの試行の打ち切り = 開始 + B − 全体のテストの控え 2 つ − 最終ゲートの修正の控え（決定 26）
    assert got["fix_end_at"] == (START + (b - 2 - 5.5) * M).isoformat(timespec="seconds")
    assert got["final_end_at"] == (START + b * M).isoformat(timespec="seconds")
    # 最終ゲートの修正の 1 回目に必ず渡す長さ（秒）= 控えの final_fix
    assert got["final_fix_seconds"] == 330


def test_the_test_limit_grows_with_the_measured_whole_test(timeline):
    assert timeline.test_timeout(30, 59.0) == 177          # 3 × 59
    assert timeline.test_timeout(30, 1.0) == 18            # 0.01 × 1800 が下限
    assert timeline.test_timeout(30, None) == 180          # 測れていなければ着手前の上限


def test_values_after_the_plan_are_empty_before_the_plan(timeline):
    got = timeline.compute(START, 30, None)
    assert got["add_tests_end_at"] is None and got["implement_end_at"] is None
    assert got["fix_end_at"] is None and got["final_fix_seconds"] is None
    assert got["propose_end_at"] and got["plan_end_at"] and got["final_end_at"]


def test_the_phase_timeout_is_the_time_left_plus_the_margin(timeline):
    end = START + 10 * M
    assert timeline.phase_timeout(end, START, 90) == 600 + 90
    # 終わりを過ぎていれば余裕だけ
    assert timeline.phase_timeout(end, end + 5 * M, 90) == 90
