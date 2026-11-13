from __future__ import annotations

import pytest

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
        lambda d: {**d, "version": 2},
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
        "limits.json", text='{"version": 1, "policies": {"a.b": {"summary": "x", "seconds": 1}, "a.b": {"summary": "y", "seconds": 2}}}'
    )
    with pytest.raises(limits.LimitsError, match="a.b"):
        limits.table()


def test_missing_declaration_stops(contracts_copy):
    (contracts_copy.root / "limits.json").unlink()
    with pytest.raises(limits.LimitsError):
        limits.table()
