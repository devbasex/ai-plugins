"""結果 JSON の `items` の任意の欄（`lens`・`category`・`severity`・`acceptance`）の形（#3684 の受け入れ条件 5）。"""

from __future__ import annotations

import pytest

from ndf.contracts import facts, result


def _errors(*items):
    return result.errors(result.result("t", result.OK, "s", items=list(items)), 0)


@pytest.mark.parametrize("severity", facts.ids(result.SEVERITIES))
def test_an_item_with_the_four_fields_passes(severity):
    item = {"lens": "security", "category": "設計", "severity": severity, "acceptance": ["3684:5"], "path": "a.py"}
    assert _errors(item) == []


def test_items_without_the_fields_and_empty_items_pass():
    assert _errors({"kind": "finding", "id": "r1-1", "outcome": "fixed"}) == []
    assert _errors({"acceptance": []}) == []
    assert _errors() == []


def test_a_severity_row_added_to_the_table_is_accepted(contracts_copy):
    assert len(_errors({"severity": "info"})) == 1
    row = {"id": "info", "rank": -1, "blocking": False, "refactor": False}
    contracts_copy(f"domains/{result.SEVERITIES}.json", lambda d: {**d, "rows": [*d["rows"], row]})
    assert _errors({"severity": "info"}) == []


@pytest.mark.parametrize(
    "item",
    [
        {"severity": "blocker"},
        {"severity": "info"},
        {"severity": None},
        {"severity": 1},
        {"lens": ""},
        {"lens": ["security"]},
        {"category": ""},
        {"acceptance": "3684:5"},
        {"acceptance": [""]},
    ],
)
def test_a_wrong_value_names_the_item(item):
    found = _errors({"kind": "finding"}, item)
    assert len(found) == 1
    assert found[0].startswith(f"items/1/{next(iter(item))}")
