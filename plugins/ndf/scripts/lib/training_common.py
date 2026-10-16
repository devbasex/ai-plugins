"""学習の設定を読み書きする腐敗防止層の、ランタイムに依らない部品（#1597）。

読んだ結果の形（`Reading`）・理由の文言・試験用の宛先の受け入れ・`PATCH` の応答の判定を持つ。
claude 固有の層（`claude_training`）と codex 固有の層（`codex_training`）が使う。
"""

from __future__ import annotations

import urllib.parse
from dataclasses import asdict, dataclass

LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost")

NETWORK = "通信の失敗"
NOT_LOCAL = "試験用の宛先が手元でない"


@dataclass(frozen=True)
class Reading:
    """読んだ結果。`training` は true / false / null で、真偽値を読めなかったら必ず null（I1）。"""

    runtime: str
    training: bool | None
    source: str | None = None
    updated_at: str | None = None
    reason: str | None = None
    config_dir: str | None = None

    def to_item(self) -> dict:
        d = asdict(self)
        return {k: d[k] for k in ("runtime", "config_dir", "training", "source", "updated_at", "reason")}


def unread(reason: str, runtime: str = "claude", config_dir: str | None = None) -> Reading:
    return Reading(runtime, None, reason=reason, config_dir=config_dir)


def http_reason(status: int) -> str:
    return NETWORK if status == 0 else f"HTTP {status}"


def target(env, var: str, default: str) -> str | None:
    """宛先。試験用の差し替えは、ホストが手元の http(s) の URL だけを受ける。受けられなければ None（I7）。"""
    raw = env.get(var)
    if not raw:
        return default
    try:
        u = urllib.parse.urlsplit(raw)
        host = (u.hostname or "").lower()
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or "@" in u.netloc or host not in LOCAL_HOSTS:
        return None
    return raw


def patch_reason(status: int) -> str | None:
    """`PATCH` の応答の状態。2xx なら None、そうでなければ理由。"""
    return None if 200 <= status < 300 else http_reason(status)
