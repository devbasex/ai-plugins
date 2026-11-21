"""本物の接続で、応答を少しずつ送って黙る仮のサーバー。"""

from __future__ import annotations

import contextlib
import socket
import ssl
import threading
from collections.abc import Callable


def _answer(srv: socket.socket, quit_: threading.Event, head: bytes, drip: bytes, every: float, count: int | None) -> None:
    conn, _ = srv.accept()
    with conn, contextlib.suppress(OSError):
        conn.recv(65536)
        conn.sendall(head)
        sent = 0
        while (count is None or sent < count) and not quit_.wait(every):
            conn.sendall(drip)
            sent += 1
        quit_.wait(5)


def start(head: bytes, drip: bytes, every: float, count: int | None = None) -> tuple[str, Callable[[], None]]:
    """サーバーを立て、URL と止める関数を返す。

    要求を受けたら `head` をすぐ送り、`drip` を `every` 秒ごとに `count` 回（`None` なら止められるまで）送って黙る。
    """
    srv = socket.create_server(("127.0.0.1", 0))
    quit_ = threading.Event()
    thread = threading.Thread(target=_answer, args=(srv, quit_, head, drip, every, count), daemon=True)
    thread.start()

    def stop() -> None:
        quit_.set()
        thread.join(5)
        srv.close()

    return f"http://127.0.0.1:{srv.getsockname()[1]}/", stop


def _tunnel(srv: socket.socket, ctx: ssl.SSLContext, quit_: threading.Event, drip: bytes, every: float) -> None:
    conn, _ = srv.accept()
    with conn, contextlib.suppress(OSError), ctx.wrap_socket(conn, server_side=True) as tls:
        got = b""
        while b"\r\n\r\n" not in got:
            chunk = tls.recv(65536)
            if not chunk:
                return
            got += chunk
        tls.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        # 接続先との TLS の最初の記録の頭（handshake・16 KiB）だけを送り、中身を少しずつ送る
        tls.sendall(b"\x16\x03\x03\x40\x00")
        while not quit_.wait(every):
            tls.sendall(drip)
        quit_.wait(5)


def start_tls_tunnel(cert: str, key: str, drip: bytes, every: float) -> tuple[str, Callable[[], None]]:
    """HTTPS のプロキシを立て、プロキシの URL と止める関数を返す。

    プロキシとの TLS を `cert`・`key` で受け、CONNECT に 200 を返した後、接続先との TLS のハンドシェイクの応答を
    `drip` ずつ `every` 秒ごとに止められるまで送る。
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv = socket.create_server(("127.0.0.1", 0))
    quit_ = threading.Event()
    thread = threading.Thread(target=_tunnel, args=(srv, ctx, quit_, drip, every), daemon=True)
    thread.start()

    def stop() -> None:
        quit_.set()
        thread.join(5)
        srv.close()

    return f"https://127.0.0.1:{srv.getsockname()[1]}", stop
