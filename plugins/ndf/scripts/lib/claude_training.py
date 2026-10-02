"""claude のアカウントの学習の設定（「Help improve our AI models」）を読み書きする腐敗防止層（#1597）。

Claude Code と同じ OAuth の認証で `GET` / `PATCH https://api.anthropic.com/api/oauth/account/settings` を呼び、
応答の `grove_enabled`・`grove_updated_at` だけを読んで NDF の語（`training` の真偽値）へ変える。応答の他の鍵
（アカウントの識別子を含む）は読まずに捨てる。トークンは `Authorization` の見出しにだけ置き、戻り値・理由の文言に
含めない。出力はしない（値を返すだけ）。公開の API ではないため、形が変わったら「確かめられない」（null）とする。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path

import claude_accounts as ca
from claude_usage import http_json, new_request

URL = "https://api.anthropic.com/api/oauth/account/settings"
URL_ENV = "NDF_TRAINING_SETTINGS_URL"  # 試験用の差し替え（手元のホストだけを受ける）
SOURCE = "oauth/account/settings.grove_enabled"
TIMEOUT = 3.0
KEYCHAIN_TIMEOUT = 2.0
KEYCHAIN_SERVICE = "Claude Code-credentials"
LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost")

NO_TOKEN = "OAuth のトークンが無い（CLAUDE_CODE_OAUTH_TOKEN・.credentials.json）"
NETWORK = "通信の失敗"
NOT_LOCAL = "試験用の宛先が手元でない"
NO_BOOL = "応答に grove_enabled の真偽値が無い"


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


def foreign_auth(env) -> list[str]:
    """OAuth より優先される認証の変数のうち、空でないものの名前（値は出さない。I4）。"""
    return [k for k in ca.FOREIGN_AUTH_ENV if env.get(k)]


def _keychain_oauth() -> dict | None:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=KEYCHAIN_TIMEOUT, check=False,
        ).stdout
        o = json.loads(out).get("claudeAiOauth")
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return None
    return o if isinstance(o, dict) else None


def oauth_entry(env, config_dir: str | None = None) -> dict | None:
    """認証ファイルの `claudeAiOauth`。`config_dir` を渡せばその置き場だけを読む。macOS で置き場の指定も
    認証ファイルも無いときだけ、既定の名前のキーチェーンを 1 回読む（決定 10）。"""
    conf = config_dir or env.get(ca.CONFIG_ENV)
    o = ca.oauth_in(conf or str(Path.home() / ".claude"))
    if o is None and not conf and sys.platform == "darwin":
        o = _keychain_oauth()
    return o


def _token_of(o: dict | None) -> str | None:
    tok = o.get("accessToken") if isinstance(o, dict) else None
    return tok if isinstance(tok, str) and tok else None


def oauth_token(env) -> str | None:
    """書き換えに使うトークン。`CLAUDE_CODE_OAUTH_TOKEN` → 認証ファイル（→ キーチェーン）。期限は見ない（決定 11）。"""
    return env.get(ca.TOKEN_ENV) or _token_of(oauth_entry(env))


def check_token(env, config_dir: str | None = None) -> tuple[str | None, str | None]:
    """確認に使う (トークン, 確かめられない理由)。`config_dir` を渡せばその置き場の認証ファイルだけを読む。
    期限（`expiresAt`）の切れたトークンは送らない（更新もしない。認証ファイルを書かない）。"""
    tok = None if config_dir else env.get(ca.TOKEN_ENV)
    if tok:
        return tok, None
    o = oauth_entry(env, config_dir)
    tok = _token_of(o)
    if not tok:
        return None, NO_TOKEN
    exp = o.get("expiresAt")
    if isinstance(exp, (int, float)) and exp / 1000 <= time.time():
        conf = config_dir or env.get(ca.CONFIG_ENV) or str(Path.home() / ".claude")
        return None, f"トークンの期限切れ（CLAUDE_CONFIG_DIR={conf} claude を 1 度起動して更新する）"
    return tok, None


def _oauth_request(url: str, token: str, method: str = "GET"):
    headers = {"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20", "Accept": "application/json"}
    return new_request(url, headers, method)


def read_with(token: str, url: str, config_dir: str | None = None) -> Reading:
    """トークンで学習の設定を 1 回読む。例外を出さない。"""
    status, body = http_json(_oauth_request(url, token), TIMEOUT, follow_redirects=False)
    if status != 200:
        return unread(http_reason(status), config_dir=config_dir)
    grove = (body or {}).get("grove_enabled")
    if not isinstance(grove, bool):
        return unread(NO_BOOL, config_dir=config_dir)
    at = body.get("grove_updated_at")
    return Reading("claude", grove, SOURCE, at if isinstance(at, str) else None, None, config_dir)


def read_setting(env=None, config_dir: str | None = None) -> Reading:
    """確認（`check`）の読み取り。OAuth でない接続・宛先・トークン・HTTP・形の失敗を null と理由に変える。"""
    env = os.environ if env is None else env
    if not config_dir:
        foreign = foreign_auth(env)
        if foreign:
            return unread(f"OAuth 以外の接続が有効（{', '.join(foreign)}）")
    url = target(env, URL_ENV, URL)
    if url is None:
        return unread(NOT_LOCAL, config_dir=config_dir)
    tok, why = check_token(env, config_dir)
    if not tok:
        return unread(why, config_dir=config_dir)
    return read_with(tok, url, config_dir)


def turn_off(token: str, url: str) -> str | None:
    """`PATCH {"grove_enabled": false}` を 1 回送る。2xx なら None、そうでなければ理由。例外を出さない。"""
    status, _ = http_json(_oauth_request(url, token, "PATCH"), TIMEOUT, body={"grove_enabled": False}, follow_redirects=False)
    return None if 200 <= status < 300 else http_reason(status)

