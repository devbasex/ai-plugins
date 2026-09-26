"""HTTP と Slack の呼び出し・`.env` の読み取りの包み（lib/notify.py・#1142 の決定 19）。外部パッケージは全体テストの環境（根の pyproject.toml）が入れる。

今の `wait-notify.py:_parse_env` から変わる入力を固定する: `export KEY=v` は `KEY` として読む（今は `export KEY`）、
引用しない値の後ろの ` # 注記` は値に含めない（今は含める）。すでにある環境変数を上書きしないのは今と同じ。
"""
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import slack_sdk  # noqa: E402,F401  包みの外部パッケージ。無ければ集めるところで落とす
import dotenv  # noqa: E402,F401  包みの外部パッケージ。無ければ集めるところで落とす
import httpx  # noqa: E402,F401  包みの外部パッケージ。無ければ集めるところで落とす
import notify  # noqa: E402


@pytest.fixture
def server():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append((self.path, self.headers.get("authorization"), body))
            ok = body.get("channel") == "C1"
            self._send(200, {"ok": ok, **({} if ok else {"error": "channel_not_found"})})

        def do_GET(self):
            self._send(404 if self.path == "/missing" else 200, {"path": self.path})

        def _send(self, code, obj):
            out = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", seen
    httpd.shutdown()


def test_slack_call_posts_json_with_the_token(server, monkeypatch):
    base, seen = server
    monkeypatch.setenv("NDF_SLACK_API_BASE", base)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-t")
    assert notify.slack_call("chat.postMessage", {"channel": "C1", "text": "x"}) == {"ok": True}
    assert seen[0] == ("/api/chat.postMessage", "Bearer xoxb-t", {"channel": "C1", "text": "x"})
    errors = []
    assert notify.slack_call("chat.postMessage", {"channel": "C9"}, on_error=lambda m, e: errors.append((m, e))) is None
    assert errors == [("chat.postMessage", "channel_not_found")]
    assert notify.slack_call("x", {}, base_url="http://127.0.0.1:1", on_error=lambda m, e: errors.append(e)) is None


def test_http_results_carry_the_reason(server):
    base, _ = server
    ok = notify.http_get(base + "/a")
    assert ok.ok and ok.status == 200 and json.loads(ok.text) == {"path": "/a"}
    miss = notify.http_get(base + "/missing")
    assert (miss.status, miss.error, miss.ok) == (404, "HTTP 404", False)
    assert notify.http_get("http://127.0.0.1:1/").error.startswith("接続できない")
    assert notify.http_post_json(base + "/api/x", {"channel": "C1"}).ok


def test_env_file_is_found_up_to_the_git_top_and_does_not_override(tmp_path: Path, monkeypatch):
    top = tmp_path / "repo"
    (top / "a" / "b").mkdir(parents=True)
    (top / ".git").mkdir()
    (top / ".env").write_text('NDF_T_A="q v"\nNDF_T_B=keep-me\nexport NDF_T_C=3\nNDF_T_D=v # 注記\n# c\n')
    (tmp_path / ".env").write_text("NDF_T_OUT=1\n")
    for k in ("NDF_T_A", "NDF_T_C", "NDF_T_D", "NDF_T_OUT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NDF_T_B", "set")
    assert notify.load_env_upward(top / "a" / "b") == top / ".env"
    assert (os.environ["NDF_T_A"], os.environ["NDF_T_B"], os.environ["NDF_T_C"], os.environ["NDF_T_D"]) == \
        ("q v", "set", "3", "v")
    assert "NDF_T_OUT" not in os.environ and "export NDF_T_C" not in os.environ


def test_env_file_falls_back_and_may_be_missing(tmp_path: Path, monkeypatch):
    (tmp_path / "w" / ".git").mkdir(parents=True)
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / ".env").write_text("NDF_T_F=1\n")
    monkeypatch.delenv("NDF_T_F", raising=False)
    assert notify.load_env_upward(tmp_path / "w", fallback=tmp_path / "s") == tmp_path / "s" / ".env"
    assert os.environ["NDF_T_F"] == "1"
    assert notify.load_env_upward(tmp_path / "w") is None
