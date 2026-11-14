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
    assert len(table) == 31
    codes = exit_codes.codes("stopped")
    assert all(row.exit in codes and row.std_type for row in table.values())


def test_exit_code_value_lives_only_in_the_exit_codes_table():
    # 止まった理由の表は終了コードの表の id を指し、値は終了コードの表から引く（#3367）
    for row in causes.table().values():
        assert row.exit == exit_codes.code_of(row.exit_id)


@pytest.mark.parametrize(
    ("kind", "code"),
    [("base_conflict", 4), ("not_applicable", 3), ("precondition", 3), ("deps_missing", 69), ("infra_wait", 75), ("transient", 75)],
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
        _edit_cause("unknown", exit=1),
        _edit_cause("unknown", exit="no_such"),
        _edit_cause("unknown", exit="gate"),
        _edit_cause("unknown", route="later"),
        _edit_cause("ci_pending", retry="cause.no-such"),
        _edit_cause("ci_pending", retry=None),
        _edit_cause("ci_pending", exhausted=None),
        _edit_cause("ci_pending", exhausted="retry"),
        _edit_cause("unknown", retry="cause.ci-pending"),
        _edit_cause("unknown", exhausted="human"),
        lambda d: {**d, "causes": d["causes"] + [d["causes"][0]]},
        lambda d: {k: v for k, v in d.items() if k != "owner"},
        lambda d: {**d, "version": 1},
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


def test_exit_follows_the_exit_codes_table(contracts_copy):
    contracts_copy("causes.json", _edit_cause("unknown", exit="deps_missing"))
    assert causes.exit_code("unknown") == 69
    contracts_copy(
        "facts/exit-codes.json", lambda t: {**t, "rows": [{**r, "code": 7} if r["id"] == "deps_missing" else r for r in t["rows"]]}
    )
    assert causes.exit_code("unknown") == causes.exit_code("deps_missing") == 7


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
