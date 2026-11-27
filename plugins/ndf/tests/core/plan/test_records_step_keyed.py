"""ステップごとの JSON 記録（`step-gaps.json`・`step-missing.json`）を読み、dict でなければ空とし、そのステップの項目を外して入れ直す振る舞い（`core/plan/records.py`）。"""

from __future__ import annotations

import json

import pytest

from ndf.core.plan import records

GAP = {"name": "delivery", "reason": "欠けた"}


def _raw(path):
    return json.loads(path.read_text(encoding="utf-8"))


# --- _keep_step_gaps ---


def test_gaps_of_other_steps_are_kept_when_one_step_is_rewritten(tmp_path):
    records._keep_step_gaps(tmp_path, "a", [GAP])
    records._keep_step_gaps(tmp_path, "b", [GAP])
    records._keep_step_gaps(tmp_path, "a", [{"name": "x"}])
    assert _raw(tmp_path / records.STEP_GAPS_FILE) == {"b": [GAP], "a": [{"name": "x"}]}


def test_gaps_cleared_for_the_last_step_leave_an_empty_object(tmp_path):
    # 記録があったなら空でも書き直す
    records._keep_step_gaps(tmp_path, "a", [GAP])
    records._keep_step_gaps(tmp_path, "a", None)
    assert _raw(tmp_path / records.STEP_GAPS_FILE) == {}


@pytest.mark.parametrize("gaps", [None, []])
def test_no_gaps_and_no_record_creates_no_file(tmp_path, gaps):
    records._keep_step_gaps(tmp_path, "a", gaps)
    assert not (tmp_path / records.STEP_GAPS_FILE).exists()


@pytest.mark.parametrize("content", ["[1, 2]", '"text"', "{broken"])
def test_gaps_over_a_record_that_is_not_an_object_start_from_empty(tmp_path, content):
    path = tmp_path / records.STEP_GAPS_FILE
    path.write_text(content, encoding="utf-8")
    records._keep_step_gaps(tmp_path, "a", [GAP])
    assert _raw(path) == {"a": [GAP]}


@pytest.mark.parametrize("content", ["[1, 2]", "{broken"])
def test_no_gaps_over_a_record_that_is_not_an_object_leaves_it_untouched(tmp_path, content):
    path = tmp_path / records.STEP_GAPS_FILE
    path.write_text(content, encoding="utf-8")
    records._keep_step_gaps(tmp_path, "a", None)
    assert path.read_text(encoding="utf-8") == content


def test_gaps_are_stored_as_given(tmp_path):
    gaps = [GAP]
    records._keep_step_gaps(tmp_path, "a", gaps)
    assert _raw(tmp_path / records.STEP_GAPS_FILE) == {"a": gaps}


# --- _keep_step_missing ---


def test_missing_names_of_other_steps_are_kept_when_one_step_is_rewritten(tmp_path):
    records._keep_step_missing(tmp_path, "a", "gate", ["x"])
    records._keep_step_missing(tmp_path, "b", "gate", ["y"])
    records._keep_step_missing(tmp_path, "a", "gate", ["z"])
    assert _raw(tmp_path / records.STEP_MISSING_FILE) == {"b": ["y"], "a": ["z"]}


def test_missing_names_cleared_for_the_last_step_leave_an_empty_object(tmp_path):
    records._keep_step_missing(tmp_path, "a", "gate", ["x"])
    records._keep_step_missing(tmp_path, "a", "gate", None)
    assert _raw(tmp_path / records.STEP_MISSING_FILE) == {}


def test_missing_names_are_copied_into_a_list(tmp_path):
    names = ("x", "y")
    records._keep_step_missing(tmp_path, "a", "gate", names)  # type: ignore[arg-type]
    assert _raw(tmp_path / records.STEP_MISSING_FILE) == {"a": ["x", "y"]}


@pytest.mark.parametrize("names", [None, []])
def test_no_missing_names_and_no_record_creates_no_file(tmp_path, names):
    records._keep_step_missing(tmp_path, "a", "gate", names)
    assert not (tmp_path / records.STEP_MISSING_FILE).exists()


@pytest.mark.parametrize("content", ["[1, 2]", '"text"', "{broken"])
def test_missing_names_over_a_record_that_is_not_an_object_start_from_empty(tmp_path, content):
    path = tmp_path / records.STEP_MISSING_FILE
    path.write_text(content, encoding="utf-8")
    records._keep_step_missing(tmp_path, "a", "gate", ["x"])
    assert _raw(path) == {"a": ["x"]}


@pytest.mark.parametrize("content", ["[1, 2]", "{broken"])
def test_no_missing_names_over_a_record_that_is_not_an_object_leaves_it_untouched(tmp_path, content):
    # 空として読んだ内容と変わらないので書かない
    path = tmp_path / records.STEP_MISSING_FILE
    path.write_text(content, encoding="utf-8")
    records._keep_step_missing(tmp_path, "a", "gate", None)
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("status", ["ok", "stopped"])
def test_missing_names_of_a_round_without_a_decision_leave_the_record_untouched(tmp_path, status):
    records._keep_step_missing(tmp_path, "a", "gate", ["x"])
    records._keep_step_missing(tmp_path, "a", status, None)
    records._keep_step_missing(tmp_path, "b", status, ["y"])
    assert _raw(tmp_path / records.STEP_MISSING_FILE) == {"a": ["x"]}
