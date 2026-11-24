"""`worker_kinds.values_of`・`plan_values_error` の形の確かめ（`VALUES_SHAPE`・`PLAN_SHAPE`）。"""

from __future__ import annotations

from typing import Any

import pytest

from ndf.contracts import worker_kinds as wk

PLAN_ERROR = "work_values は plan, values, inputs, local_tests を持つ辞書にする"


def _held(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"plan": "p", "values": {"a": 1}, "inputs": {}, "local_tests": False, "at": "2026-11-23T00:00:00+00:00"}
    base.update(over)
    return base


def _plan(**over: Any) -> dict[str, Any]:
    held = _held(**over)
    held.pop("at")
    return held


def test_values_of_returns_the_held_values_when_the_shape_matches():
    held = _held()
    assert wk.values_of(held) == held


def test_values_of_moves_the_product_version_held_before_the_rename():
    # PR 4358 の r4-1: 更新の前に始めた試行の工程の入力を、今の作業の定義の名前で引ける
    got = wk.values_of(_held(values={"repo": "/r"}, inputs={"version": "11.0.23", "issues": [1]}))
    assert got is not None and got["inputs"] == {"product_version": "11.0.23", "issues": [1]}
    assert wk.kind_gaps("layout-review", got["values"], got["inputs"]) == {}


def test_values_of_keeps_extra_keys():
    held = _held(extra=1)
    assert wk.values_of(held) == held


@pytest.mark.parametrize("held", [None, [], "x", 1, ["plan"]])
def test_values_of_refuses_a_non_dict(held):
    assert wk.values_of(held) is None


@pytest.mark.parametrize("key", ["plan", "values", "inputs", "local_tests", "at"])
def test_values_of_refuses_a_missing_key(key):
    held = _held()
    del held[key]
    assert wk.values_of(held) is None


@pytest.mark.parametrize(
    "over",
    [{"plan": 1}, {"values": []}, {"inputs": None}, {"local_tests": 0}, {"local_tests": "true"}, {"at": None}],
)
def test_values_of_refuses_a_wrong_type(over):
    assert wk.values_of(_held(**over)) is None


def test_plan_values_error_none_without_the_key():
    assert wk.plan_values_error({}) is None
    assert wk.plan_values_error({"other": 1}) is None


def test_plan_values_error_none_for_the_matching_shape_without_at():
    assert wk.plan_values_error({wk.PLAN_KEY: _plan()}) is None
    assert wk.plan_values_error({wk.PLAN_KEY: wk.plan_values("p", {"a": 1}, True)}) is None


def test_plan_values_error_ignores_at_and_extra_keys():
    assert wk.plan_values_error({wk.PLAN_KEY: _held(at=1, extra=2)}) is None


@pytest.mark.parametrize("held", [None, [], "x", 1])
def test_plan_values_error_message_for_a_non_dict(held):
    assert wk.plan_values_error({wk.PLAN_KEY: held}) == PLAN_ERROR


@pytest.mark.parametrize("key", ["plan", "values", "inputs", "local_tests"])
def test_plan_values_error_message_for_a_missing_key(key):
    held = _plan()
    del held[key]
    assert wk.plan_values_error({wk.PLAN_KEY: held}) == PLAN_ERROR


@pytest.mark.parametrize("over", [{"plan": 1}, {"values": []}, {"inputs": None}, {"local_tests": 1}])
def test_plan_values_error_message_for_a_wrong_type(over):
    assert wk.plan_values_error({wk.PLAN_KEY: _plan(**over)}) == PLAN_ERROR
