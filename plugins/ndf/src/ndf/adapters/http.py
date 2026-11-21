"""HTTP のアダプター。HTTP は httpx だけが行い、urllib を使わない。

- `post_json` は JSON を 1 回 POST し、応答の JSON のオブジェクトを返す。失敗は `PostError` で、打ち直して通りうるか
  （`retryable`）を区別する
- `fetch_body` は URL の本文を、1 件あたりの総経過時間の上限の内で取得する。失敗は `FetchError`
- `request_json` は 1 回の要求を指定のメソッドで送り、`(状態, JSON の object)` を返す。転送を追わず、環境変数のプロキシに従い、
  接続の前から受信の終わりまでを 1 つの期限（`DeadlineBackend`）で縛る

上限の秒数は呼ぶ側が上限の設定（`contracts/limits.json`）から引いて渡す。テストは `transport`（`request_json` は `RequestOptions.transport`）に
`httpx.MockTransport` を渡し、ネットワークへ出ない。
"""

from __future__ import annotations

import contextlib
import json
import socket
import ssl
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpcore
import httpx
from httpcore._backends.sync import SyncStream, TLSinTLSStream
from httpcore._exceptions import map_exceptions
from httpx._utils import URLPattern, get_environment_proxies  # httpx と同じ環境変数の読み方と当て方を使う

# 単調時計。テストが差し替える
_now = time.monotonic


class FetchError(Exception):
    """本文を取得できなかった。`str(e)` が理由の日本語 1 行。"""


class PostError(Exception):
    """JSON を送れなかった・応答が使えなかった。`str(e)` が理由の日本語 1 行。

    `retryable` が真なら、同じ要求を打ち直して通りうる（接続の失敗・上限の時間・429・5xx・Slack の `ratelimited`）。
    偽なら打ち直しても同じ失敗になる（URL の誤り・トークンの誤り・応答の形の誤り）。
    `retry_after` は応答の `Retry-After`（秒）で、打ち直すまでに少なくとも待つ秒。無ければ `None`。
    """

    def __init__(self, reason: str, retryable: bool, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.retryable = retryable
        self.retry_after = retry_after


# 失敗の理由の文（post_json と fetch_body が同じ文を返す）
_BAD_URL = "URL が不正"


def _timeout_reason(timeout: float) -> str:
    return f"{timeout:g} 秒を越えた"


def _connect_reason(e: Exception) -> str:
    return f"接続できない（{type(e).__name__}）"


def _status_retryable(status: int) -> bool:
    return status == httpx.codes.TOO_MANY_REQUESTS or status >= httpx.codes.INTERNAL_SERVER_ERROR


def _retry_after(resp: httpx.Response) -> float | None:
    """`Retry-After` の秒（delay-seconds の形）を返す。無い・秒の形（0 以上の整数）でなければ `None`。"""
    raw = resp.headers.get("Retry-After")
    if raw is None or not raw.strip().isdigit():
        return None
    return float(raw.strip())


def post_json(
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """JSON を 1 回 POST し、応答が 2xx で JSON のオブジェクトならその辞書を返す。ほかは `PostError`。"""
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            resp = client.post(url, json=body, headers=headers)
    except (httpx.InvalidURL, httpx.UnsupportedProtocol) as e:
        raise PostError(_BAD_URL, retryable=False) from e
    except httpx.TimeoutException as e:
        raise PostError(_timeout_reason(timeout), retryable=True) from e
    except httpx.TransportError as e:
        raise PostError(_connect_reason(e), retryable=True) from e
    return _json_object(resp)


def _json_object(resp: httpx.Response) -> dict[str, Any]:
    """応答が 2xx で JSON のオブジェクトならその辞書を返す。ほかは `PostError`。"""
    if not resp.is_success:
        raise PostError(f"HTTP {resp.status_code}", retryable=_status_retryable(resp.status_code), retry_after=_retry_after(resp))
    try:
        data = resp.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise PostError("応答が JSON でない", retryable=False) from e
    if not isinstance(data, dict):
        raise PostError("応答が JSON のオブジェクトでない", retryable=False)
    return data


# 要求ごとに新しく接続を張り、期限の監視が今の接続を必ず受け取れるようにする
_NO_KEEPALIVE = httpx.Limits(max_keepalive_connections=0)


def _expired(timeout: float) -> FetchError:
    return FetchError(_timeout_reason(timeout))


# 監視の対象にする接続を返す httpcore の trace の事象（前置きは "connection." や "proxy." など）
_WATCHED_EVENTS = (".connect_tcp.complete", ".start_tls.complete")


class _Watchdog:
    """期限に、待っている受信（応答の頭と本文）を止める。

    httpcore は受信の待ちの秒数を要求ごと・本文の読み始めに引き、以後の受信はその秒数で待つため、少しずつ
    届き続ける応答では総経過時間の上限が効かない。そこで取得の全体に 1 つの期限を掛け、期限に別のスレッドから
    今の接続を止め（`shutdown`）、待っている受信を失敗か終わりで戻す。接続は張るたびと TLS 化のたびに
    httpcore の `trace` で受け取る。止めた後の受信の終わりは本文の正常な終わりと読まれることがある（長さの無い応答）ため、
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
        """httpcore の `trace` の拡張。TCP の接続を張り終えたら、その接続を監視の対象にする。

        HTTPS では続く TLS 化が元の接続を手放し（記述子が -1 になる）、TLS の接続を新しく返すため、
        TLS 化を終えたらその接続へ監視の対象を移す。
        """
        if not event.endswith(_WATCHED_EVENTS):
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


@dataclass(frozen=True)
class _Fetch:
    """取得 1 回の期限。転送の前後で同じ 1 つを使う。

    `deadline` は単調時計（`_now`）の絶対時刻、`timeout` は上限の秒（理由の文に使う）、`watch` はその期限の監視。
    """

    deadline: float
    timeout: float
    watch: _Watchdog


def _limit_hook(fetch: _Fetch):
    """要求（転送の先を含む）ごとに、期限の残りを httpx のタイムアウトへ掛け直し、接続を監視へ渡す。"""

    def hook(request: httpx.Request) -> None:
        left = fetch.deadline - _now()
        if left <= 0:
            raise _expired(fetch.timeout)
        request.extensions["timeout"] = httpx.Timeout(left).as_dict()
        request.extensions["trace"] = fetch.watch.trace

    return hook


def _read(resp: httpx.Response, fetch: _Fetch) -> bytes:
    """最終の応答の本文を、期限の内で読み切る。2xx でなければ読まずに `FetchError`。

    受信のたびに本文を受け取り（量で溜めない）、受け取るたびに期限を確かめる。受信を待っている間に
    期限が来たら `_Watchdog` が待ちを止める。止めた受信が本文の終わりと読まれても、途中までの本文を返さない。
    """
    if not resp.is_success:
        raise FetchError(f"HTTP {resp.status_code}")
    parts: list[bytes] = []
    for chunk in resp.iter_bytes():
        parts.append(chunk)
        if _now() > fetch.deadline:
            raise _expired(fetch.timeout)
    if fetch.watch.fired:
        raise _expired(fetch.timeout)
    return b"".join(parts)


def _follow(client: httpx.Client, request: httpx.Request, fetch: _Fetch) -> bytes:
    """転送を追い、最終の応答の本文を返す。転送の応答は本文を読まずに閉じる。"""
    for _ in range(client.max_redirects + 1):
        with contextlib.closing(client.send(request, stream=True)) as resp:
            if resp.next_request is None:
                return _read(resp, fetch)
            request = resp.next_request
    raise FetchError("転送が多すぎる")


def fetch_body(url: str, timeout: float, transport: httpx.BaseTransport | None = None) -> bytes:
    """URL の本文を取得する。転送は追い、上限の秒数は転送の前後で通しで数える。

    上限は接続・応答の頭の受信・本文の受信のすべてに効く。最終の応答が 2xx なら本文の全部を返す。
    上限を越えた・2xx でない・転送が多すぎる・URL が不正・接続できないときは
    `FetchError`（理由の日本語 1 行）を送出する。
    """
    fetch = _Fetch(deadline=_now() + timeout, timeout=timeout, watch=_Watchdog(timeout))
    hooks = {"request": [_limit_hook(fetch)]}
    try:
        with httpx.Client(transport=transport, timeout=timeout, event_hooks=hooks, limits=_NO_KEEPALIVE) as client:
            return _follow(client, client.build_request("GET", url), fetch)
    except httpx.TimeoutException as e:
        raise _expired(timeout) from e
    except httpx.InvalidURL as e:
        raise FetchError(_BAD_URL) from e
    except httpx.RequestError as e:
        if fetch.watch.fired:
            raise _expired(timeout) from e
        raise FetchError(_connect_reason(e)) from e
    finally:
        fetch.watch.stop()


# ---- request_json ----

_BODY_MAX = 1024 * 1024  # request_json が読む本文の上限（バイト）
_USER_AGENT = "ndf-claude-accounts"

Resolve = Callable[..., Any]
Connect = Callable[[tuple[Any, ...], float], socket.socket]


class _NotSent(Exception):
    """`before_send` が送らない理由を返した。"""


def _left(deadline: float, error: type[Exception]) -> float:
    """期限までの残りの秒。残りが無ければ `error`（httpcore の例外。httpx が同じ名前の例外へ写す）。"""
    left = deadline - time.monotonic()
    if left <= 0:
        raise error("期限に達した")
    return left


def _clamp(timeout: float | None, left: float) -> float:
    return left if timeout is None else min(timeout, left)


def _connect(info: tuple[Any, ...], timeout: float) -> socket.socket:
    """アドレス 1 つへ、`timeout` 秒の内に接続する。"""
    family, kind, proto, _, addr = info
    sock = socket.socket(family, kind, proto)
    try:
        sock.settimeout(timeout)
        sock.connect(addr)
    except BaseException:
        sock.close()
        raise
    return sock


class _DeadlineSocket:
    """TLS の中の TLS（HTTPS のプロキシ越しの HTTPS）の下の接続。ソケットの I/O ごとに、待ちを期限までの残りに縮める。

    httpcore の `TLSinTLSStream` は 1 回のハンドシェイク・読み書きの中で、最初に掛けた 1 つの秒のまま何度も
    `recv`・`sendall` を打つ。この層は `settimeout` の秒を覚え、I/O のたびにその秒と残りの短い方を掛け直す。
    残りが無ければ `TimeoutError`（httpcore が同じ名前のタイムアウトへ写す）。
    """

    def __init__(self, sock: socket.socket, deadline: float) -> None:
        self._sock = sock
        self._deadline = deadline
        self._timeout: float | None = None

    def settimeout(self, timeout: float | None) -> None:
        self._timeout = timeout

    def _arm(self) -> None:
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("期限に達した")
        self._sock.settimeout(_clamp(self._timeout, left))

    def recv(self, max_bytes: int) -> bytes:
        self._arm()
        return self._sock.recv(max_bytes)

    def sendall(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            self._arm()
            view = view[self._sock.send(view) :]

    def close(self) -> None:
        self._sock.close()

    def fileno(self) -> int:
        return self._sock.fileno()

    def getsockname(self) -> Any:
        return self._sock.getsockname()

    def getpeername(self) -> Any:
        return self._sock.getpeername()


class _DeadlineStream(httpcore.NetworkStream):
    """ソケット操作ごとに、待ちを期限までの残りに縮める接続。"""

    def __init__(self, inner: httpcore.NetworkStream, deadline: float) -> None:
        self._inner = inner
        self._deadline = deadline

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._inner.read(max_bytes, _clamp(timeout, _left(self._deadline, httpcore.ReadTimeout)))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        """送る。httpcore の `SyncStream.write` は部分送信のたびに同じ秒を掛け直すので使わず、送信ごとに残りを引き直す。"""
        sock = self._inner.get_extra_info("socket") if isinstance(self._inner, SyncStream) else None
        if not isinstance(sock, socket.socket):
            # TLS の中の TLS: 下の接続（`_DeadlineSocket`）が I/O ごとに残りへ縮める
            self._inner.write(buffer, _clamp(timeout, _left(self._deadline, httpcore.WriteTimeout)))
            return
        view = memoryview(buffer)
        try:
            while view:
                sock.settimeout(_clamp(timeout, _left(self._deadline, httpcore.WriteTimeout)))
                view = view[sock.send(view) :]
        except TimeoutError as e:
            raise httpcore.WriteTimeout(str(e)) from e
        except OSError as e:
            raise httpcore.WriteError(str(e)) from e

    def close(self) -> None:
        self._inner.close()

    def start_tls(
        self, ssl_context: ssl.SSLContext, server_hostname: str | None = None, timeout: float | None = None
    ) -> httpcore.NetworkStream:
        left = _clamp(timeout, _left(self._deadline, httpcore.ConnectTimeout))
        sock = self._inner.get_extra_info("socket") if isinstance(self._inner, SyncStream) else None
        if not isinstance(sock, ssl.SSLSocket):
            return _DeadlineStream(self._inner.start_tls(ssl_context, server_hostname, left), self._deadline)
        # TLS の中の TLS: httpcore の `SyncStream.start_tls` と同じ `TLSinTLSStream` を、期限の層を下に挟んで作る
        with map_exceptions({socket.timeout: httpcore.ConnectTimeout, OSError: httpcore.ConnectError}):
            try:
                inner = TLSinTLSStream(_DeadlineSocket(sock, self._deadline), ssl_context, server_hostname, left)  # type: ignore[arg-type]
            except BaseException:
                self._inner.close()
                raise
        return _DeadlineStream(inner, self._deadline)

    def get_extra_info(self, info: str) -> Any:
        return self._inner.get_extra_info(info)


class DeadlineBackend(httpcore.NetworkBackend):
    """名前解決・接続・TLS・送受信のすべてを、1 つの期限（`time.monotonic` の絶対時刻）で縛る httpcore の転送の層。

    `socket.create_connection` は名前解決にタイムアウトが効かず、複数のアドレスへ同じタイムアウトを毎回当てるため使わない。
    名前解決は daemon のスレッドで打ち、期限までの残りだけ待つ。アドレスは順に、その時点の残りで試す。
    `resolve`（`socket.getaddrinfo` の形）と `connect`（アドレスと秒を受けてソケットを返す）はテストが差し替える。
    """

    def __init__(self, deadline: float, resolve: Resolve = socket.getaddrinfo, connect: Connect | None = None) -> None:
        self._deadline = deadline
        self._resolve = resolve
        self._connect = connect or _connect

    def _addresses(self, host: str, port: int) -> list[tuple[Any, ...]]:
        box: dict[str, Any] = {}
        done = threading.Event()

        def run() -> None:
            try:
                box["infos"] = self._resolve(host, port, 0, socket.SOCK_STREAM)
            except OSError as e:
                box["error"] = e
            done.set()

        threading.Thread(target=run, daemon=True).start()
        if not done.wait(_left(self._deadline, httpcore.ConnectTimeout)):
            raise httpcore.ConnectTimeout("名前解決が期限までに返らない")
        if "error" in box:
            raise httpcore.ConnectError(str(box["error"]))
        return list(box["infos"])

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        last: Exception = httpcore.ConnectError("接続先のアドレスが無い")
        for info in self._addresses(host, port):
            left = _clamp(timeout, _left(self._deadline, httpcore.ConnectTimeout))
            try:
                sock = self._connect(info, left)
            except OSError as e:
                last = httpcore.ConnectTimeout(str(e)) if isinstance(e, TimeoutError) else httpcore.ConnectError(str(e))
                continue
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return _DeadlineStream(SyncStream(sock), self._deadline)
        raise last

    def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options: Any = None) -> httpcore.NetworkStream:
        raise httpcore.ConnectError("UNIX ソケットへは接続しない")

    def sleep(self, seconds: float) -> None:
        time.sleep(min(seconds, _left(self._deadline, httpcore.PoolTimeout)))


def _deadline_transport(deadline: float, proxy: str | None = None) -> httpx.HTTPTransport:
    """期限の層（`DeadlineBackend`）を通す httpx の転送。`proxy` があればそのプロキシを経由する。

    httpx は転送の層を差し替える口を持たないので、作った接続の溜まり（プロキシの溜まりを含む）の層を替える。
    プロキシへの接続・CONNECT・TLS・送受信も同じ層を通るので、同じ期限で縛られる。
    """
    t = httpx.HTTPTransport(proxy=proxy, limits=_NO_KEEPALIVE)
    t._pool._network_backend = DeadlineBackend(deadline)  # noqa: SLF001 httpx は network_backend を渡す口を持たない
    return t


def _env_proxy(url: httpx.URL) -> str | None:
    """環境変数（`HTTPS_PROXY`・`HTTP_PROXY`・`ALL_PROXY`・`NO_PROXY`）で `url` に当たるプロキシ。直接つなぐなら None。

    httpx は `transport` を渡すと環境変数のプロキシを読まないので、httpx と同じ読み方（`get_environment_proxies`）と
    同じ当て方（`URLPattern` の優先の順で最初に合ったもの）で、接続先 1 つの設定だけを選ぶ。`NO_PROXY` に当たれば None。
    """
    found = get_environment_proxies()
    for pattern, proxy in sorted(((URLPattern(k), v) for k, v in found.items()), key=lambda kv: kv[0]):
        if pattern.matches(url):
            return proxy
    return None


@dataclass(frozen=True)
class RequestOptions:
    """`request_json` の任意の指定。`body` は JSON で送る本文、`before_send` は接続の前に呼ぶ送信前の判定、
    `transport` はテストが渡す httpx の転送（期限の層とプロキシを通らない）。"""

    body: dict[str, Any] | None = None
    before_send: Callable[[], str | None] | None = None
    transport: httpx.BaseTransport | None = None


_NO_OPTIONS = RequestOptions()


def request_json(
    method: str,
    url: str,
    headers: Mapping[str, str],
    timeout: float,
    options: RequestOptions = _NO_OPTIONS,
) -> tuple[int, dict[str, Any] | None]:
    """1 回の要求を `method`（`GET`・`POST` など）で送り、`(状態, JSON の object)` を返す。`options.body` があれば JSON で送る。

    `timeout` は呼んだ時刻からの往復全体の期限。環境変数のプロキシ（`HTTPS_PROXY`・`NO_PROXY` など）に従い、
    プロキシ越しの往復も同じ期限で縛る。転送は追わない（3xx はその状態を返し、転送先へ見出しを送らない）。
    接続先に当たるプロキシの設定だけを組み（`_env_proxy`）、その設定が使えなければ（読めない URL を含む）直接つながず `(0, None)`。
    期限に達した・接続・URL の失敗・見出しに送れない値がある・本文が 1 MiB を超えたときは `(0, None)`、
    本文が読めない・JSON の object でなければ `(状態, None)`。
    `options.before_send` は接続の前に呼び、文字列（送らない理由）が返れば送らずに `(-1, None)`。
    プロキシの設定が使えないときも呼び、理由が返れば `(0, None)` より `(-1, None)` を優先する。
    `options.transport` を渡すテストは期限の層とプロキシを通らない。
    """
    deadline = time.monotonic() + timeout
    before_send = options.before_send

    def hook(_request: httpx.Request) -> None:
        if before_send is not None and (why := before_send()) is not None:
            raise _NotSent(why)

    try:
        hdrs = httpx.Headers(headers)
    except UnicodeEncodeError:
        return 0, None
    if "User-Agent" not in hdrs:
        hdrs["User-Agent"] = _USER_AGENT
    transport = options.transport
    if transport is None:
        try:
            transport = _deadline_transport(deadline, _env_proxy(httpx.URL(url)))
        except (ImportError, ValueError, httpx.InvalidURL):
            # 接続先に当たるプロキシの設定が使えない（socksio の無い SOCKS・対応しない形・ポートの誤りなど読めない URL）。直接つながず通信の失敗にする。
            # 送らない理由（停止など）は通信の失敗より優先するので、ここでも送信前の判定を通す
            return (-1, None) if before_send is not None and before_send() is not None else (0, None)
    try:
        with (
            httpx.Client(transport=transport, timeout=timeout, follow_redirects=False, event_hooks={"request": [hook]}) as client,
            client.stream(method, url, headers=hdrs, json=options.body) as resp,
        ):
            raw = _read_capped(resp)
    except _NotSent:
        return -1, None
    except (httpx.RequestError, httpx.InvalidURL, UnicodeEncodeError):
        return 0, None
    if raw is None:
        return 0, None
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        # 読めない本文（JSON の誤り・UTF-8 でない・整数の桁数の上限を超える数・深すぎる入れ子）
        return resp.status_code, None
    return resp.status_code, data if isinstance(data, dict) else None


def _read_capped(resp: httpx.Response) -> bytes | None:
    """本文を `_BODY_MAX` まで読む。超えれば None。"""
    parts: list[bytes] = []
    size = 0
    for chunk in resp.iter_bytes():
        size += len(chunk)
        if size > _BODY_MAX:
            return None
        parts.append(chunk)
    return b"".join(parts)
