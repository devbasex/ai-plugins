from __future__ import annotations

import pytest

from ndf import contracts
from ndf.contracts import limits


def test_policy_and_values_from_plugin_ndf_settings():
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
    ("rel", "change"),
    [
        ("_meta.json", lambda d: {**d, "version": 1}),
        ("_meta.json", lambda d: {k: v for k, v in d.items() if k != "owner"}),
        ("_meta.json", lambda d: {**d, "owner": ""}),
        ("a.b.json", lambda _: {"summary": "x"}),
        ("a.b.json", lambda _: {"summary": "x", "seconds": -1}),
        ("a.b.json", lambda _: {"summary": "x", "attempts": 1.5}),
        ("a.b.json", lambda _: {"summary": "x", "weird": 1}),
        ("nodot.json", lambda _: {"summary": "x", "seconds": 1}),
        ("a.b.json", lambda _: {"summary": " ", "seconds": 1}),
    ],
)
def test_wrong_shape_stops(contracts_copy, rel, change):
    contracts_copy(f"limits/{rel}", change)
    with pytest.raises(limits.LimitsError):
        limits.table()


def test_duplicate_keys_in_a_policy_stop(contracts_copy):
    contracts_copy("limits/a.b.json", text='{"summary": "x", "seconds": 1, "seconds": 2}')
    with pytest.raises(limits.LimitsError, match="a.b"):
        limits.table()


@pytest.mark.parametrize("pattern", ["limits/_meta.json", "limits/[!_]*.json"], ids=["no-meta", "no-policy"])
def test_missing_ndf_settings_stops(contracts_copy, pattern):
    contracts_copy.remove(pattern)
    with pytest.raises(limits.LimitsError):
        limits.table()


# 現状固定（I-006）: 値のキーは Policy の欄（name・summary を除く）と設定の形の値のキーの 3 か所が同じ集合を持つ。
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
    "gib",
    "lock",
    "gate",
    "first",
    "max_chars",
    "rest_seconds",
    "lock_seconds",
    "rounds",
)
# 数でない値（`hook.stop-wait.judge` の model・runtimes・auth_markers・endpoint）。`limits.value` では引かない
_SCHEMA_OTHER_KEYS = ("model", "runtimes", "auth_markers", "endpoint")


def test_value_keys_match_policy_fields_and_schema():
    schema = contracts.load_json(limits.SCHEMA)
    declared = schema["properties"]
    assert set(declared) - {"summary"} == set(_SCHEMA_VALUE_KEYS) | set(_SCHEMA_OTHER_KEYS)
    assert set(limits.Policy._fields) - {"name", "summary"} == set(_SCHEMA_VALUE_KEYS) | set(_SCHEMA_OTHER_KEYS)
    assert set(limits.VALUE_KEYS) == set(_SCHEMA_VALUE_KEYS)


def test_the_stop_wait_judge_row_is_read_from_the_shipped_settings():
    """プラグインの `contracts/limits/` に判定の方針があり、hook の表（`tables.policy_value`）が差し替えなしで読める（#4000）。"""
    from ndf.domains.hooks import reply_judge

    assert reply_judge.judge_setting("model") == "haiku"
    assert reply_judge.judge_setting("max_chars") == 1600
    assert reply_judge.judge_setting("seconds") <= limits.value("hook.budget.stop", "deadline") - limits.value("hook.budget.stop", "margin")
    assert limits.policy("hook.stop-wait.judge").endpoint["entrypoints"] == ["cli", "sdk-cli"]


@pytest.mark.parametrize("key", _SCHEMA_VALUE_KEYS)
def test_value_reads_every_value_key(contracts_copy, key):
    contracts_copy("limits/a.b.json", lambda _: {"summary": "x", key: 7})
    assert limits.value("a.b", key) == 7
    assert getattr(limits.policy("a.b"), key) == 7
    others = [k for k in _SCHEMA_VALUE_KEYS if k != key]
    assert all(getattr(limits.policy("a.b"), k) is None for k in others)


@pytest.mark.parametrize("key", ["name", "summary", "_fields", "count_", ""])
def test_value_rejects_non_value_keys(key):
    with pytest.raises(limits.LimitsError, match=f"cause.infra-wait に {key} が無い"):
        limits.value("cause.infra-wait", key)


def test_validate_builds_policies_with_name_and_absent_values_as_none():
    got = limits.validate({"a.b": {"summary": "x", "seconds": 1.5, "attempts": 2}})
    assert got == {"a.b": limits.Policy(name="a.b", summary="x", seconds=1.5, attempts=2)}
    assert got["a.b"].interval is None


def test_table_keeps_every_policy_and_representative_values():
    # #3687: 表を方針ごとのファイルへ分けても、読みの結果は分ける前と同じ件数と値を返す
    table = limits.table()
    assert len(table) == 230
    assert limits.value("test-run.test", "seconds") == 900
    assert limits.value("test-run.test", "factor") == 3
    assert limits.value("launch.weight.test", "gib") == 14
    assert table["test-run.test"].name == "test-run.test"
