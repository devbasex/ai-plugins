"""claude アカウントの残量・トークンの更新の宛先と、利用上限の文言の読み（#1389）。

`lib/claude_accounts.py`（置き場と選び方）が呼ぶ。ここは置き場を知らず、1 回の要求と応答の形・文言の読みだけを持つ。

- 使用量の取得先 `GET /api/oauth/usage` は推論を呼ばない。公開の文書が無く形が変わりうるので、形が違えば
  残量不明（`error: shape`）として扱う（2026-09-28 に形を実測）
- トークンの更新は Claude Code 2.1.283 の本体と同じ宛先・同じ要求の形（JSON の `grant_type`・`refresh_token`・
  `client_id`・`scope`）で送る
- 通信は標準ライブラリの `urllib` で行う。ラッパーの環境（`relay_lib/runtime.py` の `GROUPS`）に HTTP の包み
  （`lib/notify.py` の httpx）を足さないためである（#1389 の決定 13）
- `NDF_ACCOUNT_USAGE_URL`・`NDF_ACCOUNT_TOKEN_URL` は試験用に宛先を差し替える
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import clock

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
HTTP_TIMEOUT = 10.0
REFRESH_REJECTED = frozenset({400, 401, 403})  # リフレッシュトークンが取り消されたと読む状態
KINDS = ("five_hour", "seven_day", "spend", "unknown")
# claude の古い形の上限の文言（`Claude AI usage limit reached|<解除の UNIX 時刻>`）と、`resets 3pm (UTC)` の形
LIMIT_EPOCH = re.compile(r"usage limit reached\|(\d{9,11})", re.I)
LIMIT_RESETS = re.compile(r"resets?(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)?(?:\s*\(([^)]+)\))?", re.I)


def iso_utc(t: float | None) -> str | None:
    return None if t is None else datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def epoch(s) -> float | None:
    """ISO 8601 の時刻を epoch 秒へ（タイムゾーンが無ければ UTC）。読めなければ None。"""
    d = clock.parse(s, naive="utc") if isinstance(s, str) else None
    return d.timestamp() if d is not None else None


# ---------------------------------------------------------------- 残量


@dataclass
class Usage:
    """最後に取得した残量。`five_hour`・`seven_day` は `{utilization, resets_at}`（空は読めなかった）。

    `scoped` はモデル別の週の枠 `[{model, utilization, resets_at}]`（`[]` は応答に無かった、None は読めなかった）、
    `spend` は支出の状態 `{percent, severity}`（None は読めなかった）。`spend_limit_reached` は応答の生の値である。"""

    five_hour: dict | None = None
    seven_day: dict | None = None
    spend_limit_reached: bool | None = None
    fetched_at: float = 0.0
    error: str | None = None
    scoped: list | None = None
    spend: dict | None = None

    @classmethod
    def from_json(cls, d: dict | None) -> Usage | None:
        if not isinstance(d, dict):
            return None
        return cls(
            five_hour=d.get("five_hour") if isinstance(d.get("five_hour"), dict) else None,
            seven_day=d.get("seven_day") if isinstance(d.get("seven_day"), dict) else None,
            spend_limit_reached=d.get("spend_limit_reached") if isinstance(d.get("spend_limit_reached"), bool) else None,
            fetched_at=epoch(d.get("fetched_at")) or 0.0,
            error=d.get("error") if isinstance(d.get("error"), str) else None,
            scoped=_scoped_saved(d.get("scoped")),
            spend=d.get("spend") if isinstance(d.get("spend"), dict) else None,
        )

    def to_json(self) -> dict:
        return {
            "fetched_at": iso_utc(self.fetched_at),
            "five_hour": self.five_hour,
            "seven_day": self.seven_day,
            "scoped": self.scoped,
            "spend_limit_reached": self.spend_limit_reached,
            "spend": self.spend,
            "error": self.error,
        }

    def windows_known(self) -> bool:
        return self.error is None and (self.five_hour is not None or self.seven_day is not None)

    def windows(self) -> list[dict]:
        """使用率の読める枠（5 時間の枠・週の枠・モデル別の週の枠）。"""
        ws = [self.five_hour, self.seven_day, *(self.scoped or [])]
        return [w for w in ws if isinstance(w, dict) and isinstance(w.get("utilization"), (int, float))]

    def score(self) -> float | None:
        """使用率: 5 時間の枠・週の枠・モデル別の週の枠の使用率の最大（I5）。読めなければ None。"""
        if not self.windows_known():
            return None
        vals = [w["utilization"] for w in self.windows()]
        return float(max(vals)) if vals else None

    def spend_reached(self) -> bool | None:
        """支出上限に達したか（I4）: `spend_limit_reached`・`spend.percent` ≥ 100・`spend.severity` = critical のどれか。

        どれも読めなければ None。"""
        sp = self.spend or {}
        pct, sev = sp.get("percent"), sp.get("severity")
        if self.spend_limit_reached or (isinstance(pct, (int, float)) and pct >= 100) or sev == "critical":
            return True
        if self.spend_limit_reached is None and self.spend is None:
            return None
        return False

    def resets(self, key: str) -> float | None:
        w = getattr(self, key)
        return epoch(w.get("resets_at")) if isinstance(w, dict) else None

    def limited_until(self, now: float) -> float | None:
        """上限にあるならそのリセット時刻（支出上限で時刻が無ければ無限）。無ければ None。"""
        if not self.windows_known():
            return None
        if self.spend_reached():
            return math.inf
        until = [epoch(w.get("resets_at")) or math.inf for w in self.windows() if w["utilization"] >= 100]
        until = [t for t in until if t > now]
        return max(until) if until else None


def _scoped_one(x) -> dict | None:
    if not isinstance(x, dict) or not isinstance(x.get("utilization"), (int, float)):
        return None
    return _scoped_row(x.get("model"), x["utilization"], x.get("resets_at"))


def _scoped_row(model, util: float, resets) -> dict:
    """モデル別の週の枠の 1 行（保存の形）。文字列でない `model`・`resets_at` は None にする。"""
    return {
        "model": model if isinstance(model, str) else None,
        "utilization": float(util),
        "resets_at": resets if isinstance(resets, str) else None,
    }


def _scoped_saved(v) -> list | None:
    """保存した `scoped` を読む。配列でない・要素が崩れていれば None（読めなかった）。"""
    if not isinstance(v, list):
        return None
    out = [_scoped_one(x) for x in v]
    return None if any(x is None for x in out) else out


def _parse_scoped(limits) -> list | None:
    """応答の `limits[]` から `kind == "weekly_scoped"` を写す。キーが無ければ `[]`、崩れていれば None（I6）。"""
    if limits is None:
        return []
    if not isinstance(limits, list):
        return None
    out = [_parse_scoped_limit(x) for x in limits if isinstance(x, dict) and x.get("kind") == "weekly_scoped"]
    return None if any(x is None for x in out) else out


def _parse_scoped_limit(x: dict) -> dict | None:
    """`weekly_scoped` の要素 1 つを保存の形へ写す。`percent` が数でなければ None（崩れている）。"""
    if not isinstance(x.get("percent"), (int, float)):
        return None
    scope = x.get("scope") if isinstance(x.get("scope"), dict) else {}
    m = scope.get("model") if isinstance(scope.get("model"), dict) else {}
    model = m.get("display_name") if isinstance(m.get("display_name"), str) else m.get("id")
    return _scoped_row(model, x["percent"], x.get("resets_at"))


def _parse_spend(sp) -> dict | None:
    if not isinstance(sp, dict):
        return None
    pct, sev = sp.get("percent"), sp.get("severity")
    return {
        "percent": float(pct) if isinstance(pct, (int, float)) and not isinstance(pct, bool) else None,
        "severity": sev if isinstance(sev, str) else None,
    }


def parse_usage(d: dict | None, now: float) -> Usage:
    """取得先の応答を読む。`five_hour`・`seven_day` の形が違えば残量不明（`error: shape`）。

    `limits[]` と `spend` の崩れはその項目だけを読めなかった（None）とし、残量全体を不明にしない（I6）。"""

    def window(key: str) -> dict | None:
        w = d.get(key)
        if w is None:
            return None
        if not isinstance(w, dict) or not isinstance(w.get("utilization"), (int, float)):
            raise ValueError(key)
        return {"utilization": float(w["utilization"]), "resets_at": w.get("resets_at") if isinstance(w.get("resets_at"), str) else None}

    try:
        if not isinstance(d, dict):
            raise ValueError("body")
        five, seven = window("five_hour"), window("seven_day")
        if five is None and seven is None:
            raise ValueError("windows")
    except ValueError:
        return Usage(fetched_at=now, error="shape")
    ex = d.get("extra_usage")
    spend = ex.get("spend_limit_reached") if isinstance(ex, dict) and isinstance(ex.get("spend_limit_reached"), bool) else None
    return Usage(
        five_hour=five,
        seven_day=seven,
        spend_limit_reached=spend,
        fetched_at=now,
        scoped=_parse_scoped(d.get("limits")),
        spend=_parse_spend(d.get("spend")),
    )


# ---------------------------------------------------------------- 宛先


def new_request(url: str, headers: dict[str, str], method: str = "GET") -> urllib.request.Request:
    """`http_json` へ渡す要求。`urllib.request` を import するのをこのモジュールに留めるため、他のモジュールはこれで作る。"""
    return urllib.request.Request(url, method=method, headers=headers)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # 3xx を送り直さず HTTPError として返す
        return None


def http_json(
    req: urllib.request.Request, timeout: float, *, body: dict | None = None, follow_redirects: bool = True
) -> tuple[int, dict | None]:
    """(状態, JSON)。通信の失敗は (0, None)、JSON でなければ (状態, None)。

    `body` を渡すと JSON の本文として送る（方法は `req` の `method`）。`follow_redirects=False` なら 3xx を追わず、
    その状態コードを返す（トークンを転送先へ送らない）。"""
    if not req.has_header("User-agent"):
        req.add_header("User-Agent", "ndf-claude-accounts")
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    open_ = urllib.request.urlopen if follow_redirects else urllib.request.build_opener(_NoRedirect).open
    try:
        with open_(req, timeout=timeout) as f:
            status, raw = f.status, f.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, b""
    except (OSError, ValueError):
        return 0, None
    try:
        data = json.loads(raw)
    except ValueError:
        return status, None
    return status, data if isinstance(data, dict) else None


def get_usage(access_token: str, now: float) -> tuple[int, Usage]:
    """取得先を 1 回呼ぶ。(状態, 残量)。状態が 200 でなければ残量は `error` に理由を持つ。"""
    req = urllib.request.Request(
        os.environ.get("NDF_ACCOUNT_USAGE_URL") or USAGE_URL,
        headers={"Authorization": f"Bearer {access_token}", "anthropic-beta": "oauth-2025-04-20"},
    )
    status, d = http_json(req, HTTP_TIMEOUT)
    if status == 0:
        return 0, Usage(fetched_at=now, error="network")
    if status != 200:
        return status, Usage(fetched_at=now, error=f"http-{status}")
    return 200, parse_usage(d, now)


def refresh_oauth(o: dict, now: float, timeout: float = 30) -> tuple[str, dict | None]:
    """リフレッシュトークンで更新する。("ok", 新しい claudeAiOauth) / ("rejected", None) / ("error", None)。

    `timeout` は宛先の待ちの上限（秒）。"""
    body = {
        "grant_type": "refresh_token",
        "refresh_token": o.get("refreshToken") or "",
        "client_id": CLIENT_ID,
        "scope": " ".join(o.get("scopes") or []),
    }
    req = urllib.request.Request(
        os.environ.get("NDF_ACCOUNT_TOKEN_URL") or TOKEN_URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    status, d = http_json(req, timeout)
    # 取り消し（invalid_grant など）と分かる 400/401/403 だけを rejected にする。429・408・5xx・通信の失敗・
    # 形の崩れた 200 は一時的な失敗として error（今のトークンを使い、次回に再試行する）にする
    if status in REFRESH_REJECTED:
        return "rejected", None
    if status != 200 or not d or not isinstance(d.get("access_token"), str) or not isinstance(d.get("expires_in"), (int, float)):
        return "error", None
    new = dict(o, accessToken=d["access_token"], refreshToken=d.get("refresh_token") or o.get("refreshToken"))
    new["expiresAt"] = int((now + d["expires_in"]) * 1000)
    if isinstance(d.get("refresh_token_expires_in"), (int, float)):
        new["refreshTokenExpiresAt"] = int((now + d["refresh_token_expires_in"]) * 1000)
    if isinstance(d.get("scope"), str) and d["scope"]:
        new["scopes"] = d["scope"].split()
    return "ok", new


# ---------------------------------------------------------------- 上限の文言


def kind_of_text(text: str) -> str:
    """上限の文言の種類（`five_hour`・`seven_day`・`spend`・`unknown`）。"""
    t = (text or "").lower()
    if "spend limit" in t or "spending limit" in t:
        return "spend"
    if "session limit" in t or "5-hour" in t or "five_hour" in t:
        return "five_hour"
    if "weekly limit" in t or "seven_day" in t:
        return "seven_day"
    return "unknown"


def limit_reset_at(text: str, now: float | None = None) -> float | None:
    """上限の文言から解除の時刻（UNIX 時刻）を読む。読めなければ None。

    読む形: `usage limit reached|<UNIX 時刻>` と `resets 3pm (Asia/Tokyo)` / `resets at 15:30`。
    時刻だけの形は、今より後の最初のその時刻（時間帯が無ければ手元の時間帯）とする。
    """
    now = time.time() if now is None else now
    m = LIMIT_EPOCH.search(text or "")
    if m:
        return float(m.group(1))
    m = LIMIT_RESETS.search(text or "")
    if not m:
        return None
    hour, minute, ampm, zone = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower(), m.group(4)
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    try:
        tz = ZoneInfo(zone.strip()) if zone else None
    except (KeyError, ValueError):
        tz = None
    cur = datetime.fromtimestamp(now, tz) if tz else datetime.fromtimestamp(now).astimezone()
    at = cur.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at.timestamp() <= now:
        at += timedelta(days=1)
    return at.timestamp()
