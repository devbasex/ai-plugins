"""config.yaml の各節を pydantic のモデルで読む（#1142 の D8）。

移す前は ``bool(x)`` / ``int(x)`` / ``str(x)`` と ``raw["key"]`` で手で変換していた。次の入力で結果が変わる。
"""
from __future__ import annotations

import pytest

from playwright_kit.config import BodyCheckConfig, PlaywrightConfig, ReportConfig, _role_from_raw


def test_a_false_string_is_false():
    """移す前は ``bool("false")`` で真になった。"""
    assert PlaywrightConfig.from_raw({"headless": "false"}).headless is False


def test_a_value_that_is_not_a_boolean_is_an_error():
    """移す前は空でない値をすべて真にした。"""
    with pytest.raises(ValueError, match="headless"):
        PlaywrightConfig.from_raw({"headless": "maybe"})


def test_a_missing_login_field_is_a_value_error_naming_the_field():
    """移す前は ``KeyError('path')`` だった。"""
    with pytest.raises(ValueError, match="path"):
        _role_from_raw("admin", {"login": {"fields": {}, "fail_if_url_contains": "/login"}})


def test_null_items_fall_back_to_the_defaults():
    got = PlaywrightConfig.from_raw({"viewport": {"width": None}, "step_delay_ms": None})
    assert (got.viewport_width, got.step_delay_ms) == (1280, 1800)
    assert BodyCheckConfig.model_validate({"fatal_patterns": None}).fatal_patterns == BodyCheckConfig().fatal_patterns


def test_numbers_are_read_into_text_items():
    assert ReportConfig.model_validate({"title": 2026, "phase_labels": {"1": 3}}).phase_labels == {1: "3"}
