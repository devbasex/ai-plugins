"""認証 fixture: ``pwk_config`` と動的に生成する ``pwk_role_<id>``。

pytest-playwright が提供する ``page`` / ``context`` / ``browser_context_args``
fixture と協調して動作する。

設計方針:
- ``pwk_config`` は session scope。``--pwk-config`` で指定された YAML を
  1 度だけ読み込む。利用者プロジェクトの ``conftest.py`` から override 可能。
- 各 role に対し ``pwk_role_<id>`` fixture を *動的* に生成する。
  実体は ``_login_and_get_storage_state`` で session 内 1 回だけ login し、
  storage_state を session-scoped cache (`_StorageStateCache`) に保管。
  以降の test では同じ role の cache を ``context.add_cookies`` 等で再利用する
  ことで login の再実行を避ける。
- function scope で ``page.context.storage_state(...)`` を inject し、
  ``page`` は既に該当 role でログイン済みの状態で test 関数に渡される。

fail_if_url_contains による失敗判定もここで行い、test 開始前に明示的に
``pytest.fail`` する。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import pytest

from playwright_kit.config import Config, Login, Role


def _same_origin(origin_url: str, base_url: str) -> bool:
    """``origin_url`` が ``base_url`` と同一 origin (scheme + host + port) かを返す。

    storage_state には認証対象以外のサードパーティ origin (広告 / 計測タグ等) が
    含まれることがある。restore 時にそれらへ ``page.goto(url)`` するのは意図しない
    外部送信になるため、本関数で base_url の origin に厳格一致するもののみ許可する。
    """
    try:
        a = urlsplit(origin_url)
        b = urlsplit(base_url)
    except ValueError:
        return False
    return (
        bool(a.scheme)
        and bool(a.hostname)
        and a.scheme == b.scheme
        and a.hostname == b.hostname
        and (a.port or None) == (b.port or None)
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass
class _StorageStateCache:
    """session 内で role ごとの storage_state を 1 回だけ作る簡易 cache。"""

    states: dict[str, dict[str, Any]]

    @classmethod
    def empty(cls) -> "_StorageStateCache":
        return cls(states={})

    def get(self, role_id: str) -> dict[str, Any] | None:
        return self.states.get(role_id)

    def put(self, role_id: str, state: dict[str, Any]) -> None:
        self.states[role_id] = state


def _submit_login_form(page, login: Login) -> None:
    """ログインフォームの submit を行う。

    優先順位は ``login.submit_selectors`` → role/type=submit → password Enter。
    """
    for sel in login.submit_selectors:
        try:
            page.locator(sel).first.click(timeout=2000)
            return
        except Exception:
            continue
    for fallback in (
        "role=button[name=/login|sign.?in|ログイン/i]",
        'button[type="submit"]',
        'input[type="submit"]',
    ):
        try:
            page.locator(fallback).first.click(timeout=2000)
            return
        except Exception:
            continue
    pw_field = next(
        (n for n in login.fields if "pass" in n.lower() or "pwd" in n.lower()),
        None,
    )
    if pw_field:
        page.locator(f'input[name="{pw_field}"]').press("Enter")
        return
    raise RuntimeError(
        "ログイン送信ボタンが見つかりません (submit_selectors を設定してください)"
    )


def _login_and_get_storage_state(
    *,
    playwright,
    base_url: str,
    role: Role,
    basic_auth_user: str,
    basic_auth_password: str,
    verify_tls: bool,
    nav_timeout_ms: int = 30_000,
) -> dict[str, Any]:
    """role の login flow を実行し storage_state を返す。

    1 度だけ呼ばれることを想定。失敗時は ``pytest.fail`` を投げる。

    AQ Critical-2 完遂: 関数全体を browser try/finally で囲み、
    page.goto() / fill() / expect_navigation() / fail_if_url_contains で
    pytest.fail() が発生した場合も含め、全ての failure path で
    browser.close() が必ず呼ばれることを保証する。
    pytest.fail() は内部的に例外を raise するため finally は確実に動く。
    """
    browser = playwright.chromium.launch(headless=True)
    try:
        ctx_kwargs: dict[str, Any] = {
            "ignore_https_errors": not verify_tls,
        }
        if role.login.requires_basic_auth:
            ctx_kwargs["http_credentials"] = {
                "username": basic_auth_user,
                "password": basic_auth_password,
            }
        context = browser.new_context(**ctx_kwargs)
        context.set_default_navigation_timeout(nav_timeout_ms)
        context.set_default_timeout(nav_timeout_ms)

        try:
            page = context.new_page()
            url = f"{base_url}{role.login.path}"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
            except Exception as exc:  # pragma: no cover - depends on remote target
                pytest.fail(
                    f"[pwk_role_{role.id}] login page open failed: {url} ({exc})"
                )

            for name, value in role.login.fields.items():
                try:
                    page.locator(f'input[name="{name}"]').fill(
                        value, timeout=nav_timeout_ms
                    )
                except Exception as exc:  # pragma: no cover
                    pytest.fail(
                        f"[pwk_role_{role.id}] fill {name!r} failed: {exc}"
                    )

            try:
                with page.expect_navigation(
                    wait_until="domcontentloaded", timeout=nav_timeout_ms
                ):
                    _submit_login_form(page, role.login)
            except Exception as exc:  # pragma: no cover
                pytest.fail(
                    f"[pwk_role_{role.id}] navigation 失敗: "
                    f"{type(exc).__name__}: {exc}"
                )

            final_url = page.url
            # Amazon Q Critical-1: fail_if_url_contains が空文字列の場合、空文字列は
            # あらゆる文字列に含まれるため常に True になり全 login が失敗する。
            # 空文字列 (= 未設定) の場合はチェックをスキップする。
            if role.login.fail_if_url_contains and role.login.fail_if_url_contains in final_url:
                pytest.fail(
                    f"[pwk_role_{role.id}] login 失敗: "
                    f"final_url={final_url} に '{role.login.fail_if_url_contains}' を含む"
                )

            state = context.storage_state()
            return state
        finally:
            try:
                context.close()
            except Exception:
                pass
    finally:
        try:
            browser.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pwk_config(pytestconfig) -> Config:
    """``--pwk-config`` で指定された YAML をロードして ``Config`` を返す。

    指定が無い場合は CWD 直下の ``scenario.config.yaml`` を試し、
    それも無ければ ``pytest.skip`` する (NDF 機能が要らない test と共存可能にする)。
    """
    raw_path: str | None = pytestconfig.getoption("pwk_config", default=None)
    if not raw_path:
        env = os.environ.get("PWK_CONFIG")
        if env:
            raw_path = env
    if not raw_path:
        candidate = Path.cwd() / "scenario.config.yaml"
        if candidate.exists():
            raw_path = str(candidate)
    if not raw_path:
        pytest.skip(
            "pwk_config 未指定: --pwk-config <path> もしくは PWK_CONFIG env、"
            "または ./scenario.config.yaml を用意してください。"
        )

    path = Path(raw_path).resolve()
    return Config.load(path)


@pytest.fixture(scope="session")
def _pwk_storage_state_cache() -> _StorageStateCache:
    return _StorageStateCache.empty()


def _make_role_fixture(role_id: str) -> Callable:
    """role_id ごとに ``pwk_role_<id>`` fixture の実装関数を生成する。"""

    def _fixture(
        pwk_config: Config,
        playwright,
        context,
        _pwk_storage_state_cache: _StorageStateCache,
    ) -> Role:
        """login 済の storage_state を ``context`` に注入し、Role を返す。

        - ``playwright`` / ``context`` は ``pytest-playwright`` 提供
        - 既に同 role の storage_state が cache 済なら login をスキップ
        """
        role = pwk_config.role(role_id)

        state = _pwk_storage_state_cache.get(role_id)
        if state is None:
            state = _login_and_get_storage_state(
                playwright=playwright,
                base_url=pwk_config.base_url,
                role=role,
                basic_auth_user=pwk_config.basic_auth.user,
                basic_auth_password=pwk_config.basic_auth.password,
                verify_tls=pwk_config.verify_tls,
                nav_timeout_ms=pwk_config.playwright.navigation_timeout_ms,
            )
            _pwk_storage_state_cache.put(role_id, state)

        # cookies / origins (localStorage 等) を新しい context に注入する。
        cookies = state.get("cookies") or []
        if cookies:
            context.add_cookies(cookies)
        # storage_state に含まれる広告/計測タグ等の third-party origin に対して
        # 不用意に goto するのを避けるため、base_url と同一 origin のみ復元する。
        for origin in state.get("origins") or []:
            url = origin.get("origin")
            items = origin.get("localStorage") or []
            if not url or not items:
                continue
            if not _same_origin(url, pwk_config.base_url):
                continue
            try:
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded")
                for it in items:
                    page.evaluate(
                        "([k, v]) => window.localStorage.setItem(k, v)",
                        [it.get("name"), it.get("value")],
                    )
                page.close()
            except Exception:
                # localStorage 注入失敗は致命的ではない (cookie ベースの認証なら OK)。
                pass

        return role

    _fixture.__name__ = f"pwk_role_{role_id}"
    _fixture.__doc__ = (
        f"role={role_id!r} で login 済の storage_state を context に注入する。"
    )
    return _fixture


def register_role_fixtures(plugin_module, config: Config) -> list[str]:
    """plugin module に ``pwk_role_<id>`` fixture を動的登録する。

    ``pytest_configure`` から呼ばれる。pytest は modules の attribute を
    fixture として discover するため、setattr で十分。

    Returns:
        登録した fixture 名のリスト。
    """
    registered: list[str] = []
    for role_id in config.roles:
        name = f"pwk_role_{role_id}"
        if hasattr(plugin_module, name):
            continue
        impl = _make_role_fixture(role_id)
        # function scope (default) で wrap してから plugin module に attach
        wrapped = pytest.fixture(name=name)(impl)
        setattr(plugin_module, name, wrapped)
        registered.append(name)
    return registered
