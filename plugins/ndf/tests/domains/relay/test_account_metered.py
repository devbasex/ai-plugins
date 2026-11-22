"""従量の接続（Bedrock・API キー）の設定と確かめ（`ndf relay account add-bedrock`・`add-api-key`・`api-key-monthly`・
`api-key-use`・`check`。#3569）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from ndf.domains.relay import account_metered as m
from ndf.domains.relay import account_registry as reg

FAKE_AWS = """#!/bin/sh
case "$*" in
  "configure list-profiles") echo default; echo dev ;;
  "configure get region --profile dev") echo us-east-1 ;;
  sts*) echo '{}' ;;
  bedrock-runtime*) if [ "$FAKE_AWS_DENY" = 1 ]; then echo "An error occurred (AccessDeniedException) when calling: You don't have access to the model" >&2; exit 254; fi; echo '{}' ;;
  *) exit 1 ;;
esac
"""


def _transport(status: int, body: dict | None = None) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, json=body or {}))


def _down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("down", request=request)


def _store(tmp_path: Path) -> Path:
    d = tmp_path / "store" / "work1"
    d.mkdir(parents=True)
    (d / reg.ACCOUNT_FILE).write_text(json.dumps({"name": "work1", "email": "a@example.com"}))
    return tmp_path / "store"


@pytest.mark.parametrize(
    ("err", "reason"),
    [
        ("An error occurred (ExpiredTokenException) x", "auth_expired"),
        ("An error occurred (AccessDeniedException) not authorized to perform", "no_permission"),
        ("An error occurred (AccessDeniedException) You don't have access to the model", "model_unavailable"),
        ("Could not connect to the endpoint URL", "region_unavailable"),
        ("An error occurred (ValidationException) The provided model identifier is invalid", "model_unavailable"),
        ("boom", "unclassified"),
    ],
)
def test_classify_failure(err, reason):
    assert m.classify_failure(err)[0] == reason


@pytest.mark.parametrize(
    ("status", "body", "want"),
    [
        (200, {}, "ok"),
        (401, {"error": {"type": "authentication_error"}}, "auth_failed"),
        (400, {"error": {"type": "invalid_request_error", "message": "Your credit balance is too low"}}, "credit_low"),
        (500, {"error": {"type": "api_error"}}, "unclassified"),
    ],
)
def test_check_api_key_reads_the_answer(status, body, want):
    assert m.check_api_key("sk", {}, _transport(status, body))[0] == want
    assert m.check_api_key("sk", {}, httpx.MockTransport(_down))[0] == "network"


def test_check_api_key_sends_only_to_the_fixed_endpoint():
    seen = []
    t = httpx.MockTransport(lambda request: seen.append(str(request.url)) or httpx.Response(200, json={}))
    assert m.check_api_key("sk", {"NDF_API_KEY_CHECK_URL": "http://elsewhere.test/v1/messages"}, t)[0] == "ok"
    assert seen == [m.CHECK_URL]


def test_add_api_key_saves_only_after_the_check(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(m.MeteredError) as bad:
        m.add_api_key(store, {}, m.KeyRequest("key1", "work1", "sk-secret", None), _transport(401))
    assert "sk-secret" not in str(bad.value) and not reg.book_path(store).exists()
    row = m.add_api_key(store, {}, m.KeyRequest("key1", "work1", "sk-secret", 100.0), _transport(200))
    assert row["monthly_usd"] == 100.0 and "sk-secret" not in json.dumps(row)
    assert reg.read_book(store)["keys"]["key1"]["key"] == "sk-secret"
    calls: list[httpx.Request] = []
    with pytest.raises(m.MeteredError, match="結び付いている"):
        m.add_api_key(store, {}, m.KeyRequest("key2", "work1", "sk-2", None), httpx.MockTransport(calls.append))
    assert calls == []  # 登録簿だけで断れる要求は、有料の確かめを呼ばずに断る
    with pytest.raises(m.MeteredError, match="重なる"):
        m.add_api_key(store, {}, m.KeyRequest("work1", "work1", "sk-2", None), _transport(200))
    with pytest.raises(m.MeteredError, match="登録済みアカウントでない"):
        m.add_api_key(store, {}, m.KeyRequest("key3", "nobody", "sk-2", None), _transport(200))


def test_monthly_use_and_remove(tmp_path):
    store = _store(tmp_path)
    m.add_api_key(store, {}, m.KeyRequest("key1", "work1", "sk", None), _transport(200))
    assert m.set_monthly(store, "key1", 50.0)["monthly_usd"] == 50.0
    m.set_monthly(store, "key1", None)
    assert "monthly_usd" not in reg.read_book(store)["keys"]["key1"]
    with pytest.raises(m.MeteredError):
        m.set_monthly(store, "nokey", 1.0)
    assert m.set_use(store, None, None) == {"kind": "api_key", "use": "off", "weekly_at": 85.0}
    assert m.set_use(store, "near-limit", 80.0)["weekly_at"] == 80.0
    assert m.set_use(store, None, 90.0)["use"] == "near-limit"
    with pytest.raises(m.MeteredError):
        m.weekly_at("0")
    with pytest.raises(m.MeteredError):
        m.amount("-1")
    assert m.amount("0", allow_zero=True) is None
    assert m.check(store, {}, "key1", _transport(200))["state"] == "使える"
    assert m.remove(store, "key1")["kind"] == "api_key"
    with pytest.raises(m.MeteredError):
        m.remove(store, "metered")


@pytest.fixture
def fake_aws(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "aws"
    exe.write_text(FAKE_AWS)
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return exe


def test_add_bedrock_asks_then_saves_after_the_check(tmp_path, fake_aws, monkeypatch):
    store = tmp_path / "store"
    env = dict(os.environ)
    with pytest.raises(m.MeteredError, match="default, dev"):
        m.add_bedrock(store, env, m.Target("", "", ""), True)
    with pytest.raises(m.MeteredError, match="--yes"):
        m.add_bedrock(store, env, m.Target("dev", "", "anthropic.claude-x"), False)
    row = m.add_bedrock(store, env, m.Target("dev", "", "anthropic.claude-x"), True)
    assert row["details"] == {"profile": "dev", "region": "us-east-1", "model": "anthropic.claude-x"} and not row["replaced"]
    saved = json.loads(reg.metered_path(store).read_text())
    assert saved["env"]["CLAUDE_CODE_USE_BEDROCK"] == "1" and oct(reg.metered_path(store).stat().st_mode)[-3:] == "600"
    assert m.check(store, env, "metered")["state"] == "使える"
    monkeypatch.setenv("FAKE_AWS_DENY", "1")
    with pytest.raises(m.MeteredError) as denied:
        m.check(store, dict(os.environ), "metered")
    assert denied.value.items[0]["reason"] == "model_unavailable"


# プロファイル prod を親の AWS_CONFIG_FILE の先にだけ持つ aws（AWS_CONFIG_FILE が無ければ prod が見つからない）
FILE_ONLY_AWS = """#!/bin/sh
if [ -z "$AWS_CONFIG_FILE" ]; then echo "The config profile (prod) could not be found" >&2; exit 255; fi
echo '{}'
"""


def test_saved_bedrock_is_checked_without_the_parent_aws_files(tmp_path, monkeypatch):
    """保存した設定の子は親の AWS_CONFIG_FILE・AWS_SHARED_CREDENTIALS_FILE を受け継がないので、登録と確かめもそれ無しで aws を呼ぶ（#3604）。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "aws"
    exe.write_text(FILE_ONLY_AWS)
    exe.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "AWS_CONFIG_FILE": str(tmp_path / "config"),
        "AWS_SHARED_CREDENTIALS_FILE": str(tmp_path / "cred"),
    }
    monkeypatch.setenv("PATH", env["PATH"])
    store = tmp_path / "store"
    with pytest.raises(m.MeteredError) as refused:
        m.add_bedrock(store, env, m.Target("prod", "us-east-1", "anthropic.claude-x"), True)
    assert refused.value.items[0]["reason"] == "auth_expired"
    assert not reg.metered_path(store).exists()
    t = m.Target("prod", "us-east-1", "anthropic.claude-x")
    reg.save_metered(store, m.PROVIDER, t.env(), t.details(), 0.0)
    with pytest.raises(m.MeteredError):
        m.check(store, env, "metered")
    # 環境変数の設定の子は親の環境を受け継ぐので、親のファイルのまま確かめる
    declared = {**env, "NDF_SUPERVISE_CLAUDE_FALLBACK": "CLAUDE_CODE_USE_BEDROCK=1 AWS_PROFILE=prod AWS_REGION=us-east-1 ANTHROPIC_MODEL=x"}
    assert m.check(store, declared, "metered")["state"] == "使える"


def test_check_metered_reads_the_env_ndf_settings_first(tmp_path):
    with pytest.raises(m.MeteredError, match="Bedrock"):
        m.check(tmp_path, {"NDF_SUPERVISE_CLAUDE_FALLBACK": "ANTHROPIC_BASE_URL=https://x"}, "metered")
    with pytest.raises(m.MeteredError, match="設定が無い"):
        m.check(tmp_path, {}, "metered")
    with pytest.raises(m.MeteredError, match="metered か"):
        m.check(tmp_path, {}, "nobody")
