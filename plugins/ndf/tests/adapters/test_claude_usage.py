"""使用率の API とトークンの更新のエンドポイント（`adapters/claude_usage.py`）と、`http.request_json` のテスト。

設計文書のテスト設計の「決定 4（代わりの経路と形の変化）」のエンドポイントの側（500・通信の失敗・`five_hour` の形の違いを
それぞれ `http-500`・`network`・`shape` で返し、窓を返さない）と、詳細設計書の「単体テストの観点」の
`claude_usage` の行を縛る。偽のエンドポイントは `httpx.MockTransport` で、実のネットワークとアカウントを使わない。
"""

from __future__ import annotations

import functools
import json
import shutil
import socket
import ssl
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from ndf.adapters import claude_usage, http

from . import _drip_server

NOW = 1_800_000_000.0
TOKEN = "fake-access-value"
REFRESH = "fake-refresh-value"
RESET_5 = "2027-01-15T10:00:00Z"
RESET_7 = "2027-01-20T00:00:00+00:00"
MARGIN = 0.5  # 期限を越えたかの判定に足す小さな余白（秒）
_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


@pytest.fixture(autouse=True)
def _no_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """手元の環境のプロキシの変数を外し、テストが決めた経路だけを通す。"""
    for name in _PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _respond(status: int, body: Any = None, calls: list[httpx.Request] | None = None, **kw: Any) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if body is None:
            return httpx.Response(status, **kw)
        return httpx.Response(status, json=body, **kw)

    return httpx.MockTransport(handler)


def _body(**over: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "five_hour": {"utilization": 42.0, "resets_at": RESET_5},
        "seven_day": {"utilization": 70, "resets_at": RESET_7},
        "limits": [
            {"kind": "weekly_scoped", "percent": 95, "resets_at": RESET_7, "scope": {"model": {"id": "opus", "display_name": "Opus"}}},
            {"kind": "weekly_scoped", "percent": 10, "resets_at": RESET_5, "scope": {"model": {"display_name": "Sonnet"}}},
            {"kind": "other", "percent": "x"},
        ],
        "extra_usage": {"spend_limit_reached": True},
    }
    d.update(over)
    return d


def _get(body: Any, status: int = 200, calls: list[httpx.Request] | None = None) -> tuple[int, claude_usage.Usage]:
    return claude_usage.get_usage(TOKEN, NOW, 5, transport=_respond(status, body, calls))


# ---- get_usage・parse_usage ----


def test_get_usage_reads_windows_scoped_and_spend() -> None:
    """正常: 2 つの窓・`weekly_scoped` のモデル別の窓・`spend_limit_reached` が読め、要求に `Authorization` と `anthropic-beta` がある。"""
    calls: list[httpx.Request] = []
    status, u = _get(_body(), calls=calls)
    assert status == 200
    assert u.error is None
    assert u.five_hour == (42.0, _ts(RESET_5))
    assert u.seven_day == (70.0, _ts(RESET_7))
    assert u.scoped == [("opus", 95.0, _ts(RESET_7)), ("Sonnet", 10.0, _ts(RESET_5))]
    assert max(p for _, p, _ in u.scoped) == 95.0
    assert u.spend_limit_reached is True
    assert u.fetched_at == NOW
    (req,) = calls
    assert req.method == "GET"
    assert str(req.url) == claude_usage.USAGE_URL
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert req.headers["anthropic-beta"] == "oauth-2025-04-20"


@pytest.mark.parametrize(
    ("status", "transport", "error"),
    [
        (500, _respond(500, {"error": "x"}), "http-500"),
        (0, httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down", request=r))), "network"),
    ],
)
def test_get_usage_failure_returns_no_windows(status: int, transport: httpx.BaseTransport, error: str) -> None:
    """決定 4（代わりの経路と形の変化）: 500 と通信の失敗は窓を返さず、理由の符号だけを返す。"""
    got_status, u = claude_usage.get_usage(TOKEN, NOW, 5, transport=transport)
    assert (got_status, u.error) == (status, error)
    assert u.five_hour is None and u.seven_day is None
    assert TOKEN not in repr(u)


@pytest.mark.parametrize(
    ("body", "missing"),
    [
        (_body(five_hour="42%"), ("five_hour",)),
        (_body(five_hour={"utilization": 42, "resets_at": "not-a-time"}), ("five_hour",)),
        (_body(five_hour={"utilization": 42}), ("five_hour",)),
        (_body(five_hour={"utilization": 5, "resets_at": None}), ("five_hour",)),
        (_body(five_hour={"utilization": 0}), ("five_hour",)),
        (_body(five_hour={"utilization": 10**400, "resets_at": RESET_5}), ("five_hour",)),
        (_body(seven_day={"utilization": True, "resets_at": RESET_7}), ("seven_day",)),
        ([1, 2], ("body",)),
        ({"limits": []}, ("windows",)),
    ],
)
def test_parse_usage_shape_names_missing(body: Any, missing: tuple[str, ...]) -> None:
    """決定 4（代わりの経路と形の変化）: `five_hour` の形が違う応答は `shape` で、読めなかった項目の名前を残し、窓を返さない。"""
    status, u = _get(body)
    assert status == 200
    assert u.error == "shape"
    assert u.missing == missing
    assert u.five_hour is None and u.seven_day is None


def test_parse_usage_idle_window_is_not_shape() -> None:
    """境界値: `utilization` が 0 で `resets_at` が null の窓は使用の無い窓で、`shape` にしない。"""
    u = claude_usage.parse_usage(_body(five_hour={"utilization": 0, "resets_at": None}), NOW)
    assert u.error is None
    assert u.five_hour is None
    assert u.seven_day == (70.0, _ts(RESET_7))


def test_parse_usage_five_hour_only() -> None:
    """境界値: `five_hour` だけの応答を読み、`limits` が無ければ `scoped` は `[]`。"""
    u = claude_usage.parse_usage({"five_hour": {"utilization": 3, "resets_at": RESET_5}}, NOW)
    assert u.error is None
    assert u.five_hour == (3.0, _ts(RESET_5))
    assert u.seven_day is None
    assert u.scoped == []
    assert u.spend_limit_reached is None


def test_parse_usage_null_windows_with_spend_limit() -> None:
    """境界値: 2 つの窓が null で `spend_limit_reached` が真の応答は `shape` にせず、上限の旗を返す。"""
    u = claude_usage.parse_usage({"five_hour": None, "seven_day": None, "extra_usage": {"spend_limit_reached": True}}, NOW)
    assert u.error is None
    assert (u.five_hour, u.seven_day, u.spend_limit_reached) == (None, None, True)


@pytest.mark.parametrize("extra", ["yes", {"spend_limit_reached": "true"}, {"spend_limit_reached": 1}, None])
def test_parse_usage_broken_extra_usage_is_only_none(extra: Any) -> None:
    """境界値: `extra_usage` が崩れていてもその項目だけが None で、全体を `shape` にしない。"""
    u = claude_usage.parse_usage(_body(extra_usage=extra), NOW)
    assert u.error is None
    assert u.spend_limit_reached is None
    assert u.five_hour is not None


@pytest.mark.parametrize(
    "limits",
    [
        "weekly",
        [{"kind": "weekly_scoped", "percent": 5, "resets_at": RESET_7, "scope": {}}],
        [{"kind": "weekly_scoped", "percent": 5, "resets_at": RESET_7, "scope": {"model": {"id": ""}}}],
        [{"kind": "weekly_scoped", "percent": 5, "resets_at": "soon", "scope": {"model": {"id": "opus"}}}],
        [{"kind": "weekly_scoped", "percent": "5", "resets_at": RESET_7, "scope": {"model": {"id": "opus"}}}],
        [
            {"kind": "weekly_scoped", "percent": 5, "resets_at": RESET_7, "scope": {"model": {"id": "opus"}}},
            {"kind": "weekly_scoped", "percent": 6, "resets_at": RESET_7, "scope": {"model": {"id": "opus"}}},
        ],
        [{"kind": "weekly_scoped", "percent": 0, "scope": {"model": {"id": "opus"}}}],
    ],
)
def test_parse_scoped_broken_is_none(limits: Any) -> None:
    """異常: 識別の無い・`resets_at` の読めない・欠けた・`percent` が数でない・識別の重なる `weekly_scoped` で `scoped` が None。"""
    u = claude_usage.parse_usage(_body(limits=limits), NOW)
    assert u.error is None
    assert u.scoped is None
    assert u.five_hour is not None


def test_parse_scoped_skips_idle_element() -> None:
    """境界値: `percent` が 0 で `resets_at` が null の `weekly_scoped` は使用の無い窓として飛ばす。"""
    idle = {"kind": "weekly_scoped", "percent": 0, "resets_at": None, "scope": {"model": {"id": "haiku"}}}
    u = claude_usage.parse_usage(_body(limits=[idle]), NOW)
    assert u.scoped == []


@pytest.mark.parametrize(
    ("s", "want"),
    [
        ("2027-01-15T10:00:00Z", _ts("2027-01-15T10:00:00+00:00")),
        ("2027-01-15T10:00:00", _ts("2027-01-15T10:00:00+00:00")),
        ("2027-01-15T19:00:00+09:00", _ts("2027-01-15T10:00:00+00:00")),
        ("later", None),
        (1_800_000_000, None),
        (None, None),
    ],
)
def test_epoch(s: Any, want: float | None) -> None:
    """`_epoch`: `Z` と時間帯の無い時刻を UTC で読み、文字列でない・読めない値は None。"""
    assert claude_usage._epoch(s) == want


def test_get_usage_does_not_follow_redirect() -> None:
    """境界値: 302 の応答で転送先を呼ばず（トークンを転送先へ送らない）、`http-302` を返す。"""
    calls: list[httpx.Request] = []
    status, u = claude_usage.get_usage(
        TOKEN, NOW, 5, transport=_respond(302, None, calls, headers={"Location": "https://elsewhere.test/steal"})
    )
    assert (status, u.error) == (302, "http-302")
    assert all(r.url.host == "api.anthropic.com" for r in calls)


def test_get_usage_before_send_refuses() -> None:
    """`before_send` が理由を返せば送らず、`(-1, Usage(error=理由))` を返す。"""
    calls: list[httpx.Request] = []
    status, u = claude_usage.get_usage(TOKEN, NOW, 5, before_send=lambda: "scope", transport=_respond(200, _body(), calls))
    assert (status, u.error) == (-1, "scope")
    assert calls == []


def test_get_usage_unsendable_token_is_not_sent() -> None:
    """見出しで送れないアクセストークン（非 ASCII・空白）は送らず、例外を漏らさずに `(-1, Usage(error="token"))`。"""
    for token in ("トークン", "a b", ""):
        calls: list[httpx.Request] = []
        status, u = claude_usage.get_usage(token, NOW, 5, transport=_respond(200, _body(), calls))
        assert (status, u.error) == (-1, "token")
        assert calls == []


def test_get_usage_before_send_allows() -> None:
    """`before_send` が None を返せば送る。"""
    status, u = claude_usage.get_usage(TOKEN, NOW, 5, before_send=lambda: None, transport=_respond(200, _body()))
    assert (status, u.error) == (200, None)


# ---- refresh_oauth ----

OAUTH = {
    "accessToken": "old-access",
    "refreshToken": REFRESH,
    "expiresAt": 1,
    "refreshTokenExpiresAt": 2,
    "scopes": ["user:inference", "user:profile"],
    "subscriptionType": "max",
}


def _refresh(status: int, body: Any = None, calls: list[httpx.Request] | None = None) -> tuple[str, dict | None]:
    return claude_usage.refresh_oauth(OAUTH, NOW, 3, transport=_respond(status, body, calls))


def test_refresh_oauth_ok_replaces_tokens() -> None:
    """正常: 200 の応答で `accessToken`・`refreshToken`・`expiresAt`・`refreshTokenExpiresAt`・`scopes` を替え、ほかの項目を残す。要求は本体と同じ形。"""
    calls: list[httpx.Request] = []
    body = {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_in": 3600,
        "refresh_token_expires_in": 7200,
        "scope": "user:profile",
    }
    result, new = _refresh(200, body, calls)
    assert result == "ok"
    assert new == {
        **OAUTH,
        "accessToken": "new-access",
        "refreshToken": "new-refresh",
        "expiresAt": int((NOW + 3600) * 1000),
        "refreshTokenExpiresAt": int((NOW + 7200) * 1000),
        "scopes": ["user:profile"],
    }
    (req,) = calls
    assert (req.method, str(req.url)) == ("POST", claude_usage.TOKEN_URL)
    assert json.loads(req.content) == {
        "grant_type": "refresh_token",
        "refresh_token": REFRESH,
        "client_id": claude_usage.CLIENT_ID,
        "scope": "user:inference user:profile",
    }


@pytest.mark.parametrize("extra", [{}, {"refresh_token": None}])
def test_refresh_oauth_keeps_refresh_token_when_absent(extra: dict[str, Any]) -> None:
    """境界値: `refresh_token` の無い・null の応答は元の値を残し、`expiresAt` は `(now + expires_in) × 1000`。"""
    result, new = _refresh(200, {"access_token": "new-access", "expires_in": 60, **extra})
    assert result == "ok"
    assert new is not None
    assert new["refreshToken"] == REFRESH
    assert new["expiresAt"] == int((NOW + 60) * 1000)
    assert new["scopes"] == OAUTH["scopes"]


@pytest.mark.parametrize("refresh_expires", ["7200", 1e308, 10**400])
def test_refresh_oauth_keeps_optional_on_broken_values(refresh_expires: Any) -> None:
    """境界値: `refresh_token_expires_in` が文字列・期限へ変換できない数、`scope` が空なら元の値を残して `ok`。"""
    result, new = _refresh(200, {"access_token": "a", "expires_in": 60, "refresh_token_expires_in": refresh_expires, "scope": ""})
    assert result == "ok"
    assert new is not None
    assert new["expiresAt"] == int((NOW + 60) * 1000)
    assert new["refreshTokenExpiresAt"] == OAUTH["refreshTokenExpiresAt"]
    assert new["scopes"] == OAUTH["scopes"]


@pytest.mark.parametrize("status", sorted(claude_usage.REFRESH_REJECTED))
def test_refresh_oauth_rejected(status: int) -> None:
    """異常: 400・401・403 は `rejected`。"""
    assert _refresh(status, {"error": "invalid_grant"}) == ("rejected", None)


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (429, {"error": "rate"}),
        (500, None),
        (200, {"expires_in": 60}),
        (200, {"access_token": "", "expires_in": 60}),
        (200, {"access_token": "a", "expires_in": 0}),
        (200, {"access_token": "a", "expires_in": -5}),
        (200, {"access_token": "a", "expires_in": "60"}),
        (200, {"access_token": "a", "expires_in": True}),
        (200, {"access_token": "a", "expires_in": 60, "refresh_token": ""}),
        (200, {"access_token": "a", "expires_in": 60, "refresh_token": 5}),
        (200, {"access_token": "トークン", "expires_in": 60}),
        (200, {"access_token": "a\r\nX: y", "expires_in": 60}),
        (200, {"access_token": "a", "expires_in": 1e308}),
        (200, {"access_token": "a", "expires_in": 10**400}),
        (200, None),
    ],
)
def test_refresh_oauth_error(status: int, body: Any) -> None:
    """異常: 429・500・必須の条件に外れた 200 は `error` で、返りにトークンの値が無い。"""
    got = _refresh(status, body)
    assert got == ("error", None)
    assert REFRESH not in repr(got)


def test_refresh_oauth_network_error() -> None:
    """異常: 通信の失敗は `error`。"""
    t = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down", request=r)))
    assert claude_usage.refresh_oauth(OAUTH, NOW, 3, transport=t) == ("error", None)


# ---- request_json ----


def test_request_json_returns_status_and_object() -> None:
    """渡したメソッドで送り、状態の番号と JSON の object を返す。`User-Agent` が無ければ足す。本文が object でなければ `(状態, None)`。"""
    calls: list[httpx.Request] = []
    assert http.request_json("POST", "https://x.test/a", {}, 5, body={"k": 1}, transport=_respond(201, {"ok": 1}, calls)) == (
        201,
        {"ok": 1},
    )
    assert calls[0].headers["User-Agent"] == "ndf-claude-accounts"
    assert json.loads(calls[0].content) == {"k": 1}
    assert http.request_json("GET", "https://x.test/a", {"User-Agent": "mine"}, 5, transport=_respond(200, [1], calls)) == (200, None)
    assert calls[1].headers["User-Agent"] == "mine"
    assert [c.method for c in calls] == ["POST", "GET"]
    assert http.request_json("GET", "https://x.test/a", {}, 5, transport=_respond(200, None, content=b"not json")) == (200, None)


def test_request_json_unsendable_header_is_network_failure() -> None:
    """見出しに送れない値（非 ASCII）があっても例外を漏らさず `(0, None)`。"""
    calls: list[httpx.Request] = []
    assert http.request_json("GET", "https://x.test/a", {"Authorization": "Bearer トークン"}, 5, transport=_respond(200, {}, calls)) == (
        0,
        None,
    )
    assert calls == []


def test_request_json_body_over_limit() -> None:
    """本文が 1 MiB を超えれば打ち切って `(0, None)`。"""
    big = httpx.MockTransport(lambda r: httpx.Response(200, content=b"{" + b" " * (1024 * 1024 + 1) + b"}"))
    assert http.request_json("GET", "https://x.test/a", {}, 5, transport=big) == (0, None)


def test_request_json_bad_url() -> None:
    """URL の失敗は `(0, None)`。"""
    assert http.request_json("GET", "ftp://x.test/a", {}, 5) == (0, None)


def _timed(fn: Callable[[], Any]) -> tuple[Any, float]:
    start = time.monotonic()
    got = fn()
    return got, time.monotonic() - start


def test_request_json_slow_body_hits_deadline() -> None:
    """往復全体の期限: 見出しと本文を上限より短い間隔で少しずつ返し続けるサーバーでも、`timeout` の内に `(0, None)`。"""
    url, stop = _drip_server.start(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n", b"x", every=0.1)
    try:
        got, took = _timed(lambda: http.request_json("GET", url, {}, 0.6))
    finally:
        stop()
    assert got == (0, None)
    assert took < 0.6 + MARGIN


def test_request_json_slow_resolve_hits_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """往復全体の期限: 名前解決が `timeout` より長く返らなくても、結果を待たずに `(0, None)`。"""
    release = threading.Event()

    def slow_resolve(*a: Any, **k: Any) -> Any:
        release.wait(10)
        raise socket.gaierror("late")

    monkeypatch.setattr(http, "DeadlineBackend", functools.partial(http.DeadlineBackend, resolve=slow_resolve))
    try:
        got, took = _timed(lambda: http.request_json("GET", "http://slow.test/", {}, 0.5))
    finally:
        release.set()
    assert got == (0, None)
    assert took < 0.5 + MARGIN


def test_request_json_two_slow_addresses_share_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """往復全体の期限: アドレスが 2 つあってどちらの接続も渡された残りいっぱい待っても、通しで `timeout` の内に `(0, None)`。"""
    infos = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 80)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.2", 80)),
    ]
    given: list[float] = []

    def connect(info: Any, timeout: float) -> socket.socket:
        given.append(timeout)
        threading.Event().wait(timeout)
        raise TimeoutError("slow")

    backend = functools.partial(http.DeadlineBackend, resolve=lambda *a, **k: infos, connect=connect)
    monkeypatch.setattr(http, "DeadlineBackend", backend)
    got, took = _timed(lambda: http.request_json("GET", "http://two.test/", {}, 0.6))
    assert got == (0, None)
    assert took < 0.6 + MARGIN
    assert given and given[0] <= 0.6
    assert sum(given) <= 0.6 + MARGIN


def test_request_json_before_send_refuses_without_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    """`before_send` が理由を返せば接続の前に打ち切り、`(-1, None)` を返す。"""
    resolved: list[Any] = []
    backend = functools.partial(http.DeadlineBackend, resolve=lambda *a, **k: resolved.append(a) or [])
    monkeypatch.setattr(http, "DeadlineBackend", backend)
    assert http.request_json("GET", "http://x.test/", {}, 1, before_send=lambda: "token") == (-1, None)
    assert resolved == []


def test_request_json_real_connection_ok() -> None:
    """期限の層を通した本物の接続で、すぐ返る応答を読める。"""
    payload = json.dumps({"five_hour": None}).encode()
    head = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(payload)
    url, stop = _drip_server.start(head + payload, b"", every=5, count=0)
    try:
        assert http.request_json("GET", url, {}, 3) == (200, {"five_hour": None})
    finally:
        stop()


def test_get_usage_returns_utc_time_window() -> None:
    """`resets_at` の Unix 秒は UTC で読む（時間帯の無い時刻も UTC）。"""
    naive = "2027-01-15T10:00:00"
    u = claude_usage.parse_usage({"five_hour": {"utilization": 1, "resets_at": naive}}, NOW)
    assert u.five_hour == (1.0, datetime(2027, 1, 15, 10, tzinfo=timezone.utc).timestamp())


# ---- request_json のプロキシ ----

_JSON_HEAD = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n"


def _record_resolve(monkeypatch: pytest.MonkeyPatch, hosts: list[str], fail: bool = False) -> None:
    def resolve(host: str, *a: Any, **k: Any) -> Any:
        hosts.append(host)
        if fail:
            raise socket.gaierror("no such host")
        return socket.getaddrinfo(host, *a, **k)

    monkeypatch.setattr(http, "DeadlineBackend", functools.partial(http.DeadlineBackend, resolve=resolve))


def test_request_json_goes_through_env_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """`HTTP_PROXY` があればプロキシへつなぎ、宛先の名前を手元で引かない。"""
    payload = json.dumps({"via": "proxy"}).encode()
    proxy, stop = _drip_server.start(_JSON_HEAD % len(payload) + payload, b"", every=5, count=0)
    monkeypatch.setenv("HTTP_PROXY", proxy)
    hosts: list[str] = []
    _record_resolve(monkeypatch, hosts)
    try:
        assert http.request_json("GET", "http://usage.invalid/", {}, 3) == (200, {"via": "proxy"})
    finally:
        stop()
    assert hosts == ["127.0.0.1"]


def test_request_json_no_proxy_connects_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    """`NO_PROXY` に当たる宛先はプロキシを通らず、宛先へ直接つなぐ。"""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9/")
    monkeypatch.setenv("NO_PROXY", "usage.invalid")
    hosts: list[str] = []
    _record_resolve(monkeypatch, hosts, fail=True)
    assert http.request_json("GET", "http://usage.invalid/", {}, 3) == (0, None)
    assert hosts == ["usage.invalid"]


def test_request_json_slow_proxy_hits_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """往復全体の期限: プロキシが応答を少しずつ返し続けても、`timeout` の内に `(0, None)`。"""
    proxy, stop = _drip_server.start(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n", b"x", every=0.1)
    monkeypatch.setenv("HTTP_PROXY", proxy)
    try:
        got, took = _timed(lambda: http.request_json("GET", "http://usage.invalid/", {}, 0.6))
    finally:
        stop()
    assert got == (0, None)
    assert took < 0.6 + MARGIN


# ---- 送信の期限 ----


def test_deadline_stream_write_stops_slow_reader() -> None:
    """往復全体の期限: 相手が少しずつ読み取り続けても、送信は部分送信ごとに残りを引き直し、期限で打ち切る。"""
    a, b = socket.socketpair()
    a.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    b.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    quit_ = threading.Event()

    def drain() -> None:
        while not quit_.wait(0.02):
            try:
                b.recv(1024)
            except OSError:
                return

    threading.Thread(target=drain, daemon=True).start()
    stream = http._DeadlineStream(http.SyncStream(a), time.monotonic() + 0.5)
    try:
        start = time.monotonic()
        with pytest.raises(http.httpcore.WriteTimeout):
            stream.write(b"x" * (8 * 1024 * 1024), timeout=5)
        assert time.monotonic() - start < 0.5 + MARGIN
    finally:
        quit_.set()
        a.close()
        b.close()


# ---- 宛先に当たるプロキシの設定だけを組む ----


def test_request_json_ignores_unusable_proxy_of_other_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTPS の宛先は、対象外の `HTTP_PROXY` の SOCKS（socksio が無くて組めない）を組まず、例外を漏らさない。"""
    monkeypatch.setenv("HTTP_PROXY", "socks5://127.0.0.1:9")
    hosts: list[str] = []
    _record_resolve(monkeypatch, hosts, fail=True)
    assert http.request_json("GET", "https://usage.invalid/", {}, 3) == (0, None)
    assert hosts == ["usage.invalid"]
    assert http.request_json("GET", "https://usage.invalid/", {}, 3, before_send=lambda: "token") == (-1, None)


@pytest.mark.parametrize("name", ["HTTPS_PROXY", "ALL_PROXY"])
@pytest.mark.parametrize("proxy", ["socks5://127.0.0.1:9", "ftp://127.0.0.1:9"])
def test_request_json_unusable_proxy_is_network_failure(monkeypatch: pytest.MonkeyPatch, name: str, proxy: str) -> None:
    """宛先に当たるプロキシが組めない（socksio の無い SOCKS・対応しない形）なら直接つながず `(0, None)`。取得と更新も例外を漏らさない。"""
    monkeypatch.setitem(__import__("sys").modules, "socksio", None)
    monkeypatch.setenv(name, proxy)
    hosts: list[str] = []
    _record_resolve(monkeypatch, hosts)
    assert http.request_json("GET", "https://usage.invalid/", {}, 3) == (0, None)
    assert claude_usage.get_usage(TOKEN, NOW, 3)[1].error == "network"
    assert claude_usage.refresh_oauth(OAUTH, NOW, 3) == ("error", None)
    assert hosts == []
    # 送らない理由（停止など）は組めないプロキシより優先する
    assert http.request_json("GET", "https://usage.invalid/", {}, 3, before_send=lambda: "stopped") == (-1, None)
    assert http.request_json("GET", "https://usage.invalid/", {}, 3, before_send=lambda: None) == (0, None)
    assert claude_usage.get_usage(TOKEN, NOW, 3, before_send=lambda: "stopped")[0] == -1
    assert hosts == []


def test_request_json_builds_one_transport_for_many_no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """`NO_PROXY` の除外先がいくつあっても、要求 1 回に組む転送（と TLS の設定）は 1 つ。"""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", ",".join(f"h{i}.invalid" for i in range(20)))
    built: list[Any] = []
    real = httpx.HTTPTransport

    def counting(*a: Any, **k: Any) -> httpx.HTTPTransport:
        built.append(k.get("proxy"))
        return real(*a, **k)

    monkeypatch.setattr(httpx, "HTTPTransport", counting)
    _record_resolve(monkeypatch, [], fail=True)
    assert http.request_json("GET", "https://h3.invalid/", {}, 3) == (0, None)
    assert built == [None]
    assert http.request_json("GET", "https://usage.invalid/", {}, 3) == (0, None)
    assert built == [None, "http://127.0.0.1:9"]


# ---- 読めない本文 ----


@pytest.mark.parametrize("raw", [b'{"access_token": "a", "expires_in": 1' + b"0" * 5000 + b"}", b"[" * 200_000])
def test_refresh_oauth_unreadable_number_is_error(raw: bytes) -> None:
    """整数の桁数の上限を超える数・深すぎる入れ子の本文（1 MiB の内）は読めない応答で、更新は `error`。"""
    t = _respond(200, content=raw)
    assert http.request_json("POST", "https://x.test/a", {}, 5, transport=t) == (200, None)
    assert claude_usage.refresh_oauth(OAUTH, NOW, 3, transport=t) == ("error", None)


# ---- HTTPS のプロキシ越しの HTTPS ----


@pytest.fixture
def proxy_cert(tmp_path: Any) -> tuple[str, str]:
    """プロキシの自己署名の証明書（127.0.0.1）と鍵。openssl が無ければ飛ばす。"""
    if shutil.which("openssl") is None:
        pytest.skip("openssl が無い")
    cert, key = str(tmp_path / "c.pem"), str(tmp_path / "k.pem")
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            key,
            "-out",
            cert,
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


def test_request_json_tls_in_tls_slow_handshake_hits_deadline(monkeypatch: pytest.MonkeyPatch, proxy_cert: tuple[str, str]) -> None:
    """往復全体の期限: HTTPS のプロキシへの CONNECT の後、宛先との TLS の応答が少しずつ届き続けても、`timeout` の内に `(0, None)`。"""
    cert, key = proxy_cert
    ctx = ssl.create_default_context(cafile=cert)
    monkeypatch.setattr(http.httpcore._sync.connection, "default_ssl_context", lambda: ctx)
    proxy, stop = _drip_server.start_tls_tunnel(cert, key, b"\x00", every=0.1)
    monkeypatch.setenv("HTTPS_PROXY", proxy)
    try:
        got, took = _timed(lambda: http.request_json("GET", "https://usage.invalid/", {}, 0.8))
    finally:
        stop()
    assert got == (0, None)
    assert took < 0.8 + MARGIN
