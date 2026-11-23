"""上限の値の読む順・1 以上の整数だけを受ける決まり・マシンの設定の書き方（#3989 の決定 4・受け入れ条件 10）。"""

from __future__ import annotations

import json

import pytest

from ndf.contracts import named_cap
from ndf.contracts.named_cap import Cap, NamedCapError


def _write_raw(state, body):
    p = named_cap.path(state)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")


def test_machine_setting_comes_before_the_environment_and_the_limits(tmp_path):
    assert named_cap.read({}, tmp_path) == Cap(6, "limits")
    assert named_cap.read({"NDF_RELAY_MAX_NAMED": "2"}, tmp_path) == Cap(2, "env")
    named_cap.write(tmp_path, 4)
    assert json.loads(named_cap.path(tmp_path).read_text()) == {"version": 1, "max_named": 4}
    assert named_cap.read({"NDF_RELAY_MAX_NAMED": "2"}, tmp_path) == Cap(4, "machine")
    # 同じプロセスの次の読みが書いた値を返す（持ち越さない）
    named_cap.write(tmp_path, 1)
    assert named_cap.read({}, tmp_path) == Cap(1, "machine")
    named_cap.write(tmp_path, None)
    assert named_cap.read({"NDF_RELAY_MAX_NAMED": "2"}, tmp_path) == Cap(2, "env")


def test_machine_setting_wins_over_a_broken_environment_and_the_requested_fallback(tmp_path):
    named_cap.write(tmp_path, 6)
    assert named_cap.read({"NDF_RELAY_MAX_NAMED": "1.5"}, tmp_path) == Cap(6, "machine")
    assert named_cap.read({}, tmp_path, Cap(2, "env")) == Cap(6, "machine")
    assert named_cap.read({}, tmp_path, NamedCapError("x")) == Cap(6, "machine")


def test_requested_fallback_is_used_only_without_the_machine_setting(tmp_path):
    assert named_cap.read({"NDF_RELAY_MAX_NAMED": "9"}, tmp_path, Cap(2, "env")) == Cap(2, "env")
    with pytest.raises(NamedCapError, match="頼む側"):
        named_cap.read({}, tmp_path, NamedCapError("頼む側の誤り"))


def test_fallback_never_reads_the_machine_setting(tmp_path):
    _write_raw(tmp_path, "not json")
    assert named_cap.fallback({"NDF_RELAY_MAX_NAMED": "2"}) == Cap(2, "env")
    assert named_cap.fallback({}) == Cap(6, "limits")
    with pytest.raises(NamedCapError):
        named_cap.fallback({"NDF_RELAY_MAX_NAMED": "0.5"})


@pytest.mark.parametrize("raw", [1, "1", "8"])
def test_parse_accepts_positive_integers(raw):
    assert named_cap.parse(raw, "x") == int(raw)


@pytest.mark.parametrize("raw", [0, -1, True, 1.5, 6.0, "0", "0.5", "1.5", "1e1", "inf", "nan", "-1", "many", None])
def test_parse_refuses_anything_else_without_truncating(raw):
    with pytest.raises(NamedCapError):
        named_cap.parse(raw, "x")


@pytest.mark.parametrize("raw", ["0", "0.5", "1.5", "inf", "nan", "-1", "many"])
def test_a_broken_environment_value_is_refused(tmp_path, raw):
    with pytest.raises(NamedCapError, match="NDF_RELAY_MAX_NAMED"):
        named_cap.read({"NDF_RELAY_MAX_NAMED": raw}, tmp_path)


@pytest.mark.parametrize(
    "body", ["not json", {"version": 2, "max_named": 3}, {"version": 1, "max_named": 0}, {"version": 1, "max_named": 1.5}, []]
)
def test_a_broken_machine_setting_is_refused(tmp_path, body):
    _write_raw(tmp_path, body)
    with pytest.raises(NamedCapError, match="マシンの設定"):
        named_cap.read({}, tmp_path)


def test_a_broken_limits_value_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(named_cap.limits, "value", lambda *a: 0.5)
    with pytest.raises(NamedCapError, match="relay.max-named"):
        named_cap.read({}, tmp_path)


def test_write_refuses_a_value_below_one(tmp_path):
    with pytest.raises(NamedCapError):
        named_cap.write(tmp_path, 0)
    assert not named_cap.path(tmp_path).exists()
