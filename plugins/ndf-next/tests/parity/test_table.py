from __future__ import annotations

import copy

import pytest

from ndf.parity import table


@pytest.fixture
def tables():
    return copy.deepcopy(table.load("features")), copy.deepcopy(table.load("fixes"))


def _row(features, kind="entrypoint"):
    return next(r for r in features["rows"] if r["kind"] == kind and "to" in r)


def test_shipped_tables_have_the_shape(tables):
    assert table.shape_errors(*tables) == []


def test_every_row_has_feature_trial_and_either_to_or_not_built(tables):
    features, _ = tables
    for r in features["rows"]:
        assert r["feature"] and "trial" in r
        assert ("to" in r) != ("not_built" in r), r["id"]


def test_the_five_non_regression_rows_are_rows(tables):
    features, _ = tables
    assert len([r for r in features["rows"] if r["kind"] == "non-regression"]) == 5


@pytest.mark.parametrize("change", ["drop_to", "add_not_built", "empty_feature", "drop_trial"])
def test_a_row_without_one_of_the_four_keys_is_an_error(tables, change):
    features, fixes = tables
    r = _row(features)
    if change == "drop_to":
        del r["to"]
    elif change == "add_not_built":
        r["not_built"] = "理由"
    elif change == "empty_feature":
        r["feature"] = ""
    else:
        del r["trial"]
    assert table.shape_errors(features, fixes)


@pytest.mark.parametrize(
    ("trial", "ok"),
    [
        (None, True),
        ({"result": "passed"}, True),
        ({"result": "failed", "reason": "落ちた"}, True),
        ({"result": "not_run", "reason": "試走で通らない"}, True),
        ({"result": "failed"}, False),
        ({"result": "not_run", "reason": ""}, False),
        ({"result": "skipped"}, False),
        ("passed", False),
    ],
)
def test_trial_is_null_or_a_result_with_a_reason_unless_passed(tables, trial, ok):
    features, fixes = tables
    _row(features)["trial"] = trial
    assert (table.shape_errors(features, fixes) == []) is ok


def test_a_destination_outside_places_is_an_error(tables):
    features, fixes = tables
    _row(features)["to"] = "Z9"
    assert any("Z9" in e for e in table.shape_errors(features, fixes))


def test_duplicate_ids_are_an_error(tables):
    features, fixes = tables
    features["rows"].append(copy.deepcopy(features["rows"][0]))
    assert any("重なる" in e for e in table.shape_errors(features, fixes))


def test_an_accepted_bug_must_point_to_an_existing_row(tables):
    features, fixes = tables
    taken = next(i for i in fixes["intake"] if "to" in i)
    taken["row"] = "entrypoint:scripts/無い.py"
    assert any("対応表に無い" in e for e in table.shape_errors(features, fixes))


def test_a_fix_needs_either_a_destination_or_a_reason_not_to_move(tables):
    features, fixes = tables
    moved = next(f for f in fixes["fixes"] if "to" in f)
    moved["not_moved"] = "理由"
    assert table.shape_errors(features, fixes)


def test_entrypoints_give_the_place_after_the_move(tables):
    features, _ = tables
    every = table.entrypoints(features)
    assert {e.now for e in every} == {r["now"] for r in features["rows"] if r["kind"] == "entrypoint"}
    by_now = {e.now: e for e in every}
    moved = by_now["scripts/acceptance-check.py"]
    assert (moved.to, moved.after) == ("M1", ("src/ndf/domains/loop/",))
    not_built = by_now["scripts/bundle-close.py"]
    assert (not_built.to, not_built.after) == (None, ())


def test_conductor_entrypoints_are_the_marked_subset(tables):
    features, _ = tables
    only = table.entrypoints(features, conductor_only=True)
    assert only and all(e.conductor for e in only)
    assert {e.now for e in only} == {r["now"] for r in features["rows"] if r.get("conductor")}


def test_user_commands_resolve_through_the_subcommand_table(tables):
    features, _ = tables
    run_row = next(r for r in features["rows"] if r.get("user_command") and r["to"] == "C2")
    commands = {"plan": "ndf.core.plan.cli", "planner": "ndf.core.planner", "check": "ndf.checks.run"}
    found = table.user_commands(features, commands)
    assert found[run_row["now"]] == ["plan"]
    assert set(found) == {r["now"] for r in features["rows"] if r.get("user_command")}


def test_user_commands_are_empty_until_the_destination_adds_one(tables):
    features, _ = tables
    assert all(v == [] for v in table.user_commands(features, {"check": "ndf.checks.run"}).values())


def test_unknown_table_name_is_refused():
    with pytest.raises(ValueError):
        table.load("bugs")
