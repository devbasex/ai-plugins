"""心拍・採り直しの巡・配分の行を、純粋な関数と状態ファイルで確かめる（#1743）。"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import sys
import threading
import time

import pytest

START = dt.datetime(2026, 10, 6, 14, 26, 13).astimezone()
M = dt.timedelta(minutes=1)
TABLE = {"source": "history", "test": 2.7, "verify": 0.2, "fix": 1.3, "structure": 1.0, "kinds": {"structure/extract_method": 0.8}}


@pytest.fixture(scope="module")
def mods(refactor):
    names = ("allocation", "process", "time_report")
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


# ---------- 実装の終わり（I13） ----------


# ---------- 提案の前に止まる（F2・I8） ----------


def _window(mods, budget_minutes, x, after, resumed=None):
    return mods["timeline"].compute(START, budget_minutes, x, offset_seconds=resumed if resumed is not None else x, after_plan=after)


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
            "gate_seconds": 396,
        },
        "pause": {"seconds": 0, "shifted_seconds": 0, "legacy": False, "events": []},
    }
    state.update(over)
    return state


def _at(state, key):
    return dt.datetime.fromisoformat(state["limits"][key])


def _before_plan_state(**over):
    state = _planned_state(plan=None, started_at=START.isoformat(), phase="plan", **over)
    state["limits"]["propose_end_at"] = (START + 6 * M).isoformat()
    state["limits"]["plan_end_at"] = (START + 12 * M).isoformat()
    return state


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


def test_old_rows_build_the_same_table(mods):
    """I11: 新しいキーを持つ行と持たない行で、配分テーブルは同じ値になる。"""
    allocation = mods["allocation"]
    base = {"kind": "run", "kinds": {"structure/extract_method": {"count": 2, "seconds": 120}}, "verify": {"items": 2, "seconds": 30}}
    defaults = {"test": 2.7, "structure": 1.0, "verify": 0.2, "fix": 1.3}
    new = {**base, "reserve": {"fix": {"reserved_seconds": 78, "used_seconds": 0, "unused_seconds": 78}}, "paused_seconds": 720}
    assert allocation.build_table([base], defaults) == allocation.build_table([new], defaults)


# ---------- 採り直しの巡（決定 10） ----------


def test_only_this_rounds_items_raise_the_danger_flags(refactor, tmp_path):
    """前の巡の項目のフラグは次の巡で数え直さない。今の巡の項目のフラグだけを最終ゲートへ寄せる。"""
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
        "- 採り直し: 1 回で 2 件（I-005・I-006）。最後の判定の実装の予算の残り 0.4 分。採らずに残った `budget` の候補 21 件"
    )
