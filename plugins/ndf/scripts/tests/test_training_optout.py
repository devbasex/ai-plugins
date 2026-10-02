"""training-optout.py: 学習の設定の確認（check）と、セッションの開始での書き換え（session-start）。

応答は手元の偽の宛先（`NDF_TRAINING_SETTINGS_URL`・`NDF_CODEX_SETTINGS_URL`）で差し替える。実物の API を呼ばない。
"""

from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "training-optout.py"

TOKEN = "sk-ant-oat01-SECRET-MARK"
ACCOUNT = "acct-uuid-MARK-1234"
CODEX_TOKEN = "codex-access-SECRET-MARK"
CODEX_ACCOUNT = "codex-account-MARK-5678"
MARKS = (TOKEN, ACCOUNT, CODEX_TOKEN, CODEX_ACCOUNT)


class Fake:
    """手元の偽の宛先。方法ごとの (状態, 本文) を返し、受けた要求を残す。"""

    def __init__(self):
        self.replies: dict[str, tuple[int, object]] = {}
        self.requests: list[dict] = []
        self.redirect_to: str | None = None
        fake = self

        class H(BaseHTTPRequestHandler):
            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                fake.requests.append({"method": self.command, "path": self.path, "headers": self.headers, "body": body})
                if fake.redirect_to:
                    self.send_response(302)
                    self.send_header("Location", fake.redirect_to)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status, payload = fake.replies.get(self.command, (500, None))
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_PATCH = _do

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}/backend-api/settings/user"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def of(self, method):
        return [r for r in self.requests if r["method"] == method]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    f = Fake()
    yield f
    f.close()


@pytest.fixture
def mod(monkeypatch, tmp_path, fake):
    spec = importlib.util.spec_from_file_location("training_optout", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for k in ("CLAUDE_CODE_OAUTH_TOKEN", "NDF_SUPERVISE_CLAUDE_FALLBACK", "NDF_TRAINING_OPTOUT", *m.ca.FOREIGN_AUTH_ENV):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NDF_ACCOUNTS_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("NDF_TRAINING_SETTINGS_URL", fake.url)
    monkeypatch.setenv("NDF_CODEX_SETTINGS_URL", fake.url)
    (tmp_path / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": TOKEN}}))
    codex_auth(tmp_path)
    return m


def codex_auth(tmp_path, mode="chatgpt", token=CODEX_TOKEN):
    d = tmp_path / "codex"
    d.mkdir(exist_ok=True)
    tokens = {"access_token": token, "account_id": CODEX_ACCOUNT, "refresh_token": "r-MARK"}
    (d / "auth.json").write_text(json.dumps({"auth_mode": mode, "tokens": tokens}))


def grove(value, **extra):
    return 200, {"grove_enabled": value, "grove_updated_at": "2026-08-13T15:10:07Z", "uuid": ACCOUNT, **extra}


def chatgpt(**settings):
    return 200, {"settings": settings, "user_id": CODEX_ACCOUNT, "workspace_id": CODEX_ACCOUNT}


def run(mod, capsys, *argv):
    with pytest.raises(SystemExit) as e:
        mod.main(["check", *argv])
    out = capsys.readouterr()
    no_secret(out.out + out.err)
    return e.value.code, json.loads(out.out)


def start(mod, capsys, *argv):
    code = mod.main(["session-start", *argv])
    out = capsys.readouterr()
    no_secret(out.out + out.err)
    assert code == 0 and out.err == ""
    return json.loads(out.out)["systemMessage"] if out.out.strip() else None


def no_secret(text):
    for mark in MARKS:
        assert mark not in text


# --- check: claude -------------------------------------------------------------------


def test_check_false_is_ok(mod, capsys, fake):
    fake.replies["GET"] = grove(False)
    code, res = run(mod, capsys, "--runtime", "claude")
    assert code == 0 and res["status"] == "ok"
    assert res["items"] == [
        {
            "runtime": "claude",
            "config_dir": None,
            "training": False,
            "source": "oauth/account/settings.grove_enabled",
            "updated_at": "2026-08-13T15:10:07Z",
            "reason": None,
        }
    ]
    (req,) = fake.requests
    assert req["headers"]["Authorization"] == f"Bearer {TOKEN}" and req["headers"]["anthropic-beta"] == "oauth-2025-04-20"


def test_check_true_stops(mod, capsys, fake):
    fake.replies["GET"] = grove(True)
    code, res = run(mod, capsys)
    assert code == 1 and res["status"] == "stopped" and res["items"][0]["training"] is True


@pytest.mark.parametrize(
    "reply,reason",
    [
        ((401, {}), "HTTP 401"),
        ((403, {}), "HTTP 403"),
        ((200, {"other": 1}), "応答に grove_enabled の真偽値が無い"),
        ((200, {"grove_enabled": "false"}), "応答に grove_enabled の真偽値が無い"),
    ],
)
def test_check_unreadable_is_not_optout(mod, capsys, fake, reply, reason):
    fake.replies["GET"] = reply
    code, res = run(mod, capsys)
    assert code == 3 and res["status"] == "stopped"
    assert res["items"][0]["training"] is None and res["items"][0]["reason"] == reason


def closed_port_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}/"


def test_check_network_failure(mod, capsys, monkeypatch):
    monkeypatch.setenv("NDF_TRAINING_SETTINGS_URL", closed_port_url())
    code, res = run(mod, capsys)
    assert code == 3 and res["items"][0]["training"] is None and res["items"][0]["reason"] == "通信の失敗"


def test_check_without_credentials(mod, capsys, fake, tmp_path):
    (tmp_path / ".credentials.json").unlink()
    code, res = run(mod, capsys)
    assert code == 3 and res["items"][0]["training"] is None and res["items"][0]["reason"]
    assert fake.requests == []


def test_check_env_token_wins(mod, capsys, fake, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "env-token")
    fake.replies["GET"] = grove(False)
    run(mod, capsys)
    assert fake.requests[0]["headers"]["Authorization"] == "Bearer env-token"


def test_check_kiro_and_agy_are_unsupported(mod, capsys, fake):
    fake.replies["GET"] = grove(False)
    code, res = run(mod, capsys, "--runtime", "kiro", "--runtime", "agy")
    assert code == 3 and res["status"] == "stopped"
    assert [(i["runtime"], i["training"], i["reason"]) for i in res["items"]] == [
        ("kiro", None, "unsupported"),
        ("agy", None, "unsupported"),
    ]


def test_check_ignores_optout_switch(mod, capsys, fake, monkeypatch):
    monkeypatch.setenv("NDF_TRAINING_OPTOUT", "0")
    fake.replies["GET"] = grove(True)
    code, res = run(mod, capsys)
    assert len(fake.of("GET")) == 1 and res["items"][0]["training"] is True


def account(tmp_path, name, token, expires_at=None):
    d = tmp_path / "accounts" / name
    d.mkdir(parents=True)
    oauth = {"accessToken": token} | ({} if expires_at is None else {"expiresAt": expires_at})
    (d / ".credentials.json").write_text(json.dumps({"claudeAiOauth": oauth}))
    return str(d)


def test_check_config_dirs_besides_default(mod, capsys, fake, monkeypatch, tmp_path):
    a, b = account(tmp_path, "a", "tok-a"), account(tmp_path, "b", "tok-b")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "env-token")
    fake.replies["GET"] = grove(False)
    code, res = run(mod, capsys, "--config-dir", a, b)
    assert code == 0
    assert [r["headers"]["Authorization"] for r in fake.requests] == ["Bearer env-token", "Bearer tok-a", "Bearer tok-b"]
    assert [i["config_dir"] for i in res["items"]] == [None, a, b]


def test_check_expired_token_is_not_sent(mod, capsys, fake, tmp_path):
    old = account(tmp_path, "old", "tok-old", expires_at=1000)
    live = account(tmp_path, "live", "tok-live", expires_at=4102444800000)
    fake.replies["GET"] = grove(False)
    code, res = run(mod, capsys, "--config-dir", old, live)
    assert code == 3 and [r["headers"]["Authorization"] for r in fake.requests] == [f"Bearer {TOKEN}", "Bearer tok-live"]
    by = {i["config_dir"]: i for i in res["items"]}
    assert by[old]["training"] is None and "期限切れ" in by[old]["reason"] and by[live]["training"] is False


@pytest.mark.parametrize("var", ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"])
def test_check_non_oauth_connection(mod, capsys, fake, monkeypatch, tmp_path, var):
    monkeypatch.setenv(var, "secret-value-MARK")
    a = account(tmp_path, "a", "tok-a")
    fake.replies["GET"] = grove(False)
    code, res = run(mod, capsys, "--config-dir", a)
    assert code == 3 and [r["headers"]["Authorization"] for r in fake.requests] == ["Bearer tok-a"]
    assert res["items"][0]["training"] is None and var in res["items"][0]["reason"]
    assert "secret-value-MARK" not in json.dumps(res)


def test_check_metered_declaration(mod, capsys, fake, monkeypatch):
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE_FALLBACK", "CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-east-1")
    fake.replies["GET"] = grove(False)
    code, res = run(mod, capsys)
    assert code == 3 and [i["config_dir"] for i in res["items"]] == [None, "metered"]
    assert res["items"][1]["training"] is None and "CLAUDE_CODE_USE_BEDROCK" in res["items"][1]["reason"]


def test_check_saved_metered_declaration_unless_cleared(mod, capsys, fake, monkeypatch):
    mod.ca.save_metered("bedrock", {"CLAUDE_CODE_USE_BEDROCK": "1"}, {"region": "us-east-1"})
    fake.replies["GET"] = grove(False)
    assert run(mod, capsys)[0] == 3
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE_FALLBACK", "")
    code, res = run(mod, capsys)
    assert code == 0 and len(res["items"]) == 1


def test_default_destinations(mod):
    assert mod.ct.URL == "https://api.anthropic.com/api/oauth/account/settings"
    assert mod.xt.URL == "https://chatgpt.com/backend-api/settings/user"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/",
        "http://127.0.0.1.evil.example/",
        "http://localhost.evil.example/",
        "http://localhost@evil.example/",
        "http://evil.example/?h=127.0.0.1",
        "ftp://127.0.0.1/",
        "http://user@127.0.0.1/",
    ],
)
def test_override_must_be_local(mod, capsys, fake, monkeypatch, url):
    monkeypatch.setenv("NDF_TRAINING_SETTINGS_URL", url)
    monkeypatch.setenv("NDF_CODEX_SETTINGS_URL", url)
    monkeypatch.setattr(mod.ct, "check_token", lambda *a, **k: pytest.fail("トークンを読んだ"))
    code, res = run(mod, capsys, "--runtime", "claude", "--runtime", "codex")
    assert code == 3 and [i["reason"] for i in res["items"]] == ["試験用の宛先が手元でない"] * 2
    monkeypatch.setattr(mod.ct, "oauth_token", lambda *a, **k: pytest.fail("トークンを読んだ"))
    assert "試験用の宛先が手元でない" in start(mod, capsys)
    assert "試験用の宛先が手元でない" in start(mod, capsys, "--runtime", "codex")
    assert fake.requests == []


def test_redirect_is_not_followed(mod, capsys, fake):
    target = Fake()
    try:
        fake.redirect_to = target.url
        code, res = run(mod, capsys, "--runtime", "claude", "--runtime", "codex")
        assert code == 3 and [i["reason"] for i in res["items"]] == ["HTTP 302", "HTTP 302"]
        assert "HTTP 302" in start(mod, capsys)
        assert "HTTP 302" in start(mod, capsys, "--runtime", "codex")
        assert target.requests == []
    finally:
        target.close()


# --- check: codex --------------------------------------------------------------------


@pytest.mark.parametrize(
    "settings,training",
    [
        ({"training_allowed": False, "codex_training_allowed": False, "codex_training_allowed_v2": False}, False),
        ({"training_allowed": False}, False),
        ({"training_allowed": True, "codex_training_allowed": False, "codex_training_allowed_v2": False}, True),
        ({"training_allowed": False, "codex_training_allowed": True, "codex_training_allowed_v2": False}, True),
        ({"training_allowed": False, "codex_training_allowed": False, "codex_training_allowed_v2": True}, True),
    ],
)
def test_check_codex_keys(mod, capsys, fake, settings, training):
    fake.replies["GET"] = chatgpt(**settings, notice="x")
    code, res = run(mod, capsys, "--runtime", "codex")
    item = res["items"][0]
    assert item["runtime"] == "codex" and item["training"] is training and code == (1 if training else 0)
    assert item["source"] == "chatgpt/settings/user.training_allowed"
    h = fake.requests[0]["headers"]
    assert h["Authorization"] == f"Bearer {CODEX_TOKEN}" and h["ChatGPT-Account-Id"] == CODEX_ACCOUNT
    assert h["User-Agent"] == "ndf-training-optout"


@pytest.mark.parametrize(
    "reply,reason",
    [
        (chatgpt(codex_training_allowed_v2=True), "応答に training_allowed の真偽値が無い"),
        (chatgpt(training_allowed="false"), "応答に training_allowed の真偽値が無い"),
        (chatgpt(training_allowed=True, codex_training_allowed="unknown"), "応答に codex_training_allowed の真偽値が無い"),
        (
            chatgpt(training_allowed=False, codex_training_allowed=None, codex_training_allowed_v2=False),
            "応答に codex_training_allowed の真偽値が無い",
        ),
        ((200, {"no_settings": 1}), "応答に training_allowed の真偽値が無い"),
        ((401, {}), "HTTP 401"),
        ((403, {}), "HTTP 403"),
    ],
)
def test_check_codex_unreadable(mod, capsys, fake, reply, reason):
    fake.replies["GET"] = reply
    code, res = run(mod, capsys, "--runtime", "codex")
    assert code == 3 and res["items"][0]["training"] is None and res["items"][0]["reason"] == reason


def test_check_codex_network_failure(mod, capsys, monkeypatch):
    monkeypatch.setenv("NDF_CODEX_SETTINGS_URL", closed_port_url())
    code, res = run(mod, capsys, "--runtime", "codex")
    assert code == 3 and res["items"][0]["reason"] == "通信の失敗"


def test_check_codex_without_chatgpt_login(mod, capsys, fake, tmp_path):
    codex_auth(tmp_path, mode="apikey")
    code, res = run(mod, capsys, "--runtime", "codex")
    assert code == 3 and res["items"][0]["reason"] == "ChatGPT のログインでない（API キーのログイン）"
    (tmp_path / "codex" / "auth.json").unlink()
    code, res = run(mod, capsys, "--runtime", "codex")
    assert code == 3 and res["items"][0]["reason"] == "codex のトークンが無い"
    assert fake.requests == []


# --- session-start: claude -----------------------------------------------------------


def test_session_start_turns_off_true(mod, capsys, fake):
    fake.replies["GET"] = grove(True)
    fake.replies["PATCH"] = (202, None)
    msg = start(mod, capsys)
    (patch,) = fake.of("PATCH")
    assert json.loads(patch["body"]) == {"grove_enabled": False}
    assert patch["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert msg.startswith("[ndf] ") and "Off にした" in msg and "NDF_TRAINING_OPTOUT=0" in msg


def test_session_start_sends_nothing_when_false(mod, capsys, fake):
    fake.replies["GET"] = grove(False)
    assert start(mod, capsys) is None
    assert fake.of("PATCH") == [] and len(fake.of("GET")) == 1


def test_session_start_uses_expired_token(mod, capsys, fake, tmp_path):
    (tmp_path / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": TOKEN, "expiresAt": 1000}}))
    fake.replies["GET"] = grove(False)
    assert start(mod, capsys) is None and len(fake.requests) == 1  # 決定 11: 期限を事前に判定しない


@pytest.mark.parametrize(
    "get,patch",
    [((401, {}), None), ((403, {}), None), ((200, {"grove_enabled": None}), None), (grove(True), (500, {})), (grove(True), (403, {}))],
)
def test_session_start_failures_are_told(mod, capsys, fake, get, patch):
    fake.replies["GET"] = get
    if patch:
        fake.replies["PATCH"] = patch
    msg = start(mod, capsys)
    assert msg.startswith("[ndf] ") and ("確かめられなかった" in msg or "Off にできなかった" in msg)


def test_session_start_without_credentials(mod, capsys, fake, tmp_path):
    (tmp_path / ".credentials.json").unlink()
    assert "OAuth のトークンが無い" in start(mod, capsys)
    assert fake.requests == []


def test_session_start_network_failure(mod, capsys, monkeypatch):
    monkeypatch.setenv("NDF_TRAINING_SETTINGS_URL", closed_port_url())
    assert "通信の失敗" in start(mod, capsys)


def test_session_start_unexpected_exception(mod, capsys, fake, monkeypatch):
    monkeypatch.setattr(mod.ct, "read_with", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(TOKEN)))
    msg = start(mod, capsys)
    assert "RuntimeError" in msg


@pytest.mark.parametrize("var", ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"])
def test_session_start_skips_non_oauth(mod, capsys, fake, monkeypatch, var):
    monkeypatch.setenv(var, "1")
    fake.replies["GET"] = grove(True)
    assert start(mod, capsys) is None and fake.requests == []


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_session_start_optout_sends_nothing(mod, capsys, fake, monkeypatch, runtime):
    monkeypatch.setenv("NDF_TRAINING_OPTOUT", "0")
    fake.replies["GET"] = grove(True) if runtime == "claude" else chatgpt(training_allowed=True)
    assert start(mod, capsys, "--runtime", runtime) is None and fake.requests == []


def test_session_start_finishes_within_hook_timeout(mod, capsys, monkeypatch):
    with socket.socket() as s:  # 接続は受けるが応答しない宛先
        s.bind(("127.0.0.1", 0))
        s.listen(8)
        monkeypatch.setenv("NDF_TRAINING_SETTINGS_URL", f"http://127.0.0.1:{s.getsockname()[1]}/")
        t = time.monotonic()
        msg = start(mod, capsys)
        assert time.monotonic() - t < 7 and "確かめられなかった" in msg


def test_session_start_runs_with_plain_python3(fake, tmp_path):
    """hook は `python3` で直に起動する（標準ライブラリだけで動く）。"""
    fake.replies["GET"] = grove(True)
    fake.replies["PATCH"] = (202, None)
    (tmp_path / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": TOKEN}}))
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(tmp_path), "NDF_TRAINING_SETTINGS_URL": fake.url}
    p = subprocess.run(["python3", "-S", str(SCRIPT), "session-start"], env=env, capture_output=True, text=True, timeout=20)
    assert p.returncode == 0 and p.stderr == ""
    assert "Off にした" in json.loads(p.stdout)["systemMessage"] and len(fake.of("PATCH")) == 1
    no_secret(p.stdout)


# --- session-start: codex ------------------------------------------------------------


def test_codex_session_start_turns_off_each_true_key(mod, capsys, fake):
    fake.replies["GET"] = chatgpt(training_allowed=True, codex_training_allowed=False, codex_training_allowed_v2=True)
    fake.replies["PATCH"] = (200, {"training_allowed": False})
    msg = start(mod, capsys, "--runtime", "codex")
    patches = fake.of("PATCH")
    queries = [urllib.parse.parse_qs(urllib.parse.urlsplit(p["path"]).query) for p in patches]
    assert queries == [
        {"feature": ["training_allowed"], "value": ["false"]},
        {"feature": ["codex_training_allowed_v2"], "value": ["false"]},
    ]
    assert all(urllib.parse.urlsplit(p["path"]).path == "/backend-api/settings/account_user_setting" for p in patches)
    assert all(p["headers"]["ChatGPT-Account-Id"] == CODEX_ACCOUNT for p in patches)
    assert msg.startswith("[ndf] ") and "Off にした" in msg and "NDF_TRAINING_OPTOUT=0" in msg


def test_codex_session_start_keeps_within_the_time_budget(mod, capsys, fake, monkeypatch):
    """GET 1 回と PATCH 3 回の全体を予算の内に収め、予算の切れた鍵は送らずに知らせる（hook の 10 秒で打ち切られない）。"""
    fake.replies["GET"] = chatgpt(training_allowed=True, codex_training_allowed=True, codex_training_allowed_v2=True)
    monkeypatch.setattr(mod.xt, "BUDGET", 1.0)
    monkeypatch.setattr(mod.xt, "TIMEOUT", 0.6)
    sent = []

    def slow_turn_off(cred, url, key, timeout):  # 応答しない宛先: 上限の秒まで待って失敗する
        sent.append(timeout)
        time.sleep(timeout)
        return "通信できない"

    monkeypatch.setattr(mod.xt, "turn_off", slow_turn_off)
    t = time.monotonic()
    msg = start(mod, capsys, "--runtime", "codex")
    assert time.monotonic() - t < 1.3
    assert sent and len(sent) < 3 and all(x <= 0.6 for x in sent), msg
    assert "Off にできなかった" in msg and "時間の予算" in msg


def test_codex_session_start_sends_nothing_when_false(mod, capsys, fake):
    fake.replies["GET"] = chatgpt(training_allowed=False, codex_training_allowed=False, codex_training_allowed_v2=False)
    assert start(mod, capsys, "--runtime", "codex") is None and fake.of("PATCH") == []


@pytest.mark.parametrize(
    "get,patch",
    [
        ((401, {}), None),
        ((403, {}), None),
        (chatgpt(training_allowed=True), (422, {})),
        (chatgpt(training_allowed=True, codex_training_allowed="x"), None),
    ],
)
def test_codex_session_start_failures_are_told(mod, capsys, fake, get, patch):
    fake.replies["GET"] = get
    if patch:
        fake.replies["PATCH"] = patch
    msg = start(mod, capsys, "--runtime", "codex")
    assert msg.startswith("[ndf] ") and ("確かめられなかった" in msg or "Off にできなかった" in msg)
    if get[0] == 200 and patch is None:
        assert fake.of("PATCH") == []  # 読めない値で書き換えない


def test_codex_session_start_without_auth(mod, capsys, fake, tmp_path, monkeypatch):
    (tmp_path / "codex" / "auth.json").unlink()
    assert "codex のトークンが無い" in start(mod, capsys, "--runtime", "codex")
    codex_auth(tmp_path, mode="apikey")
    assert start(mod, capsys, "--runtime", "codex") is None
    monkeypatch.setenv("NDF_CODEX_SETTINGS_URL", closed_port_url())
    codex_auth(tmp_path)
    assert "通信の失敗" in start(mod, capsys, "--runtime", "codex")
    assert fake.requests == []


# --- 置き場と hook の定義 ------------------------------------------------------------


def hooks(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))["hooks"]


def test_claude_hook_runs_on_startup_and_resume():
    groups = [g for g in hooks("hooks/claude.json")["SessionStart"] if g.get("matcher") == "startup|resume"]
    found = [h for g in groups for h in g["hooks"] if 'training-optout.py\\" session-start' in json.dumps(h["command"])]
    assert len(found) == 1 and found[0]["timeout"] == 10


def test_codex_hook_runs_on_session_start():
    found = [h for g in hooks("hooks/codex.json")["SessionStart"] for h in g["hooks"] if "session-start --runtime codex" in h["command"]]
    assert len(found) == 1 and found[0]["timeout"] == 10


def test_agy_and_kiro_hooks_do_not_rewrite():
    for path in ROOT.glob("dev.*/**/*.json"):
        assert "training-optout" not in path.read_text(encoding="utf-8")


def test_experimental_copy_is_gone():
    assert SCRIPT.exists() and not (ROOT / "scripts" / "experimental" / "training-optout.py").exists()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
