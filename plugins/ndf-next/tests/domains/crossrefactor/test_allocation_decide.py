"""配分の値を初期値と宣言から決める判断（#3617 の受け入れ条件 12・I9）。"""

from __future__ import annotations

import json

import pytest

from ndf import identity
from ndf.domains.crossrefactor import allocation_decide, allocation_history_decide

DEFAULTS = identity.ROOT / "skills/cross-refactoring/data/allocation-defaults.json"


def _defaults() -> dict:
    return json.loads(DEFAULTS.read_text(encoding="utf-8"))


def test_initial_file_is_marked_initial() -> None:
    assert _defaults()["initial"] is True


def test_without_declaration_uses_initial_values() -> None:
    got = allocation_decide.decide(_defaults(), None)
    assert got == {k: v["minutes"] for k, v in _defaults()["values"].items()}


def test_declared_value_wins_and_omitted_kinds_keep_initial() -> None:
    got = allocation_decide.decide(_defaults(), {"allocation_minutes": {"test": 9}})
    assert got["test"] == 9
    assert got["fix"] == _defaults()["values"]["fix"]["minutes"]


def test_declaration_without_allocation_keeps_initial() -> None:
    assert allocation_decide.decide(_defaults(), {"size_unit_seconds": 60}) == allocation_decide.decide(_defaults(), None)


@pytest.mark.parametrize(
    ("defaults", "declared"),
    [
        ({"values": {"test": {"minutes": 1}}}, None),
        ({"initial": True, "values": {"test": {"minutes": 0}}}, None),
        ({"initial": True, "values": {"test": {"minutes": 1}}}, {"allocation_minutes": {"unknown": 1}}),
        ({"initial": True, "values": {"test": {"minutes": 1}}}, {"allocation_minutes": {"test": -1}}),
    ],
)
def test_broken_inputs_stop(defaults: dict, declared: dict | None) -> None:
    with pytest.raises(allocation_decide.AllocationError):
        allocation_decide.decide(defaults, declared)


def _run(kinds: dict, verify=(0, 0), fix=(0, 0), **extra) -> dict:
    return {
        "kind": "run",
        "kinds": kinds,
        "verify": {"items": verify[0], "seconds": verify[1]},
        "fix": {"launches": fix[0], "seconds": fix[1]},
        **extra,
    }


def test_table_without_history_is_the_decided_values() -> None:
    values = allocation_decide.decide(_defaults(), {"allocation_minutes": {"structure": 4}})
    got = allocation_history_decide.build_table([], values)
    assert got == {
        "source": "defaults",
        "test": values["test"],
        "verify": values["verify"],
        "fix": values["fix"],
        "structure": 4.0,
        "kinds": {},
    }


def test_table_from_history_uses_run_rows_only_and_falls_back_per_kind() -> None:
    values = allocation_decide.decide(_defaults(), None)
    rows = [
        _run({"test": {"count": 2, "seconds": 240}, "structure/extract_function": {"count": 1, "seconds": 300}}, verify=(4, 120)),
        {"kind": "init_test", "seconds": 9999},
        _run({"structure/inline": {"count": 3, "seconds": 180}}),
    ]
    got = allocation_history_decide.build_table(rows, values)
    assert got["source"] == "history"
    assert got["test"] == 2.0 and got["verify"] == 0.5 and got["fix"] == values["fix"]
    assert got["kinds"] == {"structure/extract_function": 5.0, "structure/inline": 1.0}
    assert got["structure"] == 2.0  # (300 + 180) 秒 ÷ 4 件
    assert allocation_history_decide.item_estimate(got, "inline", True) == {"test": 2.0, "implement": 1.0}
    assert allocation_history_decide.item_estimate(got, "rename", False) == {"test": 0.0, "implement": 2.0}


def test_table_reads_only_the_latest_window_of_rows_that_have_the_kind() -> None:
    rows = [_run({"test": {"count": 1, "seconds": 6000}})] + [
        _run({"test": {"count": 1, "seconds": 60}}) for _ in range(allocation_history_decide.WINDOW)
    ]
    assert allocation_history_decide.build_table(rows, allocation_decide.decide(_defaults(), None))["test"] == 1.0


def test_run_row_counts_only_adopted_items() -> None:
    state = {
        "pr": 7,
        "items": [
            {"status": "adopted", "technique": "inline", "seconds": {"test": 30, "implement": 90}},
            {"status": "reverted", "technique": "inline", "seconds": {"test": 30, "implement": 90}},
            {"status": "adopted", "seconds": {"implement": 10}},
        ],
        "fix_stats": {"launches": 2, "seconds": 50},
        "verify_stats": {"items": 3, "seconds": 12},
    }
    row = allocation_history_decide.run_row(state, "2026-11-17T00:00:00+00:00")
    assert row["kinds"] == {
        "test": {"count": 1, "seconds": 30.0},
        "structure/inline": {"count": 1, "seconds": 90.0},
        "structure/unknown": {"count": 1, "seconds": 10.0},
    }
    assert row["fix"] == {"launches": 2.0, "seconds": 50.0} and row["verify"] == {"items": 3.0, "seconds": 12.0}
    assert allocation_history_decide.build_table([row], allocation_decide.decide(_defaults(), None))["test"] == 0.5
