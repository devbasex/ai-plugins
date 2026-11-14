from __future__ import annotations

import pytest

from ndf import contracts
from ndf.contracts import limits


def test_policy_and_values_from_plugin_declaration():
    p = limits.policy("cause.infra-wait")
    assert (p.attempts, p.interval, p.factor, p.max_interval) == (3, 600, 2, 3600)
    assert limits.value("output.tail-lines", "count") == 200
    assert limits.seconds("owner.window") == 86400


@pytest.mark.parametrize(("name", "key"), [("no.such", "seconds"), ("cause.usage-limit", "interval"), ("cause.usage-limit", "nope")])
def test_missing_policy_or_value_stops(name, key):
    with pytest.raises(limits.LimitsError):
        limits.value(name, key)


def test_scaled_never_goes_below_the_floor():
    # #2983: 記録が 66.2 秒でも、実測 244〜371 秒の全体テストを打ち切らない
    floor = limits.seconds("test-run.whole")
    assert limits.scaled("test-run.whole", 66.2) == floor
    assert floor > 371
    assert limits.scaled("test-run.whole", None) == floor


def test_scaled_follows_long_measurements():
    factor = limits.value("test-run.whole", "factor")
    assert limits.scaled("test-run.whole", 1000) == factor * 1000


@pytest.mark.parametrize(
    "change",
    [
        lambda d: {**d, "version": 1},
        lambda d: {k: v for k, v in d.items() if k != "owner"},
        lambda d: {**d, "policies": {}},
        lambda d: {**d, "policies": {"a.b": {"summary": "x"}}},
        lambda d: {**d, "policies": {"a.b": {"summary": "x", "seconds": -1}}},
        lambda d: {**d, "policies": {"a.b": {"summary": "x", "attempts": 1.5}}},
        lambda d: {**d, "policies": {"a.b": {"summary": "x", "weird": 1}}},
        lambda d: {**d, "policies": {"nodot": {"summary": "x", "seconds": 1}}},
        lambda d: {**d, "policies": {"a.b": {"summary": " ", "seconds": 1}}},
    ],
)
def test_wrong_shape_stops(contracts_copy, change):
    contracts_copy("limits.json", change)
    with pytest.raises(limits.LimitsError):
        limits.table()


def test_duplicate_policy_names_stop(contracts_copy):
    contracts_copy(
        "limits.json", text='{"owner": "o", "policies": {"a.b": {"summary": "x", "seconds": 1}, "a.b": {"summary": "y", "seconds": 2}}}'
    )
    with pytest.raises(limits.LimitsError, match="a.b"):
        limits.table()


def test_missing_declaration_stops(contracts_copy):
    (contracts_copy.root / "limits.json").unlink()
    with pytest.raises(limits.LimitsError):
        limits.table()


# 現状固定（I-006）: 値の鍵は Policy の欄（name・summary を除く）と宣言の形の値の鍵の 3 か所が同じ集合を持つ。
_SCHEMA_VALUE_KEYS = (
    "seconds",
    "interval",
    "factor",
    "max_interval",
    "per_call",
    "attempts",
    "same_cause",
    "count",
    "p95_ms",
    "deadline",
    "margin",
    "timeout",
    "inject_chars",
)


def test_value_keys_match_policy_fields_and_schema():
    schema = contracts.load_json(limits.SCHEMA)
    declared = schema["properties"]["policies"]["additionalProperties"]["properties"]
    assert set(declared) - {"summary"} == set(_SCHEMA_VALUE_KEYS)
    assert set(limits.Policy._fields) - {"name", "summary"} == set(_SCHEMA_VALUE_KEYS)


@pytest.mark.parametrize("key", _SCHEMA_VALUE_KEYS)
def test_value_reads_every_value_key(contracts_copy, key):
    contracts_copy("limits.json", lambda d: {**d, "policies": {"a.b": {"summary": "x", key: 7}}})
    assert limits.value("a.b", key) == 7
    assert getattr(limits.policy("a.b"), key) == 7
    others = [k for k in _SCHEMA_VALUE_KEYS if k != key]
    assert all(getattr(limits.policy("a.b"), k) is None for k in others)


@pytest.mark.parametrize("key", ["name", "summary", "_fields", "count_", ""])
def test_value_rejects_non_value_keys(key):
    with pytest.raises(limits.LimitsError, match=f"cause.infra-wait に {key} が無い"):
        limits.value("cause.infra-wait", key)


def test_validate_builds_policies_with_name_and_absent_values_as_none():
    got = limits.validate({"owner": "o", "policies": {"a.b": {"summary": "x", "seconds": 1.5, "attempts": 2}}})
    assert got == {"a.b": limits.Policy(name="a.b", summary="x", seconds=1.5, attempts=2)}
    assert got["a.b"].interval is None
