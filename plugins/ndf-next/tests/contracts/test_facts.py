from __future__ import annotations

import pytest

from ndf.contracts import facts, limits

COLUMNS = {
    "flag": {"type": "boolean", "summary": "真偽"},
    "size": {"type": "integer", "summary": "数"},
    "tags": {"type": "array", "summary": "文字列の並び", "optional": True},
    "policy": {"type": "string", "summary": "方針", "optional": True, "ref": "limits.json#monitor.phase."},
    "code": {"type": "string", "summary": "区分", "optional": True, "ref": "facts/exit-codes"},
}
ROWS = [
    {"id": "a", "flag": True, "size": 1, "tags": ["x", "y"], "policy": "review", "code": "ok"},
    {"id": "b", "flag": False, "size": 2},
    {"id": "c", "flag": True, "size": 2, "tags": []},
]


@pytest.fixture
def sample_table(fact_table):
    return fact_table(COLUMNS, ROWS, summary="試しの表", owner="テスト")


@pytest.fixture
def sample(contracts_copy, sample_table):
    contracts_copy("facts/sample.json", lambda _: sample_table)
    return contracts_copy


def test_plugin_table_reads_and_ids_follow_table_order():
    assert facts.ids("exit-codes", status="gate") == ("gate", "pause")
    assert "exit-codes" in facts.names()


def test_table_is_read_once_per_process(sample):
    assert facts.table("sample") is facts.table("sample")


def test_rows_filters_by_columns_and_skips_rows_without_optional_column(sample):
    assert facts.ids("sample") == ("a", "b", "c")
    assert facts.ids("sample", flag=True, size=2) == ("c",)
    assert facts.ids("sample", tags=[]) == ("c",)


def test_column_and_row(sample):
    assert facts.column("sample", "tags") == {"a": ["x", "y"], "c": []}
    assert facts.row("sample", "b")["size"] == 2


@pytest.mark.parametrize(
    "call",
    [
        lambda: facts.row("sample", "zz"),
        lambda: facts.rows("sample", nope=1),
        lambda: facts.column("sample", "nope"),
        lambda: facts.table("Bad_Name"),
        lambda: facts.table("missing"),
    ],
)
def test_lookup_errors_stop(sample, call):
    with pytest.raises(facts.FactsError):
        call()


@pytest.mark.parametrize(
    "change",
    [
        lambda t: {**t, "version": 2},
        lambda t: {**t, "extra": 1},
        lambda t: {**t, "rows": [{"id": "a", "flag": "yes", "size": 1}]},
        lambda t: {**t, "rows": [{"id": "a", "flag": True}]},
        lambda t: {**t, "rows": [{"id": "a", "flag": True, "size": 1, "other": 1}]},
        lambda t: {**t, "rows": [{"id": "a", "flag": True, "size": 1}, {"id": "a", "flag": False, "size": 2}]},
        lambda t: {**t, "columns": {"Bad": {"type": "string", "summary": "x"}}},
    ],
)
def test_wrong_shape_stops(contracts_copy, sample_table, change):
    contracts_copy("facts/sample.json", lambda _: change(sample_table))
    with pytest.raises(facts.FactsError):
        facts.table("sample")


@pytest.mark.parametrize("text", ["{", '{"version": 1, "version": 1}'])
def test_broken_json_stops(contracts_copy, text):
    contracts_copy("facts/sample.json", text=text)
    with pytest.raises(facts.FactsError):
        facts.table("sample")


def test_limits_ref_names_the_limits_declaration():
    assert facts.LIMITS_REF == f"{limits.DECLARATION}#"


def test_ref_errors_point_at_missing_targets(contracts_copy, sample_table):
    bad = {**sample_table, "rows": [{"id": "a", "flag": True, "size": 1, "policy": "nope", "code": "zz"}]}
    contracts_copy("facts/sample.json", lambda _: bad)
    errs = facts.ref_errors("sample", {"monitor.phase.review"})
    assert len(errs) == 2
    assert any("monitor.phase.nope" in e for e in errs)
    assert any("exit-codes" in e for e in errs)


def test_ref_errors_skip_limits_refs_when_limits_unreadable(contracts_copy, sample_table):
    bad = {**sample_table, "rows": [{"id": "a", "flag": True, "size": 1, "policy": "nope", "code": "zz"}]}
    contracts_copy("facts/sample.json", lambda _: bad)
    errs = facts.ref_errors("sample", None)
    assert len(errs) == 1 and "exit-codes" in errs[0]


def test_ref_errors_pass_when_targets_exist(sample):
    assert facts.ref_errors("sample", {"monitor.phase.review"}) == []


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["a=true", "b=false"], {"a": True, "b": False}),
        (["n=12", "m=-3"], {"n": 12, "m": -3}),
        (["s=1.5", "e=", "k=x=y"], {"s": "1.5", "e": "", "k": "x=y"}),
    ],
)
def test_parse_where(args, expected):
    assert facts.parse_where(args) == expected


def test_parse_where_needs_equals():
    with pytest.raises(facts.FactsError):
        facts.parse_where(["flag"])


def test_domain_tables_are_read_by_name(contracts_copy, fact_table):
    contracts_copy("domains/sample.json", lambda _: fact_table({}, [{"id": "x"}]))
    assert "sample" in facts.names() and "runtimes" in facts.names()
    assert facts.place("sample") == "domains/sample.json"
    assert facts.ids("sample") == ("x",)


def test_same_name_in_both_places_is_refused(contracts_copy, fact_table):
    contracts_copy("domains/exit-codes.json", lambda _: fact_table({}, [{"id": "x"}]))
    with pytest.raises(facts.FactsError, match="2 つの置き場"):
        facts.table("exit-codes")


def test_table_ref_must_name_the_tables_place(contracts_copy, fact_table):
    columns = {
        "seat": {"type": "string", "summary": "s", "ref": "facts/runtimes"},
        "next": {"type": "string", "summary": "n", "ref": "domains/runtimes"},
    }
    contracts_copy("domains/sample.json", lambda _: fact_table(columns, [{"id": "a", "seat": "claude", "next": "nobody"}]))
    errs = facts.ref_errors("sample", set())
    assert len(errs) == 2
    assert any("domains/runtimes.json にある" in e for e in errs)
    assert any("'nobody' が表 runtimes に無い" in e for e in errs)
