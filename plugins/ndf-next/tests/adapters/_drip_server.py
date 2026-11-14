"""本物の接続で、応答を少しずつ送って黙る仮のサーバー。"""

from __future__ import annotations

import contextlib
import socket
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
