"""HTTP で送る・取るアダプター（`ndf.adapters.notify`）。ネットワークへは出ず、`httpx.MockTransport` で答える。"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import threading
import time
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


Serve = Callable[..., str]


@pytest.fixture
def serve() -> Iterator[Serve]:
    """本物の接続のサーバーを立て、URL を返す。

    要求を受けたら `head` をすぐ送り、`drip` を `every` 秒ごとに `count` 回（`None` なら止められるまで）送って黙る。
    """
    stops: list[Callable[[], None]] = []

    def start(head: bytes, drip: bytes, every: float, count: int | None = None) -> str:
        srv = socket.create_server(("127.0.0.1", 0))
        quit_ = threading.Event()

        def run() -> None:
            conn, _ = srv.accept()
            with conn, contextlib.suppress(OSError):
                conn.recv(65536)
                conn.sendall(head)
                sent = 0
                while (count is None or sent < count) and not quit_.wait(every):
                    conn.sendall(drip)
                    sent += 1
                quit_.wait(5)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        def stop() -> None:
            quit_.set()
            thread.join(5)
            srv.close()

        stops.append(stop)
        return f"http://127.0.0.1:{srv.getsockname()[1]}/"

    yield start
    for stop in stops:
        stop()


def _fails_at_deadline(url: str) -> None:
    started = time.monotonic()
    with pytest.raises(notify.FetchError, match=r"^1 秒を越えた$"):
        notify.fetch_body(url, 1)
    assert time.monotonic() - started < 1.5


def test_fetch_body_stops_waiting_at_deadline_on_real_connection(serve: Serve) -> None:
    # httpcore は本文の読み始めの待ちの秒数（1 秒）で次の読み取りも待つため、期限で止めないと
    # 0.8 秒の受信の後に 1 秒待って 1.8 秒まで延びる
    _fails_at_deadline(serve(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nx", b"x", 0.8, count=1))


def test_fetch_body_unsized_body_cut_at_deadline_is_not_success(serve: Serve) -> None:
    # 長さの無い応答では、期限で止めた受信の終わりが本文の正常な終わりと読まれる
    _fails_at_deadline(serve(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nx", b"x", 0.3))


def test_fetch_body_deadline_covers_trickling_headers(serve: Serve) -> None:
    # 応答の頭が受信の待ちの秒数より短い間隔で届き続けても、上限で止める
    _fails_at_deadline(serve(b"HTTP/1.1 200 OK\r\n", b"X-A: b\r\n", 0.3))


class _Stream:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock

    def get_extra_info(self, name: str) -> socket.socket | None:
        return self.sock if name == "socket" else None


def test_watchdog_stops_the_tls_connection_after_tcp_one_is_detached() -> None:
    # httpcore の TLS 化（ssl の wrap_socket）は TCP の接続を手放して記述子を -1 にし、TLS の接続を新しく返す。
    # 期限に止めるのは、TLS 化の後の接続でなければならない
    tcp, _tcp_peer = socket.socketpair()
    tls, tls_peer = socket.socketpair()
    with tcp, _tcp_peer, tls, tls_peer:
        watch = notify._Watchdog(0.2)
        watch.trace("connection.connect_tcp.complete", {"return_value": _Stream(tcp)})
        os.close(tcp.detach())
        watch.trace("connection.start_tls.complete", {"return_value": _Stream(tls)})
        tls_peer.settimeout(2)
        assert tls_peer.recv(1) == b""
        assert watch.fired
        watch.stop()


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
