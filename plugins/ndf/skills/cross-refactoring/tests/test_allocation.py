"""配分テーブルの集計と履歴の読み書き（#933 の「データ構造: 履歴と配分テーブル」）。"""
from __future__ import annotations

import importlib
import json
import pathlib

import pytest


@pytest.fixture(scope="module")
def allocation(refactor):
    return importlib.import_module("refactor_lib.allocation")


DEFAULTS = {"test": 2.7, "structure": 1.3, "verify": 0.2, "fix": 5.5}


def test_defaults_file_holds_917_values_and_source(allocation):
    assert allocation.load_defaults() == DEFAULTS
    data = json.loads(allocation.DEFAULTS_PATH.read_text(encoding="utf-8"))
    assert data["source"]["url"].startswith("https://github.com/devbasex/ai-plugins/pull/917")
    assert all(entry["formula"] for entry in data["values"].values())


def test_history_path_stays_under_given_base(allocation, tmp_path):
    path = allocation.history_path(tmp_path, "devbasex/ai-plugins")
    assert path == tmp_path / "devbasex--ai-plugins" / allocation.HISTORY_NAME
    # 利用者の ~/.local/state を触らない
    assert pathlib.Path.home() / ".local" / "state" not in path.parents


def test_missing_or_broken_history_uses_defaults(allocation, tmp_path):
    path = allocation.history_path(tmp_path, "o/r")
    assert allocation.read_history(path) == []
    path.parent.mkdir(parents=True)
    path.write_text("not json\n[1,2]\n\n", encoding="utf-8")
    rows = allocation.read_history(path)
    assert rows == []
    table = allocation.build_table(rows, DEFAULTS)
    assert table == {"source": "defaults", **DEFAULTS, "kinds": {}}


def test_build_table_per_kind_window_and_fallbacks(allocation):
    rows = []
    # 古い 1 行だけが珍しい手法を持つ。後ろに test だけの行が 12 行続いても遡れる
    rows.append({"kinds": {"structure/rare": {"count": 1, "seconds": 600},
                           "test": {"count": 1, "seconds": 6000}}})
    for _ in range(12):
        rows.append({"kinds": {"test": {"count": 2, "seconds": 240},
                               "structure/extract_method": {"count": 0, "seconds": 0}},
                     "verify": {"items": 4, "seconds": 48},
                     "fix": {"launches": 0, "seconds": 0}})
    table = allocation.build_table(rows, DEFAULTS)
    assert table["source"] == "history"
    # test は直近 10 行（すべて 2 件 240 秒）で 2 分。古い 6000 秒の行は窓の外
    assert table["test"] == pytest.approx(2.0)
    assert table["verify"] == pytest.approx(0.2)
    # 件数 0 の行は数えない → fix は初期値、extract_method は値が出ない
    assert table["fix"] == 5.5
    assert table["kinds"] == {"structure/rare": pytest.approx(10.0)}
    assert table["structure"] == pytest.approx(10.0)
    assert allocation.lookup(table, "structure/rare") == pytest.approx(10.0)
    assert allocation.lookup(table, "structure/extract_method") == pytest.approx(10.0)
    assert allocation.lookup(table, "fix") == 5.5


def test_structure_aggregate_sums_all_techniques(allocation):
    rows = [{"kinds": {"structure/a": {"count": 1, "seconds": 60},
                       "structure/b": {"count": 3, "seconds": 420}}}]
    table = allocation.build_table(rows, DEFAULTS)
    assert table["structure"] == pytest.approx(2.0)
    assert table["kinds"]["structure/b"] == pytest.approx(140 / 60)
    assert table["test"] == 2.7


def _state():
    return {
        "id": 917, "current_pr": 917, "implementer": "claude", "budget_minutes": 60,
        "started_at": "2026-09-23T14:29:12+00:00", "ended_at": "2026-09-23T15:22:32+00:00",
        "phases": {"propose": {"started_at": "a", "ended_at": "b", "seconds": 272},
                   "plan": {"seconds": 180}},
        "items": [
            {"id": "I-001", "kind": "structure/extract_method",
             "commits": {"test": "t1", "implement": "i1", "fix": []},
             "seconds": {"test": 150, "implement": 80}},
            {"id": "I-002", "kind": "structure/extract_method",
             "commits": {"test": None, "implement": "i2", "fix": []},
             "seconds": {"test": None, "implement": 40}},
            {"id": "I-003", "kind": "structure/rename",
             "commits": {"test": None, "implement": None},
             "seconds": {}},
        ],
        "verify_stats": {"items": 2, "seconds": 20},
        "fix_stats": {"launches": 1, "seconds": 300},
        "baseline_test": {"command": "pytest", "seconds": 59},
        "whole_test": {"ran": False, "seconds": 70},
        "final_gate": {"whole_test_seconds": 61},
    }


def test_build_row_and_append_round_trip(allocation, tmp_path):
    row = allocation.build_row(_state())
    assert row["schema"] == 1
    assert row["run"] == "rf917-20260923T142912Z"
    assert row["pr"] == 917
    assert row["elapsed_seconds"] == 3200
    assert row["phases"] == {"propose": 272, "plan": 180}
    assert row["kinds"] == {"test": {"count": 1, "seconds": 150},
                            "structure/extract_method": {"count": 2, "seconds": 120}}
    assert row["verify"] == {"items": 2, "seconds": 20}
    assert row["fix"] == {"launches": 1, "seconds": 300}
    # 走らなかった危険の印の全体のテストは null
    assert row["whole_test"] == {"init": 59, "danger": None, "final": 61}

    path = allocation.history_path(tmp_path, "devbasex/ai-plugins")
    allocation.append_row(path, row)
    allocation.append_row(path, row)
    rows = allocation.read_history(path)
    assert len(rows) == 2 and rows[0] == row
    table = allocation.build_table(rows, DEFAULTS)
    assert table["test"] == pytest.approx(2.5)
    assert table["kinds"]["structure/extract_method"] == pytest.approx(1.0)
    assert table["fix"] == pytest.approx(5.0)


def test_build_row_skips_reverted_and_deferred_items(allocation):
    # 取り消し・見送りはコミットの欄を残したまま状態だけが変わる
    state = _state()
    for status in ("reverted", "deferred"):
        state["items"].append(
            {"id": f"I-{status}", "kind": "structure/extract_method", "status": status,
             "commits": {"test": "tx", "implement": "ix"},
             "seconds": {"test": 999, "implement": 999}})
    assert allocation.build_row(state)["kinds"] == allocation.build_row(_state())["kinds"]


def test_build_row_without_stats_writes_zeros(allocation):
    state = _state()
    for key in ("verify_stats", "fix_stats", "ended_at"):
        state.pop(key)
    row = allocation.build_row(state)
    assert row["verify"] == {"items": 0, "seconds": 0}
    assert row["fix"] == {"launches": 0, "seconds": 0}
