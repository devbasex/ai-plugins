"""codex（ChatGPT のログイン）のアカウントの学習の設定を読み書きする腐敗防止層（#1597）。

codex の認証（`$CODEX_HOME/auth.json`、既定 `~/.codex/auth.json`）で `GET https://chatgpt.com/backend-api/settings/user` を
読み、`settings.training_allowed`（ChatGPT の「Improve the model for everyone」）と `settings.codex_training_allowed`・
`settings.codex_training_allowed_v2`（Codex の環境の学習）だけを読む。書き換えは
`PATCH https://chatgpt.com/backend-api/settings/account_user_setting?feature=<鍵>&value=false`（2026-10-02 の実測で 200・
本文 `{"<鍵>": false}`）。トークンは `Authorization`、`account_id` は `ChatGPT-Account-Id` の見出しにだけ置き、
戻り値・理由の文言に含めない。公開の API ではないため、形が変わったら「確かめられない」（null）とする。
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

from claude_training import NOT_LOCAL, Reading, http_reason, target, unread
from claude_usage import http_json

URL = "https://chatgpt.com/backend-api/settings/user"
URL_ENV = "NDF_CODEX_SETTINGS_URL"  # 試験用の差し替え（手元のホストだけを受ける）
PATCH_PATH = "account_user_setting"  # GET の URL から相対で決める
SOURCE = "chatgpt/settings/user.training_allowed"
KEYS = ("training_allowed", "codex_training_allowed", "codex_training_allowed_v2")
TIMEOUT = 3.0
USER_AGENT = "ndf-training-optout"  # Python の既定の User-Agent では 403 が返る（2026-10-02 の実測）

NOT_CHATGPT = "ChatGPT のログインでない（API キーのログイン）"
NO_TOKEN = "codex のトークンが無い"
NO_BOOL = "応答に training_allowed の真偽値が無い"
NO_CODEX_BOOL = "応答に codex_training_allowed の真偽値が無い"


def auth(env) -> tuple[tuple[str, str | None] | None, str | None]:
    """((トークン, account_id), 理由)。ChatGPT のログインでなければ・トークンが無ければ理由だけを返す（I9）。"""
    home = Path(env.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        data = json.loads((home / "auth.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, NO_TOKEN
    if not isinstance(data, dict):
        return None, NO_TOKEN
    if data.get("auth_mode") != "chatgpt":
        return None, NOT_CHATGPT
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    tok, acct = tokens.get("access_token"), tokens.get("account_id")
    if not (isinstance(tok, str) and tok):
        return None, NO_TOKEN
    return (tok, acct if isinstance(acct, str) and acct else None), None


def _request(url: str, cred: tuple[str, str | None], method: str = "GET") -> urllib.request.Request:
    tok, acct = cred
    headers = {"Authorization": f"Bearer {tok}", "User-Agent": USER_AGENT, "Accept": "application/json"}
    if acct:
        headers["ChatGPT-Account-Id"] = acct
    return urllib.request.Request(url, method=method, headers=headers)


def convert(settings) -> tuple[bool | None, str | None]:
    """3 つの鍵から (training, 理由)。I8 の順で、形の不正を true の有無より先に見る。"""
    if not isinstance(settings, dict) or not isinstance(settings.get("training_allowed"), bool):
        return None, NO_BOOL
    if any(k in settings and not isinstance(settings[k], bool) for k in KEYS[1:]):
        return None, NO_CODEX_BOOL
    return any(settings.get(k) is True for k in KEYS), None


def read_with(cred: tuple[str, str | None], url: str) -> tuple[Reading, list[str]]:
    """(読んだ結果, true だった鍵)。例外を出さない。"""
    status, body = http_json(_request(url, cred), TIMEOUT, follow_redirects=False)
    if status != 200:
        return unread(http_reason(status), "codex"), []
    settings = (body or {}).get("settings")
    training, why = convert(settings)
    if training is None:
        return unread(why, "codex"), []
    return Reading("codex", training, SOURCE), [k for k in KEYS if settings.get(k) is True]


def read_setting(env=None) -> Reading:
    """確認（`check --runtime codex`）の読み取り。"""
    env = os.environ if env is None else env
    url = target(env, URL_ENV, URL)
    if url is None:
        return unread(NOT_LOCAL, "codex")
    cred, why = auth(env)
    if cred is None:
        return unread(why, "codex")
    return read_with(cred, url)[0]


def turn_off(cred: tuple[str, str | None], url: str, key: str) -> str | None:
    """1 つの鍵を false へ書き換える `PATCH` を 1 回送る。2xx なら None、そうでなければ理由。例外を出さない。"""
    patch = urllib.parse.urljoin(url, PATCH_PATH) + "?" + urllib.parse.urlencode({"feature": key, "value": "false"})
    status, _ = http_json(_request(patch, cred, "PATCH"), TIMEOUT, follow_redirects=False)
    return None if 200 <= status < 300 else http_reason(status)
