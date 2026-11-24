"""文脈の切り替えの 2 つの値（判断開始の値・運用上限）を解く関数（#3995 の決定 1・5）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from ndf.contracts import limits
from ndf.domains.hooks import tables
from ndf.domains.relay import cli, context_thresholds
from ndf.domains.relay.context_thresholds import thresholds

PLUGIN_ROOT = Path(__file__).resolve().parents[3]


def _value(decide_from: int, limit: int):
    """上限の設定の 2 つの値を差し替えた引き方。ほかの方針の名前は呼ばれない。"""
    table = {context_thresholds.DECIDE_FROM: decide_from, context_thresholds.LIMIT: limit}

    def value(name: str, key: str) -> int:
        assert key == "count"
        return table[name]

    return value


@pytest.mark.parametrize("value", [limits.value, tables.policy_value], ids=["limits", "tables"])
def test_defaults_come_from_the_limits_table(value):
    # 環境変数が無ければ、上限の設定の 2 つの値（450,000・600,000）と出所 default（ラッパーと guard の引き方のどちらでも）
    got = thresholds({}, value)
    assert (got.decide_from, got.limit) == (450_000, 600_000)
    assert got.sources == {"decide_from": "default", "limit": "default"}
    assert got.as_dict() == {"decide_from": 450_000, "limit": 600_000, "sources": got.sources}


def test_values_follow_the_given_table():
    # 上限の設定の値をそのまま使う（コードに値を持たない）
    got = thresholds({"NDF_CONTEXT_LIMIT": ""}, _value(1_000, 2_000))
    assert (got.decide_from, got.limit, got.sources) == (1_000, 2_000, {"decide_from": "default", "limit": "default"})


@pytest.mark.parametrize(
    ("raw", "decide_from", "limit", "decide_source"),
    [
        ("500000", 450_000, 500_000, "default"),
        ("450000", 450_000, 450_000, "default"),
        ("449999", 449_999, 449_999, "env"),
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
    assert (got.decide_from, got.limit) == (450_000, 600_000)
    assert got.sources == {"decide_from": "default", "limit": "default", "ignored": "NDF_CONTEXT_LIMIT"}


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


# --- ndf relay context resolve（#3995 の Task 4。3995:1・3995:3・3995:9 の resolve の側） ---


def _resolve(monkeypatch, capsys, env: str | None) -> dict:
    """`ndf relay context resolve` の結果 JSON。`env` は NDF_CONTEXT_LIMIT（None なら無し）。形は conftest が結果のスキーマで確かめる。"""
    if env is None:
        monkeypatch.delenv("NDF_CONTEXT_LIMIT", raising=False)
    else:
        monkeypatch.setenv("NDF_CONTEXT_LIMIT", env)
    assert cli.main(["context", "resolve"]) == 0
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_resolve_returns_the_two_defaults(monkeypatch, capsys):
    # 設定も環境変数も無ければ、判断開始の値 450,000・運用上限 600,000・出所 default
    got = _resolve(monkeypatch, capsys, None)
    assert got["items"] == [{"decide_from": 450_000, "limit": 600_000, "sources": {"decide_from": "default", "limit": "default"}}]
    assert got["metrics"] == {"decide_from": 450_000, "limit": 600_000}


@pytest.mark.parametrize(("raw", "decide_from", "limit"), [("500000", 450_000, 500_000), ("200000", 200_000, 200_000)])
def test_resolve_reads_the_env_limit(monkeypatch, capsys, raw, decide_from, limit):
    # NDF_CONTEXT_LIMIT は resolve の運用上限も替え、判断開始の値を同じ値まで下げる
    got = _resolve(monkeypatch, capsys, raw)
    assert (got["items"][0]["decide_from"], got["items"][0]["limit"]) == (decide_from, limit)
    assert got["items"][0]["sources"]["limit"] == "env"
    assert got["metrics"] == {"decide_from": decide_from, "limit": limit}


def test_resolve_follows_the_limits_table(monkeypatch, capsys):
    # 上限の設定の 2 つの値を差し替えると、resolve は差し替えた値を返す（定数や別の行を読まない）
    # CLI は検証を通さない軽量の読み手（`hooks.tables.policy_value`）で引く（PR 4220 の cross-review r1-2）
    real = tables.policy_value
    swapped = {context_thresholds.DECIDE_FROM: 100_000, context_thresholds.LIMIT: 200_000}
    monkeypatch.setattr(tables, "policy_value", lambda name, key: swapped[name] if name in swapped else real(name, key))
    got = _resolve(monkeypatch, capsys, None)
    assert got["metrics"] == {"decide_from": 100_000, "limit": 200_000}


# --- 方針と設定の形を消した（#3995 の Task 5。3995:2・3995:10 のコントラクトの側） ---


@pytest.mark.parametrize("name", ["hook.context-limit", "relay.context"])
def test_the_limits_table_has_no_old_rows(name):
    with pytest.raises(limits.LimitsError):
        limits.value(name, "count")


def test_the_restart_policy_module_is_gone():
    assert importlib.util.find_spec("ndf.domains.relay.context_policy") is None


def test_the_restart_policy_setting_schema_is_gone():
    assert not (PLUGIN_ROOT / "contracts" / "schemas" / "ndf-settings" / "context.schema.json").exists()
