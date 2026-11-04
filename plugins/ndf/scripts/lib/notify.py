"""Slack の呼び出し・`.env` の読み取りの包み（#1142 の決定 19・種類 15・16）。

slack_sdk・python-dotenv を呼ぶのはこのモジュールだけである。`urllib.request` を import するのも
このモジュールだけにする（構造チェックの I14）。

- `slack_call(method, payload)`: Slack の Web API を 1 回呼ぶ。`ok` が偽・通信の失敗は None を返し、理由を
  `on_error` へ渡す。宛先は `NDF_SLACK_API_BASE`（既定 `https://slack.com`）、鍵は `SLACK_BOT_TOKEN`
- `load_env_upward(cwd, fallback)`: `.env` を `cwd` から git のトップ（`.git` のあるディレクトリ）まで上へ探し、
  無ければ `fallback` から探す。見つけた 1 本を読み、**すでにある環境変数は上書きしない**

使う側は `deps.require("notify")` を先に呼ぶ。slack_sdk は呼ぶときに import する（hook の経路の `.env` の読み取りに
載せない。#1142 の決定 20）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from dotenv import dotenv_values

SLACK_TIMEOUT = 10


def slack_call(
    method: str,
    payload: dict,
    token: str | None = None,
    base_url: str | None = None,
    timeout: int = SLACK_TIMEOUT,
    on_error: Callable[[str, str], None] | None = None,
) -> dict | None:
    """Slack の Web API（`chat.postMessage` など）を 1 回呼び、応答の辞書を返す。失敗は None。"""
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError, SlackClientError

    base = (base_url or os.environ.get("NDF_SLACK_API_BASE") or "https://slack.com").rstrip("/")
    client = WebClient(
        token=token if token is not None else os.environ.get("SLACK_BOT_TOKEN", ""), base_url=f"{base}/api/", timeout=timeout
    )
    try:
        data = client.api_call(method, json=payload).data
    except SlackApiError as exc:
        if on_error is not None:
            on_error(method, str(exc.response.get("error", "not ok")))
        return None
    except (SlackClientError, OSError, ValueError) as exc:
        if on_error is not None:
            on_error(method, type(exc).__name__)
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        if on_error is not None:
            on_error(method, str(data.get("error") if isinstance(data, dict) else "not ok"))
        return None
    return data


def find_env_file(start: os.PathLike[str] | str) -> Path | None:
    """`start` から上へ `.env` を探す。`.git` のあるディレクトリ（git のトップ）か根で止まる。"""
    cur = Path(start)
    while True:
        if (cur / ".env").is_file():
            return cur / ".env"
        if (cur / ".git").exists() or cur.parent == cur:
            return None
        cur = cur.parent


def load_env_upward(cwd: os.PathLike[str] | str, fallback: os.PathLike[str] | str | None = None) -> Path | None:
    """`.env` を 1 本見つけて環境変数へ読み込み（既存の値は残す）、読んだファイルを返す。無ければ None。"""
    for start in (cwd, fallback):
        if start is None:
            continue
        try:
            found = find_env_file(start)
        except OSError:
            continue
        if found is None:
            continue
        for key, value in dotenv_values(found).items():
            if key and value is not None:
                os.environ.setdefault(key, value)
        return found
    return None
