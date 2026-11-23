"""文脈の切り替えの 2 つの値（判断開始の値・運用上限）を解く関数（#3995 の決定 1・5）。"""

from __future__ import annotations

import pytest

from ndf.contracts import limits
from ndf.domains.hooks import tables
from ndf.domains.relay import context_thresholds
from ndf.domains.relay.context_thresholds import thresholds


def _value(decide_from: int, limit: int):
    """上限の設定の 2 つの値を差し替えた引き方。ほかの方針の名前は呼ばれない。"""
    table = {context_thresholds.DECIDE_FROM: decide_from, context_thresholds.LIMIT: limit}

    def value(name: str, key: str) -> int:
        assert key == "count"
        return table[name]

    return value


@pytest.mark.parametrize("value", [limits.value, tables.policy_value], ids=["limits", "tables"])
def test_defaults_come_from_the_limits_table(value):
    # 環境変数が無ければ、上限の設定の 2 つの値（250,000・500,000）と出所 default（ラッパーと guard の引き方のどちらでも）
    got = thresholds({}, value)
    assert (got.decide_from, got.limit) == (250_000, 500_000)
    assert got.sources == {"decide_from": "default", "limit": "default"}
    assert got.as_dict() == {"decide_from": 250_000, "limit": 500_000, "sources": got.sources}


def test_values_follow_the_given_table():
    # 上限の設定の値をそのまま使う（コードに値を持たない）
    got = thresholds({"NDF_CONTEXT_LIMIT": ""}, _value(1_000, 2_000))
    assert (got.decide_from, got.limit, got.sources) == (1_000, 2_000, {"decide_from": "default", "limit": "default"})


@pytest.mark.parametrize(
    ("raw", "decide_from", "limit", "decide_source"),
    [
        ("300000", 250_000, 300_000, "default"),
        ("250000", 250_000, 250_000, "default"),
        ("249999", 249_999, 249_999, "env"),
    ],
)
def test_env_overrides_the_limit_and_lowers_the_decide_from(raw, decide_from, limit, decide_source):
    # NDF_CONTEXT_LIMIT は運用上限を替え、判断開始の値が上回れば同じ値へ下げる（出所も運用上限の出所へそろえる）
    got = thresholds({"NDF_CONTEXT_LIMIT": raw}, limits.value)
    assert (got.decide_from, got.limit) == (decide_from, limit)
    assert got.sources == {"decide_from": decide_source, "limit": "env"}


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "1.5", "²"])
def test_env_that_is_not_a_positive_integer_is_ignored(raw):
    # 正の整数でない値は無視して既定で続け、出所に ignored を書く（guard とラッパーを落とさない）
    got = thresholds({"NDF_CONTEXT_LIMIT": raw}, limits.value)
    assert (got.decide_from, got.limit) == (250_000, 500_000)
    assert got.sources == {"decide_from": "default", "limit": "default", "ignored": f"NDF_CONTEXT_LIMIT={raw}"}


def test_reversed_table_lowers_the_decide_from_to_the_limit():
    # 上限の設定の 2 つの値が逆なら、判断開始の値を運用上限へ下げる
    got = thresholds({}, _value(600_000, 500_000))
    assert (got.decide_from, got.limit, got.sources) == (500_000, 500_000, {"decide_from": "default", "limit": "default"})


def test_missing_table_row_is_raised_to_the_caller():
    # 上限の設定に行が無ければ、引き方の例外をそのまま呼び出し元へ返す
    def value(name: str, key: str) -> int:
        raise tables.TableError(f"上限の設定の方針 {name} に {key} が無い")

    with pytest.raises(tables.TableError):
        thresholds({}, value)
