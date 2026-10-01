"""登録済みアカウントのテストが使う置き場と、使用量の取得先・トークンの更新の宛先の偽物（#1389）。

`accounts` の fixture は `NDF_ACCOUNTS_DIR`・`NDF_ACCOUNT_USAGE_URL`・`NDF_ACCOUNT_TOKEN_URL` を偽物へ向け、
共有の設定ディレクトリ（`CLAUDE_CONFIG_DIR`）に番兵の `.credentials.json` を置く。`HOME` も一時ディレクトリへ向け、
利用者の `~/.claude/` と `~/.claude.json` に触れない。偽物は推論の宛先を持たない。
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SHARED_CREDS = {"claudeAiOauth": {"accessToken": "shared-access-SECRET", "refreshToken": "shared-refresh-SECRET", "expiresAt": 1}}


def window(util, resets_in=3600.0):
    return {"utilization": util, "resets_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + resets_in))}


class FakeAnthropic:
    """トークンごとの使用量の応答と、更新の応答を返す。呼ばれた回数を数える。"""

    def __init__(self):
        self.usage: dict[str, tuple[int, dict]] = {}
        self.refresh: dict[str, tuple[int, dict]] = {}
        self.usage_calls: list[str] = []
        self.refresh_calls: list[str] = []
        self.delay = 0.0
        self.lock = threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                tok = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                with fake.lock:
                    fake.usage_calls.append(tok)
                if fake.delay:
                    time.sleep(fake.delay)
                got = fake.usage.get(tok, (401, {"error": "unknown token"}))
                if isinstance(got, list):  # 呼ばれるたびに次の応答（最後のものは繰り返す）
                    with fake.lock:
                        got = got.pop(0) if len(got) > 1 else got[0]
                self.reply(*got)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                rt = body.get("refresh_token")
                with fake.lock:
                    fake.refresh_calls.append(rt)
                time.sleep(0.2)  # 並んだ更新が重なるように
                self.reply(*fake.refresh.get(rt, (400, {"error": "invalid_grant"})))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def set_usage(self, token, five=None, seven=None, spend=False, status=200, extra=None):
        self.usage[token] = self.usage_reply(five, seven, spend, status, extra)

    @staticmethod
    def usage_reply(five=None, seven=None, spend=False, status=200, extra=None):
        """`extra` は応答へそのまま足すキー（`limits`・`spend` など。#1453）。"""
        return (status, {"five_hour": five, "seven_day": seven, "extra_usage": {"spend_limit_reached": spend}, **(extra or {})})

    def close(self):
        self.server.shutdown()


def scoped_limit(percent, model="Fable", resets_in=86400.0):
    """応答の `limits[]` のモデル別の週の枠の 1 要素（#1453 の設計が読む `kind`・`percent`・`resets_at`・`scope.model`）。"""
    return {
        "kind": "weekly_scoped",
        "percent": percent,
        "resets_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + resets_in)),
        "scope": {"model": {"id": model.lower(), "display_name": model}},
    }


class Store:
    def __init__(self, root: Path, fake: FakeAnthropic, shared: Path):
        self.root = root
        self.fake = fake
        self.shared = shared

    def add(
        self,
        name,
        email=None,
        token=None,
        expires_in=8 * 3600,
        util5=10.0,
        util7=10.0,
        spend=False,
        scopes=None,
        refresh_in=None,
        org_id=None,
        org_name=None,
        tier=None,
        capacity=None,
        extra=None,
    ):
        """アカウントを置き場へ直に置き、偽物に残量を持たせる。`util5` が None なら残量を返さない（503）。

        `org_id` が None なら組織を書かない（組織を記録する前の account.json の形）。`tier` は `rateLimitTier`、
        `capacity` は枠の大きさの宣言、`extra` は使用量の応答へ足すキー（#1453）。
        """
        token = token or f"{name}-access-SECRET"
        d = self.root / name
        d.mkdir(parents=True, mode=0o700, exist_ok=True)
        oauth = {
            "accessToken": token,
            "refreshToken": f"{name}-refresh-SECRET",
            "expiresAt": int((time.time() + expires_in) * 1000),
            "scopes": scopes if scopes is not None else ["user:inference", "user:profile"],
            "subscriptionType": "max",
        }
        if tier is not None:
            oauth["rateLimitTier"] = tier
        if refresh_in is not None:
            oauth["refreshTokenExpiresAt"] = int((time.time() + refresh_in) * 1000)
        self.write(d / ".credentials.json", {"claudeAiOauth": oauth})
        row = {
            "name": name,
            "email": email or f"{name}@example.com",
            "registered_at": "2026-09-28T00:00:00+00:00",
            "needs_relogin": False,
            "limit": None,
        }
        if org_id is not None:
            row.update(org_id=org_id, org_name=org_name or "")
        if capacity is not None:
            row["capacity"] = capacity
        self.write(d / "account.json", row)
        if util5 is not None:
            self.fake.set_usage(token, window(util5, 3600), window(util7, 86400), spend, extra=extra)
        else:
            self.fake.usage[token] = (503, {"error": "unavailable"})
        return token

    @staticmethod
    def write(path, data):
        path.write_text(json.dumps(data))
        os.chmod(path, 0o600)

    def account(self, name):
        return json.loads((self.root / name / "account.json").read_text())

    def creds(self, name):
        return json.loads((self.root / name / ".credentials.json").read_text())["claudeAiOauth"]

    def env(self):
        return {
            "NDF_ACCOUNTS_DIR": str(self.root),
            "NDF_ACCOUNT_USAGE_URL": self.fake.url + "/usage",
            "NDF_ACCOUNT_TOKEN_URL": self.fake.url + "/token",
            "CLAUDE_CONFIG_DIR": str(self.shared),
        }


@pytest.fixture()
def accounts(tmp_path, monkeypatch):
    fake = FakeAnthropic()
    shared = tmp_path / "shared-claude"
    shared.mkdir()
    Store.write(shared / ".credentials.json", SHARED_CREDS)
    root = tmp_path / "accounts"
    root.mkdir(mode=0o700)
    home = tmp_path / "accounts-home"  # `isolated_env` の `home` と分ける（本物の HOME と同じかを比べる）
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    s = Store(root, fake, shared)
    for k, v in s.env().items():
        monkeypatch.setenv(k, v)
    for k in (
        "NDF_ACCOUNT_CHECK_INTERVAL",
        "NDF_ACCOUNT_SWITCH_AT",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_SCOPES",
        "NDF_CLAUDE_ACCOUNT",
        "NDF_SUPERVISE_CLAUDE_FALLBACK",
        "NDF_SHARED_CONFIG_DIR",
        "CLAUDE_CODE_PLUGIN_CACHE_DIR",
    ):
        monkeypatch.delenv(k, raising=False)
    yield s
    fake.close()
