"""claude のアカウントの使用率の API と、トークンの更新のエンドポイント（決定 4）。

1 回の要求と応答の形だけを持ち、保存先・排他・取得の記録は持たない（`domains/relay/accounts.py` が持つ）。
使用率の API（`GET /api/oauth/usage`）は公開の文書が無く形が変わりうるので、形が違えば `error: shape` と
読めなかった項目の名前（`missing`）を返す。トークンの更新は Claude Code 本体と同じエンドポイント・同じ要求の形で送る。
通信は `http.request_json` で行い、転送を追わない。上限の秒は呼ぶ側が `contracts/limits.json` から渡す。

10 系の出所は `976513356^` の `plugins/ndf/scripts/lib/claude_usage.py`（`USAGE_SCOPE` は同じコミットの
`claude_accounts.py`）。テストは `transport` に `httpx.MockTransport` を渡す。
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from ndf.adapters import http

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
REFRESH_REJECTED = frozenset({400, 401, 403})  # リフレッシュトークンが取り消されたと読む状態
USAGE_SCOPE = "user:profile"  # 使用率の API が要るスコープ
_USAGE_BETA = "oauth-2025-04-20"
_BEARER = re.compile(r"[\x21-\x7e]+")  # Bearer の見出しで送れるトークン（空白と制御文字の無い ASCII）

Window = tuple[float, float]  # (使用率, resets_at の Unix 秒)
Scoped = tuple[str, float, float]  # (識別, 使用率, resets_at の Unix 秒)


@dataclass(frozen=True)
class Usage:
    """使用率の API の 1 回の取得の結果。

    `five_hour`・`seven_day` は窓か None（応答に無い・使用の無い窓）。`scoped` は `weekly_scoped` のモデル別の窓の並び
    （`[]` は応答に無い、None は崩れていた）。`spend_limit_reached` は真偽か None（応答に無い・崩れていた）。
    `error` は None か `network`・`http-<状態>`・`shape` か、`before_send` が返した理由。
    `missing` は `shape` のとき読めなかった項目の名前（`body`・`windows`・`five_hour`・`seven_day`）。
    """

    five_hour: Window | None = None
    seven_day: Window | None = None
    scoped: list[Scoped] | None = None
    spend_limit_reached: bool | None = None
    fetched_at: float = 0.0
    error: str | None = None
    missing: tuple[str, ...] = ()


def _number(v: Any) -> bool:
    """有限の数（真偽を除く）か。float に収まらない巨大な整数は数として扱わない。"""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:  # noqa: NDF401 ネットワーク: 応答の巨大な整数は数でない値として扱う
        return False


def _null(d: dict[str, Any], key: str) -> bool:
    """`key` が明示的な null か（キーの欠落は null にしない）。"""
    return key in d and d[key] is None


def sendable_token(v: Any) -> bool:
    """`Authorization: Bearer <値>` の見出しとして送れるアクセストークンか。"""
    return isinstance(v, str) and _BEARER.fullmatch(v) is not None


def _epoch(s: Any) -> float | None:
    """ISO 8601 の時刻を Unix 秒へ（`Z` は `+00:00`、時間帯が無ければ UTC）。文字列でない・読めない・有限でなければ None。"""
    if not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
        t = (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except (ValueError, OverflowError, OSError):  # noqa: NDF401 ネットワーク: 応答の読めない時刻は None で返す
        return None
    return t if math.isfinite(t) else None


class _Shape(Exception):
    """窓の形が違う。`args[0]` が項目の名前。"""


def _window(d: dict[str, Any], key: str) -> Window | None:
    """窓 1 つを読む。null と使用の無い窓（`utilization` が 0 で `resets_at` が明示的な null）は None、形が違えば `_Shape`。

    `resets_at` のキーが欠けた窓は使用の無い窓でなく、形の違い（`_Shape`）。
    """
    w = d.get(key)
    if w is None:
        return None
    if isinstance(w, dict) and _number(w.get("utilization")):
        at = _epoch(w.get("resets_at"))
        if at is not None:
            return float(w["utilization"]), at
        if w["utilization"] == 0 and _null(w, "resets_at"):
            return None
    raise _Shape(key)


def _parse_scoped(limits: Any) -> list[Scoped] | None:
    """`limits[]` の `kind == "weekly_scoped"` のモデル別の窓を読む。無ければ `[]`、崩れていれば・識別が重なれば None。"""
    if limits is None:
        return []
    if not isinstance(limits, list):
        return None
    out: list[Scoped] = []
    for x in limits:
        if not isinstance(x, dict) or x.get("kind") != "weekly_scoped":
            continue
        scope = x.get("scope") if isinstance(x.get("scope"), dict) else {}
        model = scope.get("model") if isinstance(scope.get("model"), dict) else {}
        ident = next((v for v in (model.get("id"), model.get("display_name")) if isinstance(v, str) and v), None)
        pct, at = x.get("percent"), _epoch(x.get("resets_at"))
        if _number(pct) and pct == 0 and _null(x, "resets_at"):
            continue
        if ident is None or not _number(pct) or at is None or any(o[0] == ident for o in out):
            return None
        out.append((ident, float(pct), at))
    return out


def parse_usage(d: dict[str, Any] | None, now: float) -> Usage:
    """使用率の API の応答を読む。窓の形が違えば `Usage(error="shape", missing=…)`。

    `limits[]` と `extra_usage.spend_limit_reached` の崩れはその項目だけを None にし、全体を `shape` にしない。
    `weekly_scoped` の要素で `resets_at` のキーが欠けたものは使用の無い枠でなく崩れで、`scoped` を None にする。
    """
    if not isinstance(d, dict):
        return Usage(fetched_at=now, error="shape", missing=("body",))
    if "five_hour" not in d and "seven_day" not in d:
        return Usage(fetched_at=now, error="shape", missing=("windows",))
    got: dict[str, Window | None] = {}
    missing: list[str] = []
    for key in ("five_hour", "seven_day"):
        try:
            got[key] = _window(d, key)
        except _Shape:
            missing.append(key)
    if missing:
        return Usage(fetched_at=now, error="shape", missing=tuple(missing))
    ex = d.get("extra_usage")
    spend = ex.get("spend_limit_reached") if isinstance(ex, dict) else None
    return Usage(
        five_hour=got["five_hour"],
        seven_day=got["seven_day"],
        scoped=_parse_scoped(d.get("limits")),
        spend_limit_reached=spend if isinstance(spend, bool) else None,
        fetched_at=now,
    )


def get_usage(
    access_token: str,
    now: float,
    timeout: float,
    before_send: Callable[[], str | None] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, Usage]:
    """使用率の API を 1 回 GET する（転送は追わない）。`(状態, Usage)`。

    アクセストークンが見出しで送れない値（`sendable_token` が偽）なら送らずに `(-1, Usage(error="token"))`。
    送らなかったときは `(-1, Usage(error=before_send の理由))`、通信の失敗は `(0, error="network")`、
    200 以外は `error="http-<状態>"`、200 は `parse_usage`。
    """
    if not sendable_token(access_token):
        return -1, Usage(fetched_at=now, error="token")
    held: list[str] = []

    def guard() -> str | None:
        why = before_send() if before_send is not None else None
        if why is not None:
            held.append(why)
        return why

    headers = {"Authorization": f"Bearer {access_token}", "anthropic-beta": _USAGE_BETA}
    status, d = http.request_json("GET", USAGE_URL, headers, timeout, before_send=guard, transport=transport)
    if status == -1:
        return -1, Usage(fetched_at=now, error=held[0])
    if status == 0:
        return 0, Usage(fetched_at=now, error="network")
    if status != 200:
        return status, Usage(fetched_at=now, error=f"http-{status}")
    return 200, parse_usage(d, now)


def _positive(v: Any) -> bool:
    return _number(v) and v > 0


def _expiry_ms(now: float, seconds: Any) -> int | None:
    """`now` から `seconds` 秒後の時刻（ミリ秒）。正の有限の数でない・ミリ秒へ変換できなければ None。"""
    if not _positive(seconds):
        return None
    try:
        return int((now + seconds) * 1000)
    except (OverflowError, ValueError):  # noqa: NDF401 ネットワーク: 変換できない期限は無いものとして扱う
        return None


def refresh_oauth(
    o: Mapping[str, Any], now: float, timeout: float, transport: httpx.BaseTransport | None = None
) -> tuple[str, dict[str, Any] | None]:
    """リフレッシュトークンで 1 回更新する。`("ok", 新しい claudeAiOauth)`・`("rejected", None)`・`("error", None)`。

    `now` は POST の直前の時刻で `expiresAt` の起点。400・401・403 は取り消し（`rejected`）、ほか（429・5xx・
    通信の失敗・必須の条件に外れた 200）は一時的な失敗（`error`）で、呼ぶ側は認証ファイルを書かない。
    アクセストークンは見出しで送れる値（`sendable_token`）、`expires_in` はミリ秒の期限へ変換できる値だけを受ける。
    """
    token, scopes = o.get("refreshToken"), o.get("scopes")
    body = {
        "grant_type": "refresh_token",
        "refresh_token": token if isinstance(token, str) else "",
        "client_id": CLIENT_ID,
        "scope": " ".join(s for s in scopes if isinstance(s, str)) if isinstance(scopes, list) else "",
    }
    status, d = http.request_json("POST", TOKEN_URL, {}, timeout, body=body, transport=transport)
    if status in REFRESH_REJECTED:
        return "rejected", None
    if status != 200 or d is None:
        return "error", None
    access, expires, refresh = d.get("access_token"), d.get("expires_in"), d.get("refresh_token")
    expires_at = _expiry_ms(now, expires)
    if not sendable_token(access) or expires_at is None:
        return "error", None
    if refresh is not None and not (isinstance(refresh, str) and refresh):
        return "error", None
    new = dict(o, accessToken=access, refreshToken=refresh if refresh is not None else o.get("refreshToken"))
    new["expiresAt"] = expires_at
    refresh_at = _expiry_ms(now, d.get("refresh_token_expires_in"))
    if refresh_at is not None:
        new["refreshTokenExpiresAt"] = refresh_at
    if isinstance(d.get("scope"), str) and d["scope"]:
        new["scopes"] = d["scope"].split()
    return "ok", new
