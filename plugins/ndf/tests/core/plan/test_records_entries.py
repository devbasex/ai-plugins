"""プランの出力の記録をステップごとの要素の並びで持ち、判断を求めたステップの欠けを残す（`core/plan/records.py`。#4513）。"""

from __future__ import annotations

import pytest

from ndf.core.plan import records


def _merged(tmp_path):
    return records.recorded_outputs(tmp_path)[0]


def test_the_values_of_two_steps_are_kept_in_order(tmp_path):
    records._add_outputs(tmp_path, "a", "ok", {"pr": 3, "product_version": "1.0.0"})
    records._add_outputs(tmp_path, "b", "ok", {"product_version": "1.0.1"})
    assert _merged(tmp_path) == {"pr": 3, "product_version": "1.0.1"}


def test_the_same_step_returning_the_same_key_three_times_keeps_one_entry(tmp_path):
    for n in range(3):
        records._add_outputs(tmp_path, "a", "ok", {"pr": n})
    assert records._stored_entries(tmp_path) == [{"step": "a", "outputs": {"pr": 2}}]


def test_a_gate_without_values_removes_the_earlier_values_of_that_step_only(tmp_path):
    # 却下して打ち直した前の回の値を混ぜない。先のステップの同じキーは残る
    records._add_outputs(tmp_path, "a", "ok", {"product_version": "先"})
    records._add_outputs(tmp_path, "b", "gate", {"product_version": "前の回", "delivery": {"x": 1}})
    records._add_outputs(tmp_path, "b", "gate", None)
    assert records._stored_entries(tmp_path) == [{"step": "a", "outputs": {"product_version": "先"}}]


def test_a_gate_with_values_replaces_the_earlier_values_of_that_step(tmp_path):
    records._add_outputs(tmp_path, "b", "gate", {"product_version": "前の回", "delivery": {"x": 1}})
    records._add_outputs(tmp_path, "b", "gate", {"product_version": "今の回"})
    assert _merged(tmp_path) == {"product_version": "今の回"}


def test_the_former_shape_is_read_as_one_entry_without_a_step_and_kept(tmp_path):
    records.write_json(tmp_path / records.OUTPUTS_FILE, {"version": "11.0.23", "pr": 5})
    assert records._stored_entries(tmp_path) == [{"step": None, "outputs": {"product_version": "11.0.23", "pr": 5}}]
    records._add_outputs(tmp_path, "b", "gate", None)
    assert _merged(tmp_path) == {"product_version": "11.0.23", "pr": 5}


@pytest.mark.parametrize("names", [["product_version"], ["delivery", "product_version"]])
def test_the_missing_names_of_the_last_round_of_a_step_are_kept_and_cleared(tmp_path, names):
    records._keep_step_missing(tmp_path, "release", "gate", names)
    assert records.recorded_step_missing(tmp_path) == {"release": names}
    records._keep_step_missing(tmp_path, "release", "gate", None)
    assert records.recorded_step_missing(tmp_path) == {}


def test_the_missing_names_are_neither_read_nor_written_unless_the_step_asked_for_a_decision(tmp_path, monkeypatch):
    # 欠けを読むのは承認ゲートで止まった最後のステップだけなので、ほかの結果の回は記録に触れない。内容が同じなら書き直さない
    records._keep_step_missing(tmp_path, "release", "gate", ["delivery"])
    monkeypatch.setattr(records, "read_json", lambda _p: pytest.fail("gate でない回は記録を読まない"))
    records._keep_step_missing(tmp_path, "build", "ok", None)
    monkeypatch.undo()
    monkeypatch.setattr(records, "write_json", lambda _p, _v: pytest.fail("内容が変わらなければ書かない"))
    records._keep_step_missing(tmp_path, "release", "gate", ["delivery"])
    records._keep_step_missing(tmp_path, "build", "gate", None)
