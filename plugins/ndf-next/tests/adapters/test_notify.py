"""HTTP で送る・取るアダプター（`ndf.adapters.notify`）。ネットワークへは出ず、`httpx.MockTransport` で答える。"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import httpx
import pytest

from ndf.adapters import notify

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
    "handler",
    [
        lambda req: httpx.Response(200, json=[1, 2]),
        lambda req: httpx.Response(500, json={"ok": True}),
        lambda req: httpx.Response(200, content=b"not json"),
        _raise(httpx.ConnectError),
        _raise(httpx.ReadTimeout),
    ],
    ids=["array", "status-500", "not-json", "connect-error", "timeout"],
)
def test_post_json_returns_none(handler: Handler) -> None:
    assert notify.post_json("https://x.test/a", {}, {}, 5, transport=_transport({"/a": handler})) is None


def test_post_json_returns_none_for_invalid_url() -> None:
    assert notify.post_json("::", {}, {}, 5, transport=_transport({})) is None


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


def test_fetch_body_each_read_waits_only_the_remaining_time(clock: _Clock) -> None:
    seen: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        timeouts = req.extensions["timeout"]

        def body() -> Iterator[bytes]:
            for step in (1.0, 2.0):
                seen.append(timeouts["read"])
                clock.advance(step)
                yield b"x"
            seen.append(timeouts["read"])

        return httpx.Response(200, content=body())

    assert notify.fetch_body("https://x.test/a", 5, transport=_transport({"/a": handler})) == b"xx"
    assert seen == [5, 4, 2]


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
