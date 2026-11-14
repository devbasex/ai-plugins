from __future__ import annotations

import pytest

from ndf import contracts
from ndf.contracts import chosen

NEXT = {"argv": ["ndf", "loop", "answer"], "ask": ["comment"]}


def test_record_matches_the_design_example():
    got = chosen.record("run-1", "R07", "script", "wait", "gate", cause="ci_pending")
    assert chosen.errors({**got, "at": "2026-12-01T01:00:00Z"}) == []
    assert {k: v for k, v in got.items() if k != "at"} == {
        "event": "route_chosen",
        "run_id": "run-1",
        "rule": "R07",
        "chooser": "script",
        "from": "wait",
        "to": "gate",
        "cause": "ci_pending",
    }


def test_record_stamps_the_time_when_omitted():
    got = chosen.record("run-1", "R01", "human", "gate", "approved")
    assert chosen.errors(got) == [] and "cause" not in got


@pytest.mark.parametrize(
    "change",
    [
        {"rule": "07"},
        {"chooser": "ai"},
        {"cause": "no_such_kind"},
        {"run_id": ""},
    ],
)
def test_record_refuses_wrong_values(change):
    args = {"run_id": "r", "rule": "R07", "chooser": "script", "frm": "a", "to": "b", **change}
    with pytest.raises(ValueError):
        chosen.record(**args)


@pytest.mark.parametrize("change", [{"extra": 1}, {"at": "2026-12-01 01:00"}, {"event": "route"}])
def test_errors_refuse_wrong_records(change):
    got = chosen.record("r", "R07", "script", "a", "b")
    assert chosen.errors({**got, **change})
    assert chosen.errors({**got, "at": "2026-12-01T01:00:00.5+09:00"}) == []


def test_choosers_are_the_schema_enum():
    schema = contracts.load_json(chosen.SCHEMA)
    assert tuple(schema["properties"]["chooser"]["enum"]) == chosen.CHOOSERS


@pytest.mark.parametrize(
    ("suggested", "taken", "human", "want"),
    [
        (NEXT, ["ndf", "loop", "answer"], False, "script"),
        (NEXT, ["ndf", "loop", "answer", "--comment", "直した"], False, "script"),
        (NEXT, ["ndf", "loop", "answer", "--comment", "a", "--comment", "b"], False, "conductor"),
        (NEXT, ["ndf", "loop", "answer", "--other", "x"], False, "conductor"),
        (NEXT, ["ndf", "loop", "answer", "--comment"], False, "conductor"),
        (NEXT, ["ndf", "loop", "cancel"], False, "conductor"),
        (None, ["ndf", "loop", "answer"], False, "conductor"),
        (NEXT, ["ndf", "loop", "answer"], True, "human"),
    ],
)
def test_chooser(suggested, taken, human, want):
    assert chosen.chooser(suggested, taken, answered_by_human=human) == want
