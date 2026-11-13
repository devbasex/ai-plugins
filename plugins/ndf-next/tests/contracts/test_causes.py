from __future__ import annotations

import pytest

from ndf.contracts import causes, exit_codes, limits


def _edit_cause(kind: str, **changes):
    def fn(data):
        data["causes"] = [{**c, **changes} if c["kind"] == kind else c for c in data["causes"]]
        return data

    return fn


def test_plugin_table_rows_have_type_and_stopped_exit_code():
    table = causes.table()
    assert len(table) == 24
    codes = exit_codes.codes("stopped")
    assert all(row.exit in codes and row.std_type for row in table.values())


@pytest.mark.parametrize(
    ("kind", "code"), [("base_conflict", 4), ("not_applicable", 3), ("precondition", 3), ("deps_missing", 69), ("infra_wait", 75)]
)
def test_exit_code(kind, code):
    assert causes.exit_code(kind) == code


def test_unknown_kind_stops():
    with pytest.raises(causes.CausesError):
        causes.exit_code("no_such_kind")


def test_policy_of_reads_limits_only_for_retry_kinds():
    assert causes.policy_of("ci_pending") == limits.policy("cause.ci-pending")
    assert causes.policy_of("unknown") is None


@pytest.mark.parametrize(
    "fn",
    [
        _edit_cause("unknown", std_type="fatal"),
        _edit_cause("unknown", exit=7),
        _edit_cause("unknown", exit=10),
        _edit_cause("unknown", route="later"),
        _edit_cause("ci_pending", retry="cause.no-such"),
        _edit_cause("ci_pending", retry=None),
        _edit_cause("unknown", retry="cause.ci-pending"),
        lambda d: {**d, "causes": d["causes"] + [d["causes"][0]]},
        lambda d: {**d, "version": 2},
    ],
)
def test_wrong_table_stops(contracts_copy, fn):
    contracts_copy("causes.json", fn)
    with pytest.raises(causes.CausesError):
        causes.table()


def test_retry_policy_without_interval_needs_retry_after(contracts_copy):
    contracts_copy("causes.json", _edit_cause("usage_limit", requires_retry_after=False))
    with pytest.raises(causes.CausesError, match="interval"):
        causes.table()


def test_exit_code_range_ends_are_accepted(contracts_copy):
    contracts_copy("causes.json", _edit_cause("unknown", exit=69))
    assert causes.exit_code("unknown") == 69


def test_cause_builds_dict():
    assert causes.cause("test_failed", "pytest が落ちた") == {"kind": "test_failed", "detail": "pytest が落ちた"}
    full = causes.cause("usage_limit", "上限", instance="claude", retry_after=30)
    assert full == {"kind": "usage_limit", "detail": "上限", "instance": "claude", "retry_after": 30}


@pytest.mark.parametrize(("kind", "detail"), [("no_such", "x"), ("unknown", " "), ("unknown", "")])
def test_cause_refuses_unknown_kind_and_empty_detail(kind, detail):
    with pytest.raises(ValueError):
        causes.cause(kind, detail)


@pytest.mark.parametrize(
    ("value", "count"),
    [
        ({"kind": "unknown", "detail": "x"}, 0),
        ({"kind": "unknown", "detail": "x", "instance": "s"}, 0),
        ({"kind": "nope", "detail": "x"}, 1),
        ({"kind": "unknown", "detail": ""}, 1),
        ({"kind": "unknown", "detail": "x", "retry_after": -1}, 1),
        ({"kind": "unknown", "detail": "x", "retry_after": True}, 1),
        ({"kind": "unknown", "detail": "x", "extra": 1}, 1),
        ("unknown", 1),
    ],
)
def test_errors(value, count):
    assert len(causes.errors(value)) == count
