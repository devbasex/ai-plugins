"""HTTP で送る・取るアダプター。HTTP は httpx だけが行い、urllib を使わない。

- `post_json` は JSON を 1 回 POST し、応答の JSON のオブジェクトを返す。失敗は `None` で、例外を外へ出さない
- `fetch_body` は URL の本文を、1 件あたりの総経過時間の上限の内で取得する。失敗は `FetchError`

上限の秒数は呼ぶ側が上限の宣言（`contracts/limits.json`）から引いて渡す。テストは `transport` に
`httpx.MockTransport` を渡し、ネットワークへ出ない。
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

# 本文を読む 1 回の量（バイト）
CHUNK_BYTES = 65536

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
    except httpx.HTTPError:  # noqa: NDF401 ネットワーク: 送れなければ呼ぶ側が判定を見送る
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


def _read(resp: httpx.Response, deadline: float, timeout: float) -> bytes:
    """2xx の応答の本文を、期限の内で読み切る。"""
    parts: list[bytes] = []
    for chunk in resp.iter_bytes(CHUNK_BYTES):
        parts.append(chunk)
        if _now() > deadline:
            raise FetchError(f"{_seconds(timeout)} 秒を越えた")
    return b"".join(parts)


def fetch_body(url: str, timeout: float, transport: httpx.BaseTransport | None = None) -> bytes:
    """URL の本文を取得する。転送は追い、上限の秒数は転送の前後で通しで数える。

    最終の応答が 2xx なら本文の全部を返す。上限を越えた・2xx でない・転送が多すぎる・接続できないときは
    `FetchError`（理由の日本語 1 行）を送出する。
    """
    deadline = _now() + timeout
    hooks = {"request": [_limit_hook(deadline, timeout)]}
    try:
        with (
            httpx.Client(transport=transport, follow_redirects=True, timeout=timeout, event_hooks=hooks) as client,
            client.stream("GET", url) as resp,
        ):
            if not resp.is_success:
                raise FetchError(f"HTTP {resp.status_code}")
            return _read(resp, deadline, timeout)
    except httpx.TooManyRedirects as e:
        raise FetchError("転送が多すぎる") from e
    except httpx.TimeoutException as e:
        raise FetchError(f"{_seconds(timeout)} 秒を越えた") from e
    except httpx.RequestError as e:
        raise FetchError(f"接続できない（{type(e).__name__}）") from e
