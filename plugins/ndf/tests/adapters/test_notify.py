"""HTTP で送る・取るアダプター（`ndf.adapters.notify`）。ネットワークへは出ず、`httpx.MockTransport` で回答する。"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import httpx
import pytest

from ndf.adapters import http as notify


Handler = Callable[[httpx.Request], httpx.Response]


def _transport(routes: dict[str, Handler]) -> httpx.MockTransport:
    return httpx.MockTransport(lambda req: routes[req.url.path](req))


class _Clock:
    """`notify._now` の代わり。`advance` で進める。"""

    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(notify, "_now", c)
    return c


def _slow_body(clock: _Clock, steps: list[float]) -> Iterator[bytes]:
    for step in steps:
        clock.advance(step)
        yield b"x"


def _raise(exc: type[httpx.TransportError]) -> Handler:
    def handler(req: httpx.Request) -> httpx.Response:
        raise exc("失敗", request=req)

    return handler


# ---- post_json ----


def test_post_json_returns_object() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"ok": True})

    got = notify.post_json("https://x.test/a", {"q": 1}, {"Authorization": "Bearer k"}, 5, transport=_transport({"/a": handler}))
    assert got == {"ok": True}
    assert json.loads(seen[0].content) == {"q": 1}
    assert seen[0].headers["Authorization"] == "Bearer k"


@pytest.mark.parametrize(
    ("handler", "reason", "retryable"),
    [
        (lambda req: httpx.Response(200, json=[1, 2]), "応答が JSON のオブジェクトでない", False),
        (lambda req: httpx.Response(400, json={"ok": True}), "HTTP 400", False),
        (lambda req: httpx.Response(429), "HTTP 429", True),
        (lambda req: httpx.Response(500, json={"ok": True}), "HTTP 500", True),
        (lambda req: httpx.Response(200, content=b"not json"), "応答が JSON でない", False),
        (_raise(httpx.ConnectError), "接続できない（ConnectError）", True),
        (_raise(httpx.ReadTimeout), "5 秒を越えた", True),
    ],
    ids=["array", "status-400", "status-429", "status-500", "not-json", "connect-error", "timeout"],
)
def test_post_json_tells_failures_apart(handler: Handler, reason: str, retryable: bool) -> None:
    # 3394-10: 失敗を 1 つの値にまとめず、理由と打ち直して通りうるかを分ける
    with pytest.raises(notify.PostError) as got:
        notify.post_json("https://x.test/a", {}, {}, 5, transport=_transport({"/a": handler}))
    assert (str(got.value), got.value.retryable) == (reason, retryable)


@pytest.mark.parametrize(("header", "after"), [("30", 30.0), ("soon", None), (None, None)], ids=["seconds", "not-seconds", "absent"])
def test_post_json_keeps_retry_after(header: str | None, after: float | None) -> None:
    headers = {} if header is None else {"Retry-After": header}
    with pytest.raises(notify.PostError) as got:
        notify.post_json("https://x.test/a", {}, {}, 5, transport=_transport({"/a": lambda req: httpx.Response(429, headers=headers)}))
    assert got.value.retry_after == after


@pytest.mark.parametrize(("url", "transport"), [("::", _transport({})), ("ftp://x.test/a", None)], ids=["invalid", "scheme"])
def test_post_json_invalid_url_is_not_retryable(url: str, transport: httpx.BaseTransport | None) -> None:
    # scheme の誤りは本物の transport が接続の前に断る（ネットワークへ出ない）
    with pytest.raises(notify.PostError, match="^URL が不正$") as got:
        notify.post_json(url, {}, {}, 5, transport=transport)
    assert got.value.retryable is False


# ---- fetch_body ----


def test_fetch_body_returns_body() -> None:
    t = _transport({"/a": lambda req: httpx.Response(200, content=b"body")})
    assert notify.fetch_body("https://x.test/a", 5, transport=t) == b"body"


def test_fetch_body_follows_redirect() -> None:
    t = _transport(
        {
            "/old": lambda req: httpx.Response(301, headers={"Location": "/new"}),
            "/new": lambda req: httpx.Response(200, content=b"moved"),
        }
    )
    assert notify.fetch_body("https://x.test/old", 5, transport=t) == b"moved"


def test_fetch_body_finishes_exactly_at_deadline(clock: _Clock) -> None:
    t = _transport({"/a": lambda req: httpx.Response(200, content=_slow_body(clock, [2.0, 3.0]))})
    assert notify.fetch_body("https://x.test/a", 5, transport=t) == b"xx"


def test_fetch_body_deadline_while_reading(clock: _Clock) -> None:
    t = _transport({"/a": lambda req: httpx.Response(200, content=_slow_body(clock, [2.0, 3.5]))})
    with pytest.raises(notify.FetchError, match=r"^5 秒を越えた$"):
        notify.fetch_body("https://x.test/a", 5, transport=t)


def test_fetch_body_deadline_counts_across_redirect(clock: _Clock) -> None:
    def old(req: httpx.Request) -> httpx.Response:
        clock.advance(6)
        return httpx.Response(302, headers={"Location": "/new"})

    t = _transport({"/old": old, "/new": lambda req: httpx.Response(200, content=b"late")})
    with pytest.raises(notify.FetchError, match=r"^5 秒を越えた$"):
        notify.fetch_body("https://x.test/old", 5, transport=t)


def test_fetch_body_redirect_gets_remaining_timeout(clock: _Clock) -> None:
    timeouts: list[dict] = []

    def old(req: httpx.Request) -> httpx.Response:
        timeouts.append(req.extensions["timeout"])
        clock.advance(2)
        return httpx.Response(302, headers={"Location": "/new"})

    def new(req: httpx.Request) -> httpx.Response:
        timeouts.append(req.extensions["timeout"])
        return httpx.Response(200, content=b"ok")

    assert notify.fetch_body("https://x.test/old", 5, transport=_transport({"/old": old, "/new": new})) == b"ok"
    assert [t["read"] for t in timeouts] == [5, 3]


def test_fetch_body_does_not_read_redirect_body(clock: _Clock) -> None:
    def old(req: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "/new"}, content=_slow_body(clock, [10.0]))

    t = _transport({"/old": old, "/new": lambda req: httpx.Response(200, content=b"ok")})
    assert notify.fetch_body("https://x.test/old", 5, transport=t) == b"ok"


@pytest.mark.parametrize(
    ("routes", "reason"),
    [
        ({"/a": lambda req: httpx.Response(404)}, "HTTP 404"),
        (
            {"/a": lambda req: httpx.Response(301, headers={"Location": "/b"}), "/b": lambda req: httpx.Response(404)},
            "HTTP 404",
        ),
        ({"/a": lambda req: httpx.Response(302, headers={"Location": "/a"})}, "転送が多すぎる"),
        ({"/a": _raise(httpx.ConnectError)}, "接続できない（ConnectError）"),
        ({"/a": _raise(httpx.ReadTimeout)}, "5 秒を越えた"),
    ],
    ids=["status-404", "404-after-redirect", "too-many-redirects", "connect-error", "timeout"],
)
def test_fetch_body_errors(routes: dict[str, Handler], reason: str) -> None:
    with pytest.raises(notify.FetchError) as caught:
        notify.fetch_body("https://x.test/a", 5, transport=_transport(routes))
    assert str(caught.value) == reason


def test_fetch_body_invalid_url() -> None:
    with pytest.raises(notify.FetchError, match=r"^URL が不正$"):
        notify.fetch_body("::", 5, transport=_transport({}))


# 本物の記述（GitHub の REST の OpenAPI）と同じ大きさ（2026-11-23 の実測）と、その取得に使う上限（64 MiB）
_DESCRIPTION_BYTES = 14_395_662
_DESCRIPTION_MAX = 64 * 1024 * 1024


@pytest.mark.parametrize(
    ("size", "max_bytes", "reason"),
    [
        (_DESCRIPTION_BYTES, _DESCRIPTION_MAX, None),
        (8, 8, None),
        (9, 8, "本文が 8 バイトを超えた"),
        (0, 0, None),
    ],
    ids=["real-size", "exactly-max", "max-plus-one", "empty"],
)
def test_fetch_body_limits_the_body_size(size: int, max_bytes: int, reason: str | None) -> None:
    t = _transport({"/a": lambda req: httpx.Response(200, content=iter([b"x" * (size // 2), b"x" * (size - size // 2)]))})
    if reason is None:
        assert len(notify.fetch_body("https://x.test/a", 5, transport=t, max_bytes=max_bytes)) == size
        return
    with pytest.raises(notify.FetchError) as caught:
        notify.fetch_body("https://x.test/a", 5, transport=t, max_bytes=max_bytes)
    assert str(caught.value) == reason


@pytest.mark.parametrize("status", [301, 302, 304, 307])
def test_fetch_body_without_follow_sends_one_request(status: int) -> None:
    seen: list[str] = []

    def unread() -> Iterator[bytes]:
        raise AssertionError("3xx の本文を読んだ")
        yield b""

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        return httpx.Response(status, headers={"Location": "/new"}, content=unread())

    with pytest.raises(notify.FetchError) as caught:
        notify.fetch_body("https://x.test/old", 5, transport=httpx.MockTransport(handler), follow=False)
    assert str(caught.value) == f"HTTP {status}（転送は追わない）"
    assert seen == ["/old"]
