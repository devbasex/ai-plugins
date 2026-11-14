"""HTTP で送る・取るアダプター。HTTP は httpx だけが行い、urllib を使わない。

- `post_json` は JSON を 1 回 POST し、応答の JSON のオブジェクトを返す。失敗は `None` で、例外を外へ出さない
- `fetch_body` は URL の本文を、1 件あたりの総経過時間の上限の内で取得する。失敗は `FetchError`

上限の秒数は呼ぶ側が上限の宣言（`contracts/limits.json`）から引いて渡す。テストは `transport` に
`httpx.MockTransport` を渡し、ネットワークへ出ない。
"""

from __future__ import annotations

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


def _limit_hook(deadline: float, timeout: float):
    """要求（転送の先を含む）ごとに、期限の残りを httpx のタイムアウトへ掛け直す。"""

    def hook(request: httpx.Request) -> None:
        left = deadline - _now()
        if left <= 0:
            raise FetchError(f"{_seconds(timeout)} 秒を越えた")
        request.extensions["timeout"] = httpx.Timeout(left).as_dict()

    return hook


class _Watchdog:
    """期限に、本文の読み取りの待ちを止める。

    httpcore は本文の読み始めに読み取りの待ちの秒数を 1 回だけ引き、以後の読み取りはその秒数で待つため、
    待ちの秒数を後から縮めても効かない。そこで期限に別のスレッドから接続の受信を止め（`shutdown`）、
    待っている読み取りを失敗で戻す。止めるのは本文を読み終える前だけで、読み終えた後の接続には触れない。
    """

    def __init__(self, resp: httpx.Response, seconds: float) -> None:
        self._resp = resp
        self._lock = threading.Lock()
        self._done = False
        self.fired = False
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True
        self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            if self._done:
                return
            self.fired = True
            _shutdown(self._resp)

    def stop(self) -> None:
        with self._lock:
            self._done = True
        self._timer.cancel()


def _shutdown(resp: httpx.Response) -> None:
    """応答の接続の受信と送信を止める。接続の無い応答（テストの transport）では何もしない。

    TLS の接続でも TLS の状態を別のスレッドから変えないよう、記述子を複製して `shutdown` だけを掛ける。
    """
    stream = resp.extensions.get("network_stream")
    sock = stream.get_extra_info("socket") if stream is not None else None
    if not isinstance(sock, socket.socket):
        return
    try:
        with socket.fromfd(sock.fileno(), sock.family, sock.type) as dup:
            dup.shutdown(socket.SHUT_RDWR)
    except OSError:  # noqa: NDF401 ネットワーク: 既に閉じた接続は止める必要が無い
        pass


def _read(resp: httpx.Response, deadline: float, timeout: float) -> bytes:
    """2xx の応答の本文を、期限の内で読み切る。

    受信のたびに本文を受け取り（量で溜めない）、受け取るたびに期限を確かめる。受信を待っている間に
    期限が来たら `_Watchdog` が待ちを止める。
    """
    watch = _Watchdog(resp, max(deadline - _now(), 0.0))
    parts: list[bytes] = []
    try:
        for chunk in resp.iter_bytes():
            parts.append(chunk)
            if _now() > deadline:
                raise FetchError(f"{_seconds(timeout)} 秒を越えた")
    except httpx.TransportError as e:
        if watch.fired:
            raise FetchError(f"{_seconds(timeout)} 秒を越えた") from e
        raise
    finally:
        watch.stop()
    return b"".join(parts)


def fetch_body(url: str, timeout: float, transport: httpx.BaseTransport | None = None) -> bytes:
    """URL の本文を取得する。転送は追い、上限の秒数は転送の前後で通しで数える。

    転送の応答は本文を読まずに閉じ、次の要求へ進む。最終の応答が 2xx なら本文の全部を返す。
    上限を越えた・2xx でない・転送が多すぎる・URL が不正・接続できないときは
    `FetchError`（理由の日本語 1 行）を送出する。
    """
    deadline = _now() + timeout
    hooks = {"request": [_limit_hook(deadline, timeout)]}
    try:
        with httpx.Client(transport=transport, timeout=timeout, event_hooks=hooks) as client:
            request = client.build_request("GET", url)
            for _ in range(client.max_redirects + 1):
                resp = client.send(request, stream=True)
                try:
                    if resp.next_request is None:
                        if not resp.is_success:
                            raise FetchError(f"HTTP {resp.status_code}")
                        return _read(resp, deadline, timeout)
                    request = resp.next_request
                finally:
                    resp.close()
            raise FetchError("転送が多すぎる")
    except httpx.TimeoutException as e:
        raise FetchError(f"{_seconds(timeout)} 秒を越えた") from e
    except httpx.InvalidURL as e:
        raise FetchError("URL が不正") from e
    except httpx.RequestError as e:
        raise FetchError(f"接続できない（{type(e).__name__}）") from e
