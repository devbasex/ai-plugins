"""playwright_kit/config.py の Maj-4 fail-fast 検証 + Maj-10 tolerated_console_errors。

`requires_basic_auth=True` なロール宣言のときに `target.basic_auth.user` が空ならば
Config.load 時点で ValueError を投げることを確認する。
"""

from __future__ import annotations

import pytest

from playwright_kit.config import Config, PlaywrightConfig, _expand_env


_BASE_RAW = {
    "target": {
        "base_url": "https://example.com",
    },
    "roles": {
        "user": {
            "label": "user",
            "login": {
                "path": "/login",
                "requires_basic_auth": False,
                "fields": {"username": "u", "password": "p"},
                "fail_if_url_contains": "/login",
            },
        },
    },
}


def _from_dict(raw: dict) -> Config:
    from pathlib import Path
    return Config._from_dict(raw, config_path=Path("/tmp/_test.yaml"))


def test_no_basic_auth_when_not_required_passes():
    cfg = _from_dict(_BASE_RAW)
    assert cfg.basic_auth.user == ""
    assert not cfg.roles["user"].login.requires_basic_auth


def test_requires_basic_auth_with_empty_user_raises():
    raw = {
        **_BASE_RAW,
        "roles": {
            "admin": {
                "label": "admin",
                "login": {
                    "path": "/admin/login",
                    "requires_basic_auth": True,
                    "fields": {"username": "u", "password": "p"},
                    "fail_if_url_contains": "/login",
                },
            },
        },
    }
    with pytest.raises(ValueError, match="requires_basic_auth"):
        _from_dict(raw)


def test_requires_basic_auth_with_user_passes():
    raw = {
        "target": {
            "base_url": "https://example.com",
            "basic_auth": {"user": "stage", "password": "secret"},
        },
        "roles": {
            "admin": {
                "label": "admin",
                "login": {
                    "path": "/admin/login",
                    "requires_basic_auth": True,
                    "fields": {"username": "u", "password": "p"},
                    "fail_if_url_contains": "/login",
                },
            },
        },
    }
    cfg = _from_dict(raw)
    assert cfg.basic_auth.user == "stage"


def test_tolerated_patterns_default_empty():
    cfg = _from_dict(_BASE_RAW)
    assert cfg.tolerated_console_errors == []
    assert cfg.tolerated_page_errors == []


def test_empty_yaml_raises_value_error(tmp_path):
    """空 YAML ファイルを Config.load() すると ValueError が出ること (Codex Minor 7)。"""
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="空または辞書ではありません"):
        Config.load(p)


# --- _expand_env 純関数テスト (Codex Major 4) ------------------------------


def test_expand_env_simple(monkeypatch):
    monkeypatch.setenv("MY_VAR", "hello")
    assert _expand_env("${MY_VAR}") == "hello"


def test_expand_env_default_when_unset(monkeypatch):
    monkeypatch.delenv("UNSET_VAR", raising=False)
    assert _expand_env("${UNSET_VAR:-fallback}") == "fallback"


def test_expand_env_undefined_no_default_raises(monkeypatch):
    monkeypatch.delenv("UNDEFINED_VAR", raising=False)
    with pytest.raises(ValueError, match="UNDEFINED_VAR"):
        _expand_env("${UNDEFINED_VAR}")


def test_expand_env_recursive(monkeypatch):
    monkeypatch.setenv("DB_HOST", "localhost")
    monkeypatch.setenv("DB_PASS", "secret")
    raw = {"host": "${DB_HOST}", "nested": [{"pass": "${DB_PASS}"}]}
    result = _expand_env(raw)
    assert result == {"host": "localhost", "nested": [{"pass": "secret"}]}


def test_tolerated_patterns_loaded():
    raw = {
        **_BASE_RAW,
        "tolerated_console_errors": [r"favicon\.ico", "ResizeObserver loop limit"],
        "tolerated_page_errors": ["ChunkLoadError"],
    }
    cfg = _from_dict(raw)
    assert cfg.tolerated_console_errors == [
        r"favicon\.ico",
        "ResizeObserver loop limit",
    ]
    assert cfg.tolerated_page_errors == ["ChunkLoadError"]


# --- playwright.har_mode (Issue #62) ---------------------------------


def test_har_mode_default_is_minimal():
    """config を書かない場合 default は minimal (Issue #62 race 回避)。"""
    cfg = PlaywrightConfig.from_raw({})
    assert cfg.har_mode == "minimal"


@pytest.mark.parametrize("value", ["minimal", "full", "none"])
def test_har_mode_accepts_valid_values(value: str):
    cfg = PlaywrightConfig.from_raw({"har_mode": value})
    assert cfg.har_mode == value


@pytest.mark.parametrize("raw,expected", [("FULL", "full"), ("Minimal", "minimal"), ("NONE", "none")])
def test_har_mode_lowercased(raw: str, expected: str):
    """大文字混じりでも lowercase 化される。"""
    cfg = PlaywrightConfig.from_raw({"har_mode": raw})
    assert cfg.har_mode == expected


def test_har_mode_invalid_raises():
    with pytest.raises(ValueError, match="har_mode"):
        PlaywrightConfig.from_raw({"har_mode": "invalid"})
