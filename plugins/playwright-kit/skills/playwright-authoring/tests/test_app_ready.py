"""scripts/app_ready.sh の振る舞いをテストする。

手元に HTTP サーバを立て、応答があれば exit 0、応答が無いまま時間切れなら exit 1、
引数の誤りは exit 2 を返し、どの場合も標準出力が 1 行の JSON であることを確かめる。
"""

from __future__ import annotations

import http.server
import json
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "app_ready.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("curl") is None or shutil.which("bash") is None,
    reason="curl と bash が要る",
)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True,
        check=False, timeout=30,
    )


class _Handler(http.server.BaseHTTPRequestHandler):
    status = 200

    def do_GET(self):  # noqa: N802
        self.send_response(self.status)
        self.end_headers()

    def log_message(self, *args):  # 出力を黙らせる
        pass


@pytest.fixture
def server():
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_ready_when_server_answers(server: str):
    proc = run(server + "/", "--timeout", "5")
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["ready"] is True
    assert out["status"] == 200
    assert out["url"] == server + "/"


def test_server_error_is_not_ready(server: str, monkeypatch):
    monkeypatch.setattr(_Handler, "status", 503)
    proc = run(server + "/", "--timeout", "1", "--interval", "0.2")
    assert proc.returncode == 1
    out = json.loads(proc.stdout)
    assert out["ready"] is False
    assert out["status"] == 503


def test_timeout_when_nothing_listens():
    url = f"http://127.0.0.1:{_free_port()}/"
    proc = run(url, "--timeout", "1", "--interval", "0.2")
    assert proc.returncode == 1
    out = json.loads(proc.stdout)
    assert out["ready"] is False
    assert out["status"] is None


def test_missing_url_is_usage_error():
    proc = run()
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["ready"] is False


@pytest.mark.parametrize("args", [("--bad\n\x01\"\\",), ("http://a\tb/", "--timeout", "x")])
def test_usage_error_is_one_json_line(args: tuple[str, ...]):
    proc = run(*args)
    assert proc.returncode == 2
    assert proc.stdout.count("\n") == 1
    out = json.loads(proc.stdout)
    assert out["ready"] is False
    assert args[0] in (out["url"] or "") + out["error"]
