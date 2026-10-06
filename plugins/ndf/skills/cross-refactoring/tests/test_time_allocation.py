"""時間の配分（#1743）と再開で止まっていた時間（#1491）を、純粋な関数と状態ファイルで確かめる。

入力は実測（PR 1801・PR 1745 の実行）をそのまま与える。式は `docs/02-plan-and-implement.md` の「締め切り」にある。
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import os
import sys
import threading
import time

import pytest

START = dt.datetime(2026, 10, 6, 14, 26, 13).astimezone()
M = dt.timedelta(minutes=1)
TABLE = {"source": "history", "test": 2.7, "verify": 0.2, "fix": 1.3, "structure": 1.0, "kinds": {"structure/extract_method": 0.8}}


@pytest.fixture(scope="module")
def mods(refactor):
    names = ("budget", "timeline", "pause", "allocation", "culprit", "process", "time_report", "commands.catch_up")
    return {n.split(".")[-1]: importlib.import_module(f"refactor_lib.{n}") for n in names}


def _state(strategy="local-full", w=395.8, c=504.0, ci_check=None, **over):
    from crossref_helpers import strategy_state

    state = {
        "budget_minutes": 30,
        "started_at": START.isoformat(),
        "strategy": strategy_state(strategy),
        "ci_check": ci_check,
        "baseline_test": {"mode": "whole", "seconds": w, "whole_seconds": w, "ci_seconds": c},
    }
    state.update(over)
    return state


# ---------- 実装へ回す時間（F1） ----------


def test_pr1801_leaves_seven_minutes_for_the_items(mods):
    """PR 1801 の入力（経過 12.9 分）で、使える時間が 7.0 分以上になる（今は 1.3 分）。"""
    budget = mods["budget"]
    reserve = budget.plan_reserve(_state(), TABLE)
    available = budget.available_minutes(30, 12.9, reserve)
    assert available >= 7.0
    assert available == pytest.approx(30 - 12.9 - 395.8 / 60 - 2.6, abs=0.01)


def test_the_final_gate_keeps_its_buffer(mods):
    """I2: 最終ゲートの全体テスト・修正・最終ゲート修正はバッファに残り、危険フラグの全体テストは最終ゲートと兼ねる（I3）。"""
    reserve = mods["budget"].plan_reserve(_state(), TABLE)
    assert reserve["final_whole_test"] == pytest.approx(395.8 / 60)
    assert reserve["fix"] == reserve["final_fix"] == 1.3
    assert reserve["danger_whole_test"] == 0.0


def test_ci_strategies_keep_their_buffer(mods):
    """I3: CI に任せる戦略は危険フラグ 0・最終ゲート c のまま。`--ci-check` の手元の戦略は危険フラグ w を残す。"""
    budget = mods["budget"]
    on_ci = budget.plan_reserve(_state("local-scoped-ci-whole"), TABLE)
    assert (on_ci["danger_whole_test"], on_ci["final_whole_test"]) == (0.0, pytest.approx(504.0 / 60))
    checked = budget.plan_reserve(_state(ci_check="tests"), TABLE)
    assert (checked["danger_whole_test"], checked["final_whole_test"]) == (pytest.approx(395.8 / 60), pytest.approx(504.0 / 60))


def test_the_final_end_stays_at_the_budget(mods):
    """I1: 計画の後の表でも、最終ゲート修正の打ち切りは開始 + B のまま。"""
    reserve = mods["budget"].plan_reserve(_state(), TABLE)
    items = [{"status": "planned", "estimate": {"test": 0.0, "implement": 1.0, "verify": 0.2}}]
    limits = mods["timeline"].compute(START, 30, 395.8, items, reserve)
    assert limits["final_end_at"] == (START + 30 * M).isoformat(timespec="seconds")


# ---------- 実装の終わり（I13） ----------


def test_the_implement_end_ignores_the_item_estimates(mods):
    """rf1718 の形（B = 60）: 実装の終わりは打ち切り − R − Σ（未検証の verify）で、項目の実装の見積りを引かない。"""
    reserve = {"danger_whole_test": 0.0, "final_whole_test": 6.6, "fix": 1.3, "final_fix": 1.3}
    items = [
        {"status": "planned", "estimate": {"test": 0.0, "implement": 3.0, "verify": 0.5}} for _ in range(10)
    ] + [{"status": "verified", "estimate": {"test": 0.0, "implement": 3.0, "verify": 9.0}}, {"status": "carried", "estimate": {"verify": 9.0}}]
    limits = mods["timeline"].compute(START, 60, 20.0, items, reserve)
    implement_end = START + (60 - 9.2 - 5.0) * M
    assert limits["implement_end_at"] == implement_end.isoformat(timespec="seconds")
    assert limits["add_tests_end_at"] == (implement_end - 30 * M).isoformat(timespec="seconds")
    # 開始から 40 分のコミットは実装の終わり（45.8 分）の前にある
    assert START + 40 * M < implement_end


# ---------- 提案の前に止まる（F2・I8） ----------


def _window(mods, budget_minutes, x, after, resumed=None):
    return mods["timeline"].compute(START, budget_minutes, x, offset_seconds=resumed if resumed is not None else x, after_plan=after)


def test_a_budget_without_room_for_one_item_stops_before_proposing(mods):
    """w = x = 600 秒: 枠の後にバッファの見込み 12.6 分と 1 件 1.0 分が入らず、下限 41 分を出して止まる。"""
    after = {"reserve_minutes": 12.6, "item_minutes": 1.0, "table_source": "history"}
    problem = mods["timeline"].window_problem(_window(mods, 30, 600.0, after))
    assert problem is not None
    assert "41 以上" in problem
    # 下限で打ち直すと（打ち直しは開始から 600 + 300 秒まで）、着手前のテストを走らせ直さずに提案へ進む
    assert mods["timeline"].window_problem(_window(mods, 41, 600.0, after, resumed=900.0)) is None


def test_pr1745_and_pr1801_proceed_to_proposals(mods):
    """1 件の長さが入る限り止まらない（PR 1745: x = 389.5 秒・PR 1801: x = 395.8 秒）。"""
    budget, timeline = mods["budget"], mods["timeline"]
    for x in (389.5, 395.8):
        state = _state(w=x)
        reserve = budget.plan_reserve(state, TABLE)
        after = {"reserve_minutes": budget.reserve_total(reserve), "item_minutes": budget.shortest_item_minutes(TABLE, 0.0)}
        assert timeline.window_problem(_window(mods, 30, x, after)) is None


def test_the_required_budget_reduces_to_the_old_formula_without_a_forecast(mods):
    timeline = mods["timeline"]
    assert timeline.required_budget_minutes(600.0) == timeline.required_budget_minutes(600.0, 0.0) == 22
    assert timeline.required_budget_minutes(600.0, 13.6) == 41


def test_the_shortest_item_skips_the_test(mods):
    assert mods["budget"].shortest_item_minutes(TABLE, 0.0) == pytest.approx(0.8 + 0.2)
    assert mods["budget"].shortest_item_minutes(TABLE, 0.5) == pytest.approx(0.8 + 0.5)


# ---------- 再開（F3・#1491） ----------


def _planned_state(**over):
    state = {
        "budget_minutes": 30,
        "plan": {"reserve": {"final_whole_test": 6.6, "fix": 1.3, "final_fix": 1.3}},
        "limits": {
            "plan_end_at": (START + 9 * M).isoformat(),
            "add_tests_end_at": (START + 8 * M).isoformat(),
            "implement_end_at": (START + 18 * M).isoformat(),
            "fix_end_at": (START + 22 * M).isoformat(),
            "final_end_at": (START + 30 * M).isoformat(),
            "stop_revert_end_at": (START + 36 * M).isoformat(),
        },
        "pause": {"seconds": 0, "shifted_seconds": 0, "legacy": False, "events": []},
    }
    state.update(over)
    return state


def _at(state, key):
    return dt.datetime.fromisoformat(state["limits"][key])


def test_a_pause_shifts_only_the_deadlines_still_ahead(mods):
    """I4: 最後の動きの時刻より後の締め切りだけが止まっていた秒だけずれ、前に過ぎていた締め切りは開き直さない。"""
    state = _planned_state()
    event = mods["pause"].catch_up(state, START + 22 * M, START + 10 * M)
    assert event["reason"] == "shifted" and event["seconds"] == 720
    assert _at(state, "add_tests_end_at") == START + 8 * M  # 中断の前に過ぎていた
    assert _at(state, "implement_end_at") == START + 30 * M
    assert _at(state, "fix_end_at") == START + 34 * M
    assert _at(state, "final_end_at") == START + 42 * M
    assert _at(state, "stop_revert_end_at") == START + 48 * M
    assert state["pause"]["shifted_seconds"] == 720
    # ずれた後の実装の終わりから出した監視の上限は正（採った項目が実装へ進む）
    assert mods["timeline"].phase_timeout(_at(state, "implement_end_at"), START + 22 * M, 90) > 90


def test_a_past_implement_end_is_not_reopened(mods):
    state = _planned_state()
    mods["pause"].catch_up(state, START + 40 * M, START + 20 * M)
    assert _at(state, "implement_end_at") == START + 18 * M


def test_a_pause_within_the_margin_does_not_shift(mods):
    """I5: 余裕（0.05·B = 90 秒）以下ならずらさず、`within_margin` を 1 件残す。境界を含む。"""
    state = _planned_state()
    event = mods["pause"].catch_up(state, START + 10 * M + dt.timedelta(seconds=90), START + 10 * M)
    assert event["reason"] == "within_margin" and not event["shifted"]
    assert _at(state, "final_end_at") == START + 30 * M
    assert len(state["pause"]["events"]) == 1
    event = mods["pause"].catch_up(state, START + 12 * M + dt.timedelta(seconds=91), START + 12 * M)
    assert event["reason"] == "shifted"


def test_two_pauses_add_up(mods):
    state = _planned_state()
    mods["pause"].catch_up(state, START + 15 * M, START + 10 * M)
    mods["pause"].catch_up(state, START + 23 * M, START + 16 * M)
    assert state["pause"]["shifted_seconds"] == 720
    assert _at(state, "final_end_at") == START + 42 * M


def test_a_legacy_state_is_not_shifted(mods):
    """I6: `pause` を持たない状態はずらさず 1 度だけ記録し、2 回目の再開でもずらさない。"""
    state = _planned_state()
    del state["pause"]
    first = mods["pause"].catch_up(state, START + 22 * M, START + 10 * M)
    assert first["reason"] == "legacy"
    assert mods["pause"].catch_up(state, START + 40 * M, START + 23 * M) is None
    assert _at(state, "final_end_at") == START + 30 * M
    assert mods["pause"].report_line(state).startswith("止まっていた時間: 計測しない")


def test_nothing_is_recorded_before_the_plan_or_after_the_final_gate(mods):
    """I7"""
    before = _planned_state(plan=None)
    assert mods["pause"].catch_up(before, START + 22 * M, START + 10 * M) is None
    after = _planned_state(final_gate={"status": "failing"})
    assert mods["pause"].catch_up(after, START + 22 * M, START + 10 * M) is None
    assert after["pause"]["events"] == []


def test_the_newest_file_is_the_last_activity(mods, tmp_path):
    """決定 3: 状態ファイルより新しいログのファイルがあれば、その時刻が最後の動きの時刻になる。"""
    state_file, log = tmp_path / "cross-refactoring-rf7-state.json", tmp_path / "claude-implement-rf7-err.log"
    state_file.write_text("{}")
    log.write_text("x")
    old = time.time() - 600
    os.utime(state_file, (old, old))
    os.utime(log, (old + 300, old + 300))
    got = mods["pause"].last_activity(tmp_path)
    assert got.timestamp() == pytest.approx(old + 300, abs=1)


def test_catch_up_twice_shifts_once(mods, tmp_path, monkeypatch):
    """I10: 続けて 2 度打っても、1 度目の保存で最後の動きの時刻が今になり、ずれるのは 1 度だけ。報告に 1 行残る。"""
    now = dt.datetime.now().astimezone()
    state = _planned_state(started_at=now.isoformat())
    for key in ("add_tests_end_at", "implement_end_at", "fix_end_at", "final_end_at", "stop_revert_end_at"):
        state["limits"][key] = (now + 5 * M).isoformat(timespec="seconds")
    path = tmp_path / "cross-refactoring-rf7-state.json"
    path.write_text(json.dumps(state))
    old = time.time() - 600
    os.utime(path, (old, old))
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    catch_up = mods["catch_up"]
    catch_up.cmd_catch_up(argparse.Namespace(id=7))
    catch_up.cmd_catch_up(argparse.Namespace(id=7))
    saved = json.loads(path.read_text())
    reasons = [e["reason"] for e in saved["pause"]["events"]]
    assert reasons[0] == "shifted" and all(r == "within_margin" for r in reasons[1:])
    assert saved["pause"]["shifted_seconds"] == pytest.approx(600, abs=5)
    assert mods["pause"].report_line(saved).startswith("止まっていた時間:")


def test_catch_up_without_a_state_file_does_nothing(mods, tmp_path, monkeypatch):
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(mods["catch_up"], "existing_state", lambda _id: None)
    mods["catch_up"].cmd_catch_up(argparse.Namespace(id=7))
    assert list(tmp_path.iterdir()) == []


def test_the_fix_deadline_is_read_from_the_shifted_table(mods):
    """決定 5: 直しの試行の打ち切りは表の `fix_end_at` から読む（ずらした値を検証と原因の判定が同じに読む）。"""
    state = _planned_state(started_at=START.isoformat())
    mods["pause"].catch_up(state, START + 22 * M, START + 10 * M)
    assert mods["culprit"].fix_deadline(state) == START + 34 * M


# ---------- 心拍のファイル（I12） ----------


def _ages_while(fn, alive, samples=(0.25, 0.45)):
    ages = []
    worker = threading.Thread(target=fn)
    started = time.monotonic()
    worker.start()
    for at in samples:
        time.sleep(max(0.0, at - (time.monotonic() - started)))
        ages.append(time.time() - alive.stat().st_mtime)
    worker.join()
    return ages


def test_a_silent_test_keeps_the_heartbeat(mods, tmp_path, monkeypatch):
    """出力の無いテストを待つ間も、心拍のファイルは心拍の間隔の 2 倍より古くならない。"""
    monkeypatch.setattr(sys.modules["monitor_types"], "HEARTBEAT_SECONDS", 0.1)
    alive = tmp_path / "cross-refactoring-rf7-alive"
    monkeypatch.setattr(mods["process"], "ALIVE_FILE", alive)
    command = [sys.executable, "-c", "import time; time.sleep(0.6)"]
    ages = _ages_while(lambda: mods["process"].run_with_timeout(command, str(tmp_path), 10, reach=False), alive)
    assert all(age < 0.2 for age in ages), ages


def test_the_heartbeat_does_not_follow_the_poll(mods, tmp_path, monkeypatch):
    """`monitor.py --alive-file` の心拍は見回りの間隔に依らない（見回りが長くても心拍の間隔で書く）。"""
    monitor_types = sys.modules["monitor_types"]
    monkeypatch.setattr(monitor_types, "HEARTBEAT_SECONDS", 0.1)
    monkeypatch.setattr(monitor_types, "DEFAULT_POLL", 30)
    alive = tmp_path / "alive"

    def wait():
        with monitor_types.heartbeat(alive):
            time.sleep(0.6)

    ages = _ages_while(wait, alive)
    assert all(age < 0.2 for age in ages), ages


def test_the_entry_sets_the_alive_file(refactor, mods, tmp_path, monkeypatch):
    (tmp_path / "cross-refactoring-rf7-state.json").write_text("{}")
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(mods["process"], "ALIVE_FILE", None)
    refactor._set_alive_file(argparse.Namespace(id=7))
    assert mods["process"].ALIVE_FILE == tmp_path / "cross-refactoring-rf7-alive"


# ---------- 報告と実行の行（F4・F5） ----------


def _finished_state():
    return {
        "plan": {"reserve": {"danger_whole_test": 0.0, "final_whole_test": 6.6, "fix": 1.3, "final_fix": 1.3}},
        "whole_test": {"ran": False},
        "final_gate": {"checks": [{"mode": "test", "seconds": 356.0}, {"mode": "lint", "seconds": 20.0}]},
        "fix_stats": {"launches": 0, "seconds": 0.0},
        "phases": {"verify": {"seconds": 4.7}},
        "pause": {"seconds": 720.0, "shifted_seconds": 720.0, "legacy": False, "events": [{"shifted": True, "seconds": 720.0}]},
    }


def test_the_unused_buffer_is_reported_per_category(mods):
    usage = mods["allocation"].reserve_usage(_finished_state())
    assert usage["final_whole_test"] == {"reserved_seconds": 396, "used_seconds": 356, "unused_seconds": 40}
    assert usage["fix"]["unused_seconds"] == usage["final_fix"]["unused_seconds"] == 78
    assert usage["danger_whole_test"]["reserved_seconds"] == 0
    lines = mods["time_report"].lines(_finished_state())
    assert lines[0].startswith("- バッファのうち使わずに残った時間: 危険フラグの全体テスト 0.0 / 最終ゲートの全体テスト 0.7 / 修正 1.3 / 最終ゲート修正 1.3 分")
    assert any(line.startswith("- 止まっていた時間: 12.0 分（1 回。締め切りを 12.0 分ずらした）") for line in lines)
    assert not any("バッファを越えた" in line for line in lines)


def test_a_danger_whole_test_outside_the_buffer_is_reported(mods):
    state = _finished_state()
    state["whole_test"] = {"ran": True, "seconds": 396.0}
    state["fix_stats"]["cut_off"] = True
    line = mods["time_report"].over_line(state)
    assert line == "- バッファを越えた全体テスト: 危険フラグの全体テスト 6.6 分をバッファの外で走らせた（省いたもの: 直しの試行）"


def test_the_run_row_keeps_the_buffer_and_the_pause(mods):
    row = mods["allocation"].build_row({**_finished_state(), "started_at": START.isoformat(), "id": 7})
    assert row["reserve"]["final_whole_test"]["unused_seconds"] == 40
    assert row["paused_seconds"] == 720.0


def test_old_rows_build_the_same_table(mods):
    """I11: 新しいキーを持つ行と持たない行で、配分テーブルは同じ値になる。"""
    allocation = mods["allocation"]
    base = {"kind": "run", "kinds": {"structure/extract_method": {"count": 2, "seconds": 120}}, "verify": {"items": 2, "seconds": 30}}
    defaults = {"test": 2.7, "structure": 1.0, "verify": 0.2, "fix": 1.3}
    new = {**base, "reserve": {"fix": {"reserved_seconds": 78, "used_seconds": 0, "unused_seconds": 78}}, "paused_seconds": 720}
    assert allocation.build_table([base], defaults) == allocation.build_table([new], defaults)


# ---------- 採り直しの巡（決定 10） ----------


def test_only_this_rounds_items_raise_the_whole_test(refactor, tmp_path):
    """前の巡の項目のフラグは次の巡の全体テストを走らせない。今の巡の項目のフラグは走らせる。"""
    converge = importlib.import_module("refactor_lib.commands.converge")
    old = {"id": "I-001", "round": 1, "status": "verified", "danger": ["D1"], "danger_checked": True}
    new = {"id": "I-002", "round": 2, "status": "verified", "danger": [], "danger_checked": True}
    state = {"items": [old, new], "readopt": {"round": 2}, "worktrees": {"work": str(tmp_path)}, "target_scope": ["src"]}
    assert converge._flag_items(state) == []
    new["danger"] = ["D2"]
    assert converge._flag_items(state) == ["D2"]


def test_ci_deferred_flags_survive_the_next_round(refactor):
    """1 巡目に CI へ寄せた危険フラグは、記録が履歴へ移っても最終ゲートの取り消しと報告が読む。"""
    rounds = importlib.import_module("refactor_lib.rounds")
    state = {
        "whole_test_history": [{"round": 1, "record": {"resolution": "deferred", "deferred": {"flags": ["D4"], "items": ["I-001"]}}}],
        "whole_test": {"ran": False},
    }
    assert rounds.deferred_union(state) == {"flags": ["D4"], "items": ["I-001"]}
    state["whole_test"] = {"deferred": {"flags": ["D1", "D4"], "items": ["I-003"]}}
    assert rounds.deferred_union(state) == {"flags": ["D1", "D4"], "items": ["I-001", "I-003"]}
    assert rounds.deferred_union({"whole_test": {}}) == {}


def test_the_readopt_line_counts_rounds_and_leftovers(mods):
    state = {
        "readopt": {
            "round": 2,
            "events": [
                {"reason": "selected", "round": 2, "selected": ["I-005", "I-006"], "available_minutes": 3.8},
                {"reason": "no_fit", "round": 2, "selected": [], "available_minutes": 0.4},
            ],
        },
        "deferred_items": [{"defer_reason": "budget"}] * 21 + [{"defer_reason": "rank"}],
    }
    assert mods["time_report"].readopt_line(state) == (
        "- 採り直し: 1 回で 2 件（I-005・I-006）。最後の判定の残った時間 0.4 分。採らずに残った `budget` の候補 21 件"
    )
