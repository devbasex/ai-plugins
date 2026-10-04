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
    return [
        {
            "start_deadline": (START + a * M).isoformat(),
            "test_start_deadline": (START + t * M).isoformat() if t is not None else None,
            "estimate": {"test": 2.7 if t is not None else 0.0, "implement": 1.3, "verify": 0.2},
        }
        for a, t in start_offsets
    ]


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
    # 直しの試行の打ち切り = 開始 + B − 全体のテストの予備時間 2 つ − 最終ゲートの修正の予備時間（決定 26）
    assert got["fix_end_at"] == (START + (b - 2 - 5.5) * M).isoformat(timespec="seconds")
    assert got["final_end_at"] == (START + b * M).isoformat(timespec="seconds")
    # 最終ゲートの修正の 1 回目に必ず渡す長さ（秒）= 予備時間の final_fix
    assert got["final_fix_seconds"] == 330


def test_the_test_limit_grows_with_the_measured_test(timeline):
    """テスト 1 回の上限は共通層 `test_strategy.limits` の式（`max(3·x, 0.01·B)`。#1334 決定 8）。"""
    import sys

    ts = sys.modules["test_strategy"]
    local = ts.Strategy("local-full", "args")
    assert ts.limits(local, 30, measured_seconds=59.0)["test_timeout"] == 177  # 3 × 59
    assert ts.limits(local, 30, measured_seconds=1.0)["test_timeout"] == 18  # 0.01 × 1800 が下限
    assert ts.limits(local, 30)["test_timeout"] == 180  # 測れていなければ着手前の上限
    # 係数は 1 か所（共通層）にあり、timeline はそれを再公開する
    assert timeline.TEST_FACTOR == ts.TEST_FACTOR and timeline.INIT_TEST_SHARE == ts.INIT_TEST_SHARE


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


# ---------- 手順の枠は着手前のテストの終わりから数える（#1385） ----------

S = dt.timedelta(seconds=1)


def _ts():
    import sys

    return sys.modules["test_strategy"]


def _at(value):
    return dt.datetime.fromisoformat(value)


def _local_full(x):
    return _ts().limits(_ts().Strategy("local-full", "args"), 30, measured_seconds=x)


def test_the_windows_start_after_the_init_test(timeline):
    """AC1・I1・I2: 着手前のテスト 357 秒の後でも、提案は 0.20·B・計画は 0.10·B の枠を持ち、全体の終わりは動かない。"""
    got = timeline.compute(START, 30, _local_full(357.0), offset_seconds=357.0)
    assert _at(got["propose_end_at"]) == START + 717 * S
    assert _at(got["plan_end_at"]) == START + 897 * S
    assert _at(got["final_end_at"]) == START + 1800 * S
    # AC2: テストの直後の提案の監視の上限は 0.20·B + 余裕
    assert timeline.phase_timeout(_at(got["propose_end_at"]), START + 357 * S, got["margin_seconds"]) == 450
    # AC3: テストの直後の指標の測定は上限の 90 秒を使える
    assert timeline.measure_deadline(START + 357 * S, got) == 90


@pytest.mark.parametrize("x", [None, 0.0])
def test_a_short_init_test_keeps_the_windows(timeline, x):
    """AC4: テストが無い・ごく短いなら今と同じ。"""
    got = timeline.compute(START, 30, _local_full(x), offset_seconds=x)
    assert _at(got["propose_end_at"]) == START + 360 * S
    assert _at(got["plan_end_at"]) == START + 540 * S


def test_the_deadlines_after_the_plan_do_not_move(timeline):
    """AC5・I3: 計画の後の直しと取り消しの締め切りは started_at から数え、x に依らない。"""
    items = _items([(15, 12)])
    short = timeline.compute(START, 30, _local_full(0.0), items, RESERVE, offset_seconds=0.0)
    long = timeline.compute(START, 30, _local_full(357.0), items, RESERVE, offset_seconds=357.0)
    for key in ("fix_end_at", "stop_revert_end_at", "final_end_at", "add_tests_end_at", "implement_end_at"):
        assert long[key] == short[key], key


def _state(seconds=357.0, **over):
    state = {
        "started_at": START.isoformat(),
        "budget_minutes": 30,
        "strategy": {"name": "local-full", "source": "args", "suites": []},
        "baseline_test": {"mode": "whole", "seconds": seconds},
    }
    state.update(over)
    return state


def test_of_state_offsets_the_windows_by_the_measured_test(timeline):
    got = timeline.of_state(_state())
    assert _at(got["propose_end_at"]) == START + 717 * S
    # 旧い状態（seconds が無い）は今と同じ
    old = timeline.of_state(_state(seconds=None))
    assert _at(old["propose_end_at"]) == START + 360 * S


def test_a_restart_after_the_stop_starts_the_windows_at_the_restart(timeline):
    """I2: 止めた後の打ち直し（resumed_at）があれば o = max(x, resumed_at − started_at)。"""
    state = _state(seconds=1300.0, budget_minutes=39, resumed_at=(START + 1360 * S).isoformat())
    assert timeline.window_offset(state) == 1360
    assert _at(timeline.of_state(state)["propose_end_at"]) == START + 1828 * S
    assert timeline.window_offset(_state(seconds=1300.0, resumed_at=(START + 10 * S).isoformat())) == 1300


@pytest.mark.parametrize(
    "seconds, budget, resumed, needed",
    [
        (1300.0, 30, None, 39),  # ceil((1300 + 300) / 42)
        (1300.0, 39, 1700, 48),  # 打ち直しが遅れて o が伸びた
        (1300.0, 39, 1360, None),  # 下限の予算で 1 分後に打ち直せば収まる
        (1300.0, 39, 1600, None),  # 300 秒以内なら収まる
        (1260.0, 30, None, None),  # 0.70·B ちょうどは収まる
    ],
)
def test_window_problem_stops_when_the_windows_do_not_fit(timeline, seconds, budget, resumed, needed):
    """AC6・I4: 計画の枠の終わりが想定最大時間を越えるなら、要る想定最大時間の下限つきの文を返す。"""
    over = {"resumed_at": (START + resumed * S).isoformat()} if resumed else {}
    problem = timeline.window_problem(timeline.of_state(_state(seconds=seconds, budget_minutes=budget, **over)))
    if needed is None:
        assert problem is None
    else:
        assert f"--budget-minutes を {needed} 以上" in problem


def test_the_required_budget_adds_the_restart_grace(timeline):
    assert timeline.required_budget_minutes(1300) == 39
    assert timeline.required_budget_minutes(1700) == 48
    assert timeline.RESUME_GRACE_SECONDS == 300


def test_the_rebuilt_limits_carry_the_scope_test(timeline):
    """I6・AC10: 状態に残った範囲テストの所要から同じ上限を組み直し、basis に写す。予算を置き換えれば組み直した値になる。"""
    state = _state(
        seconds=320.0,
        strategy={"name": "local-scoped-ci-whole", "source": "args", "suites": []},
        baseline_test={"mode": "scope", "seconds": 320.0, "scope_seconds": 320.0, "scope_source": "history"},
    )
    got = timeline.of_state(state)
    assert got["init_test_timeout"] == 960
    assert (got["basis"]["scope_seconds"], got["basis"]["scope_source"]) == (320.0, "history")
    unknown = _state(strategy=state["strategy"], baseline_test={"mode": "scope", "seconds": 100.0}, budget_minutes=60)
    assert timeline.of_state(unknown)["init_test_timeout"] == 360


def test_the_plan_comment_shows_the_scope_test(timeline):
    """F5・AC10: 計画のコメントの入力の行に範囲テストの所要と出所が出る。"""
    plan = importlib.import_module("refactor_lib.plan")
    limits = timeline.of_state(
        _state(
            strategy={"name": "local-scoped-ci-whole", "source": "args", "suites": []},
            baseline_test={"mode": "scope", "seconds": 320.0, "scope_seconds": 320.0, "scope_source": "history"},
        )
    )
    line = next(text for text in plan.limits_section(limits) if text.startswith("入力:"))
    assert "s 320.0（history）" in line
