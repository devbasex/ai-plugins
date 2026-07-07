"""auth fixture の cache hit/miss 検証 + Amazon Q Critical-1/2 の回帰テスト。

_login_and_get_storage_state を monkeypatch で fake し、Playwright 実機不要で:
- 1 つ目の test では 1 回呼ばれる (cache miss)
- 2 つ目の test では呼ばれない (cache hit)
を確認する (Codex Major 5)。

Amazon Q Critical-1: fail_if_url_contains が空文字列の場合に全 login が失敗しない
Amazon Q Critical-2: context.close() 例外で browser.close() がスキップされない
の回帰テストも含む。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

from playwright_kit.fixtures.auth import (
    _StorageStateCache,
    _login_and_get_storage_state,
)
from playwright_kit.config import Login, Role


# ---------------------------------------------------------------------------
# _StorageStateCache の単体テスト
# ---------------------------------------------------------------------------


def test_storage_state_cache_miss_then_hit():
    """cache miss → put → hit の流れを検証。"""
    cache = _StorageStateCache.empty()
    assert cache.get("admin") is None  # miss

    state = {"cookies": [{"name": "session", "value": "abc"}], "origins": []}
    cache.put("admin", state)

    assert cache.get("admin") == state  # hit
    assert cache.get("user") is None   # 別 role は miss のまま


def test_storage_state_cache_multiple_roles():
    cache = _StorageStateCache.empty()
    cache.put("admin", {"cookies": [], "origins": []})
    cache.put("user", {"cookies": [{"name": "u"}], "origins": []})
    assert cache.get("admin") is not None
    assert cache.get("user") is not None
    assert cache.get("guest") is None


# ---------------------------------------------------------------------------
# Amazon Q Critical-1: fail_if_url_contains 空文字列の回帰テスト
# ---------------------------------------------------------------------------


def _make_role(fail_if_url_contains: str) -> Role:
    return Role(
        id="testuser",
        label="Test User",
        login=Login(
            path="/login",
            requires_basic_auth=False,
            fields={"username": "u", "password": "p"},
            fail_if_url_contains=fail_if_url_contains,
        ),
    )


def test_fail_if_url_contains_empty_string_does_not_fail(monkeypatch):
    """fail_if_url_contains が空文字列のとき login が失敗しないこと (Amazon Q Critical-1)。

    空文字列 "" はあらゆる URL に含まれるため、従来コードでは常に pytest.fail() していた。
    修正後は空文字列の場合はチェックをスキップする。
    """
    role = _make_role(fail_if_url_contains="")

    # Playwright の各オブジェクトを mock する
    fake_page = MagicMock()
    fake_page.url = "https://example.com/dashboard"

    fake_context = MagicMock()
    fake_context.new_page.return_value = fake_page
    fake_context.storage_state.return_value = {"cookies": [], "origins": []}

    fake_browser = MagicMock()
    fake_browser.new_context.return_value = fake_context

    fake_playwright = MagicMock()
    fake_playwright.chromium.launch.return_value = fake_browser

    # pytest.fail が呼ばれないことを確認
    result = _login_and_get_storage_state(
        playwright=fake_playwright,
        base_url="https://example.com",
        role=role,
        basic_auth_user="",
        basic_auth_password="",
        verify_tls=False,
    )
    assert result == {"cookies": [], "origins": []}


def test_fail_if_url_contains_non_empty_triggers_on_match(monkeypatch):
    """fail_if_url_contains が非空で URL にマッチするとき pytest.fail が呼ばれる。"""
    role = _make_role(fail_if_url_contains="/login")

    fake_page = MagicMock()
    fake_page.url = "https://example.com/login?error=1"  # login ページに留まっている

    fake_context = MagicMock()
    fake_context.new_page.return_value = fake_page

    fake_browser = MagicMock()
    fake_browser.new_context.return_value = fake_context

    fake_playwright = MagicMock()
    fake_playwright.chromium.launch.return_value = fake_browser

    with pytest.raises(pytest.fail.Exception):
        _login_and_get_storage_state(
            playwright=fake_playwright,
            base_url="https://example.com",
            role=role,
            basic_auth_user="",
            basic_auth_password="",
            verify_tls=False,
        )


# ---------------------------------------------------------------------------
# Amazon Q Critical-2: browser.close() リソースリーク回帰テスト
# ---------------------------------------------------------------------------


def test_browser_closed_even_when_context_close_raises(monkeypatch):
    """context.close() が例外を投げても browser.close() が必ず呼ばれること (Amazon Q Critical-2)。

    新実装では context.close() の例外は finally 内で握りつぶされるため、
    呼び出し元には伝播しない。storage_state は正常に返ること、
    かつ browser.close() が呼ばれることを検証する。
    """
    role = _make_role(fail_if_url_contains="")

    fake_page = MagicMock()
    fake_page.url = "https://example.com/dashboard"

    fake_context = MagicMock()
    fake_context.new_page.return_value = fake_page
    fake_context.storage_state.return_value = {"cookies": [], "origins": []}
    fake_context.close.side_effect = RuntimeError("context close failed")

    fake_browser = MagicMock()
    fake_browser.new_context.return_value = fake_context

    fake_playwright = MagicMock()
    fake_playwright.chromium.launch.return_value = fake_browser

    # context.close() が例外を投げても、新実装では握りつぶされて
    # storage_state が正常に返ること
    result = _login_and_get_storage_state(
        playwright=fake_playwright,
        base_url="https://example.com",
        role=role,
        basic_auth_user="",
        basic_auth_password="",
        verify_tls=False,
    )
    assert result == {"cookies": [], "origins": []}

    # browser.close() が呼ばれていること
    fake_browser.close.assert_called_once()


def test_browser_closed_when_goto_fails(monkeypatch):
    """page.goto() が失敗して pytest.fail() が呼ばれても browser.close() されること (AQ Critical-2 完遂)。

    goto / fill / expect_navigation / fail_if_url_contains の失敗は
    いずれも pytest.fail() を raise し、旧実装では末尾 finally に到達しなかった。
    新実装では関数全体を browser try/finally で囲んでいるため browser.close() が保証される。
    """
    role = _make_role(fail_if_url_contains="")

    fake_page = MagicMock()
    # goto() が例外を投げる
    fake_page.goto.side_effect = Exception("network error")

    fake_context = MagicMock()
    fake_context.new_page.return_value = fake_page

    fake_browser = MagicMock()
    fake_browser.new_context.return_value = fake_context

    fake_playwright = MagicMock()
    fake_playwright.chromium.launch.return_value = fake_browser

    # pytest.fail() が発生する (goto 失敗)
    with pytest.raises(pytest.fail.Exception):
        _login_and_get_storage_state(
            playwright=fake_playwright,
            base_url="https://example.com",
            role=role,
            basic_auth_user="",
            basic_auth_password="",
            verify_tls=False,
        )

    # browser.close() が呼ばれていること
    fake_browser.close.assert_called_once()


def test_browser_closed_when_fail_if_url_contains_triggers(monkeypatch):
    """fail_if_url_contains でログイン失敗判定しても browser.close() されること (AQ Critical-2 完遂)。"""
    role = _make_role(fail_if_url_contains="/login")

    fake_page = MagicMock()
    fake_page.url = "https://example.com/login?error=1"  # login ページに残留

    fake_context = MagicMock()
    fake_context.new_page.return_value = fake_page
    fake_context.storage_state.return_value = {"cookies": [], "origins": []}

    fake_browser = MagicMock()
    fake_browser.new_context.return_value = fake_context

    fake_playwright = MagicMock()
    fake_playwright.chromium.launch.return_value = fake_browser

    with pytest.raises(pytest.fail.Exception):
        _login_and_get_storage_state(
            playwright=fake_playwright,
            base_url="https://example.com",
            role=role,
            basic_auth_user="",
            basic_auth_password="",
            verify_tls=False,
        )

    # fail_if_url_contains でも browser.close() が呼ばれること
    fake_browser.close.assert_called_once()
