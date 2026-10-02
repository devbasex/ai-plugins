"""試行の training-optout.py: 学習に使わない設定かの確かめ。応答は差し替えて試す。"""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "experimental" / "training-optout.py"
TOKEN = "sk-ant-oat01-SECRET"
ACCOUNT = "acct-uuid-1234"


@pytest.fixture
def mod(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("training_optout", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": TOKEN}}))
    return m


def run(mod, capsys, *argv):
    with pytest.raises(SystemExit) as e:
        mod.main(["check", *argv])
    out = capsys.readouterr()
    return e.value.code, json.loads(out.out), out.out + out.err


def answer(mod, monkeypatch, body=None, status=None):
    seen = {}

    def fake(req, timeout):
        seen["auth"] = req.get_header("Authorization")
        seen["beta"] = req.get_header("Anthropic-beta")
        if status:
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(b"{}"))
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake)
    return seen


def test_grove_disabled_is_not_used_for_training(mod, monkeypatch, capsys):
    seen = answer(mod, monkeypatch, {"grove_enabled": False, "grove_updated_at": "2026-08-13T15:10:07Z", "uuid": ACCOUNT})
    code, res, raw = run(mod, capsys, "--runtime", "claude")
    assert code == 0 and res["status"] == "ok"
    item = res["items"][0]
    assert item["runtime"] == "claude" and item["training"] is False
    assert item["source"] == "oauth/account/settings.grove_enabled" and item["updated_at"] == "2026-08-13T15:10:07Z"
    assert seen == {"auth": f"Bearer {TOKEN}", "beta": "oauth-2025-04-20"}
    assert TOKEN not in raw and ACCOUNT not in raw


def test_grove_enabled_stops(mod, monkeypatch, capsys):
    answer(mod, monkeypatch, {"grove_enabled": True, "grove_updated_at": None})
    code, res, _ = run(mod, capsys)
    assert code == 1 and res["status"] == "stopped" and res["items"][0]["training"] is True


@pytest.mark.parametrize("body,status", [({"other": 1}, None), ({"grove_enabled": "no"}, None), (None, 403), (None, 401)])
def test_unreadable_is_not_treated_as_optout(mod, monkeypatch, capsys, body, status):
    answer(mod, monkeypatch, body, status)
    code, res, raw = run(mod, capsys)
    assert code == 3 and res["status"] == "stopped"
    assert res["items"][0]["training"] is None and res["items"][0]["reason"]
    assert TOKEN not in raw


def test_no_credentials_stops(mod, monkeypatch, capsys, tmp_path):
    (tmp_path / ".credentials.json").unlink()
    code, res, _ = run(mod, capsys)
    assert code == 3 and res["items"][0]["training"] is None


def test_env_token_wins(mod, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "env-token")
    seen = answer(mod, monkeypatch, {"grove_enabled": False})
    run(mod, capsys)
    assert seen["auth"] == "Bearer env-token"


def test_other_runtimes_are_unsupported(mod, monkeypatch, capsys):
    answer(mod, monkeypatch, {"grove_enabled": False})
    code, res, _ = run(mod, capsys, "--runtime", "claude", "--runtime", "codex")
    assert code == 3 and res["status"] == "stopped"
    by = {i["runtime"]: i for i in res["items"]}
    assert by["claude"]["training"] is False
    assert by["codex"]["training"] is None and by["codex"]["reason"] == "unsupported"
