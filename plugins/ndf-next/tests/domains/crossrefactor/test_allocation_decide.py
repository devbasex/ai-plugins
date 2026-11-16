"""配分の値を初期値と宣言から決める判断（#3617 の受け入れ条件 12・I9）。"""

from __future__ import annotations

import json

import pytest

from ndf import identity
from ndf.domains.crossrefactor import allocation_decide

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
