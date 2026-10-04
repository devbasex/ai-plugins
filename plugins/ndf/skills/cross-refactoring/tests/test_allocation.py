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
    rows.append({"kinds": {"structure/rare": {"count": 1, "seconds": 600}, "test": {"count": 1, "seconds": 6000}}})
    for _ in range(12):
        rows.append(
            {
                "kinds": {"test": {"count": 2, "seconds": 240}, "structure/extract_method": {"count": 0, "seconds": 0}},
                "verify": {"items": 4, "seconds": 48},
                "fix": {"launches": 0, "seconds": 0},
            }
        )
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
    rows = [{"kinds": {"structure/a": {"count": 1, "seconds": 60}, "structure/b": {"count": 3, "seconds": 420}}}]
    table = allocation.build_table(rows, DEFAULTS)
    assert table["structure"] == pytest.approx(2.0)
    assert table["kinds"]["structure/b"] == pytest.approx(140 / 60)
    assert table["test"] == 2.7


def _state():
    return {
        "id": 917,
        "current_pr": 917,
        "implementer": "claude",
        "budget_minutes": 60,
        "started_at": "2026-09-23T14:29:12+00:00",
        "ended_at": "2026-09-23T15:22:32+00:00",
        "phases": {"propose": {"started_at": "a", "ended_at": "b", "seconds": 272}, "plan": {"seconds": 180}},
        "items": [
            {
                "id": "I-001",
                "kind": "structure/extract_method",
                "commits": {"test": "t1", "implement": "i1", "fix": []},
                "seconds": {"test": 150, "implement": 80},
            },
            {
                "id": "I-002",
                "kind": "structure/extract_method",
                "commits": {"test": None, "implement": "i2", "fix": []},
                "seconds": {"test": None, "implement": 40},
            },
            {"id": "I-003", "kind": "structure/rename", "commits": {"test": None, "implement": None}, "seconds": {}},
        ],
        "verify_stats": {"items": 2, "seconds": 20},
        "fix_stats": {"launches": 1, "seconds": 300},
        "baseline_test": {"command": "pytest", "seconds": 59},
        "whole_test": {"ran": False, "seconds": 70},
        "final_gate": {"whole_test_seconds": 61},
    }


def test_build_row_and_append_round_trip(allocation, tmp_path):
    row = allocation.build_row(_state())
    assert row["schema"] == 2 and row["kind"] == "run"
    assert row["run"] == "rf917-20260923T142912Z"
    assert row["pr"] == 917
    assert row["elapsed_seconds"] == 3200
    assert row["phases"] == {"propose": 272, "plan": 180}
    assert row["kinds"] == {"test": {"count": 1, "seconds": 150}, "structure/extract_method": {"count": 2, "seconds": 120}}
    assert row["verify"] == {"items": 2, "seconds": 20}
    assert row["fix"] == {"launches": 1, "seconds": 300}
    # 走らなかった危険フラグの全体のテストは null
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
            {
                "id": f"I-{status}",
                "kind": "structure/extract_method",
                "status": status,
                "commits": {"test": "tx", "implement": "ix"},
                "seconds": {"test": 999, "implement": 999},
            }
        )
    assert allocation.build_row(state)["kinds"] == allocation.build_row(_state())["kinds"]


def test_build_row_without_stats_writes_zeros(allocation):
    state = _state()
    for key in ("verify_stats", "fix_stats", "ended_at"):
        state.pop(key)
    row = allocation.build_row(state)
    assert row["verify"] == {"items": 0, "seconds": 0}
    assert row["fix"] == {"launches": 0, "seconds": 0}


# ---------- 着手前のテストの行（#1385 決定 3・4・6、I7〜I9） ----------

CI = "local-scoped-ci-whole"


def _init_row(allocation, seconds, locations=("plugins/ndf",), strategy=CI, timed_out=False, mode="scope"):
    baseline = {
        "mode": mode,
        "locations": list(locations) if mode == "scope" else None,
        "seconds": seconds,
        "timed_out": timed_out,
        "checked_at": "2026-10-04T08:00:00",
    }
    return allocation.init_test_row(baseline, strategy, 30, 1554)


def test_the_init_test_row_keeps_the_strategy_the_locations_and_the_seconds(allocation, tmp_path):
    """AC11: 着手前のテストの実測と範囲と戦略が履歴に残る。置き場所は並べ替えた集合で残す。"""
    row = _init_row(allocation, 320.0, locations=("tests", "plugins/ndf", "tests"))
    assert row == {
        "schema": 2,
        "kind": "init_test",
        "at": "2026-10-04T08:00:00",
        "pr": 1554,
        "budget_minutes": 30,
        "strategy": CI,
        "mode": "scope",
        "locations": ["plugins/ndf", "tests"],
        "seconds": 320.0,
        "timed_out": False,
    }
    assert _init_row(allocation, 600.0, mode="whole")["locations"] is None
    path = allocation.history_path(tmp_path, "acme/demo")
    allocation.append_row(path, row)
    assert allocation.scope_seconds(allocation.read_history(path), CI, ["tests", "plugins/ndf"]) == 320.0


def test_scope_seconds_takes_the_largest_of_the_last_ten_matching_rows(allocation):
    """I7: 同じ戦略・同じ置き場所の集合の直近 10 行の最大。打ち切りの行（上限の秒）も混ぜて大きい側を採る。"""
    rows = [_init_row(allocation, 999.0)]  # 11 行前は使わない
    rows += [_init_row(allocation, float(s)) for s in (300, 310, 180, 320, 305, 300, 301, 302, 303)]
    rows += [_init_row(allocation, 180.0, timed_out=True)]
    rows += [_init_row(allocation, 5000.0, strategy="local-full")]  # 戦略が違う
    rows += [_init_row(allocation, 5000.0, locations=("plugins/ndf/scripts",))]  # 置き場所が違う
    assert allocation.scope_seconds(rows, CI, ["plugins/ndf"]) == 320.0
    assert allocation.scope_seconds(rows, CI, ["plugins"]) is None
    assert allocation.scope_seconds(rows, CI, []) is None


def test_old_rows_are_read_as_runs_and_not_used_for_the_scope(allocation):
    """I9: schema 1 の行と kind の無い行は実行の行として読み、範囲の一致には使わない。"""
    legacy = {"schema": 1, "strategy": CI, "mode": "scope", "locations": ["plugins/ndf"], "seconds": 900.0}
    no_kind = dict(legacy, schema=2)
    assert allocation.scope_seconds([legacy, no_kind], CI, ["plugins/ndf"]) is None
    assert allocation.run_rows([legacy, no_kind]) == [legacy, no_kind]


def test_the_table_ignores_the_init_test_rows(allocation):
    """I8: 着手前のテストの行を混ぜても配分テーブルの値と source は変わらない。"""
    runs = [allocation.build_row(_state())]
    mixed = [_init_row(allocation, 320.0), *runs, _init_row(allocation, 180.0, timed_out=True)]
    assert allocation.build_table(mixed, DEFAULTS) == allocation.build_table(runs, DEFAULTS)
    assert allocation.build_table([_init_row(allocation, 320.0)], DEFAULTS)["source"] == "defaults"


def test_a_scope_run_does_not_write_the_whole_test_seconds(allocation):
    """決定 6: 範囲テストだけを着手前に走らせた実行の行は whole_test.init を持たない。"""
    state = _state()
    state["baseline_test"] = {"mode": "scope", "command": "pytest plugins/ndf", "seconds": 320.0}
    assert allocation.build_row(state)["whole_test"]["init"] is None
    state["baseline_test"]["mode"] = "round"
    assert allocation.build_row(state)["whole_test"]["init"] == 320.0
