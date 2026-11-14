"""HTTP で送る・取るアダプター。HTTP は httpx だけが行い、urllib を使わない。

- `post_json` は JSON を 1 回 POST し、応答の JSON のオブジェクトを返す。失敗は `None` で、例外を外へ出さない
- `fetch_body` は URL の本文を、1 件あたりの総経過時間の上限の内で取得する。失敗は `FetchError`

上限の秒数は呼ぶ側が上限の宣言（`contracts/limits.json`）から引いて渡す。テストは `transport` に
`httpx.MockTransport` を渡し、ネットワークへ出ない。
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from typing import Any

import httpx

# 単調時計。テストが差し替える
_now = time.monotonic


class FetchError(Exception):
    """本文を取得できなかった。`str(e)` が理由の日本語 1 行。"""


def post_json(
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any] | None:
    """JSON を 1 回 POST し、応答が 2xx で JSON のオブジェクトならその辞書を返す。ほかは `None`。"""
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            resp = client.post(url, json=body, headers=headers)
    except (httpx.HTTPError, httpx.InvalidURL):  # noqa: NDF401 ネットワーク: 送れなければ呼ぶ側が判定を見送る
        return None
    if not resp.is_success:
        return None
    try:
        data = resp.json()
    except (json.JSONDecodeError, UnicodeDecodeError):  # noqa: NDF401 ネットワーク: 相手の本文が JSON でなければ見送る
        return None
    return data if isinstance(data, dict) else None


def _seconds(timeout: float) -> str:
    return f"{timeout:g}"


# 要求ごとに新しく接続を張り、期限の監視が今の接続を必ず受け取れるようにする
_NO_KEEPALIVE = httpx.Limits(max_keepalive_connections=0)


def _expired(timeout: float) -> FetchError:
    return FetchError(f"{_seconds(timeout)} 秒を越えた")


class _Watchdog:
    """期限に、待っている受信（応答の頭と本文）を止める。

    httpcore は受信の待ちの秒数を要求ごと・本文の読み始めに引き、以後の受信はその秒数で待つため、少しずつ
    届き続ける応答では総経過時間の上限が効かない。そこで取得の全体に 1 つの期限を掛け、期限に別のスレッドから
    今の接続を止め（`shutdown`）、待っている受信を失敗か終わりで戻す。接続は張るたびに httpcore の
    `trace` で受け取る。止めた後の受信の終わりは本文の正常な終わりと読まれることがある（長さの無い応答）ため、
    読み終えた側は `fired` を確かめる。
    """

    def __init__(self, seconds: float) -> None:
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._done = False
        self.fired = False
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True
        self._timer.start()

    def trace(self, event: str, info: dict[str, Any]) -> None:
        """httpcore の `trace` の拡張。TCP の接続を張り終えたら、その接続を監視の対象にする。"""
        if event != "connection.connect_tcp.complete":
            return
        stream = info.get("return_value")
        sock = stream.get_extra_info("socket") if stream is not None else None
        with self._lock:
            self._sock = sock if isinstance(sock, socket.socket) else None
            if self.fired:
                _shutdown(self._sock)

    def _fire(self) -> None:
        with self._lock:
            if self._done:
                return
            self.fired = True
            _shutdown(self._sock)

    def stop(self) -> None:
        with self._lock:
            self._done = True
        self._timer.cancel()


def _shutdown(sock: socket.socket | None) -> None:
    """接続の受信と送信を止める。接続の無いとき（テストの transport）と閉じた接続では何もしない。

    TLS の接続でも TLS の状態を別のスレッドから変えないよう、記述子を複製して `shutdown` だけを掛ける。
    """
    if sock is None:
        return
    try:
        with socket.fromfd(sock.fileno(), sock.family, sock.type) as dup:
            dup.shutdown(socket.SHUT_RDWR)
    except OSError:  # noqa: NDF401 ネットワーク: 既に閉じた接続は止める必要が無い
        return


def _limit_hook(deadline: float, timeout: float, watch: _Watchdog):
    """要求（転送の先を含む）ごとに、期限の残りを httpx のタイムアウトへ掛け直し、接続を監視へ渡す。"""

    def hook(request: httpx.Request) -> None:
        left = deadline - _now()
        if left <= 0:
            raise _expired(timeout)
        request.extensions["timeout"] = httpx.Timeout(left).as_dict()
        request.extensions["trace"] = watch.trace

    return hook


def _read(resp: httpx.Response, deadline: float, timeout: float, watch: _Watchdog) -> bytes:
    """最終の応答の本文を、期限の内で読み切る。2xx でなければ読まずに `FetchError`。

    受信のたびに本文を受け取り（量で溜めない）、受け取るたびに期限を確かめる。受信を待っている間に
    期限が来たら `_Watchdog` が待ちを止める。止めた受信が本文の終わりと読まれても、途中までの本文を返さない。
    """
    if not resp.is_success:
        raise FetchError(f"HTTP {resp.status_code}")
    parts: list[bytes] = []
    for chunk in resp.iter_bytes():
        parts.append(chunk)
        if _now() > deadline:
            raise _expired(timeout)
    if watch.fired:
        raise _expired(timeout)
    return b"".join(parts)


def _follow(client: httpx.Client, request: httpx.Request, deadline: float, timeout: float, watch: _Watchdog) -> bytes:
    """転送を追い、最終の応答の本文を返す。転送の応答は本文を読まずに閉じる。"""
    for _ in range(client.max_redirects + 1):
        with contextlib.closing(client.send(request, stream=True)) as resp:
            if resp.next_request is None:
                return _read(resp, deadline, timeout, watch)
            request = resp.next_request
    raise FetchError("転送が多すぎる")


def fetch_body(url: str, timeout: float, transport: httpx.BaseTransport | None = None) -> bytes:
    """URL の本文を取得する。転送は追い、上限の秒数は転送の前後で通しで数える。

    上限は接続・応答の頭の受信・本文の受信のすべてに効く。最終の応答が 2xx なら本文の全部を返す。
    上限を越えた・2xx でない・転送が多すぎる・URL が不正・接続できないときは
    `FetchError`（理由の日本語 1 行）を送出する。
    """
    deadline = _now() + timeout
    watch = _Watchdog(timeout)
    hooks = {"request": [_limit_hook(deadline, timeout, watch)]}
    try:
        with httpx.Client(transport=transport, timeout=timeout, event_hooks=hooks, limits=_NO_KEEPALIVE) as client:
            return _follow(client, client.build_request("GET", url), deadline, timeout, watch)
    except httpx.TimeoutException as e:
        raise _expired(timeout) from e
    except httpx.InvalidURL as e:
        raise FetchError("URL が不正") from e
    except httpx.RequestError as e:
        if watch.fired:
            raise _expired(timeout) from e
        raise FetchError(f"接続できない（{type(e).__name__}）") from e
    finally:
        watch.stop()
