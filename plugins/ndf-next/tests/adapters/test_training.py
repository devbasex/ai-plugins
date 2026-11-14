"""学習に使わない設定の確認と控え、起動の口が控えで起動を断ること（#3391 の 3391-5）。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from ndf.adapters import agents, claude, llm, training

TOKEN = "oauth-token-value-123456"
NOW = time.time()


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def answering(body: object, status: int = 200, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    return handler


def claude_env(tmp_path: Path, expires_ms: float | None = None) -> dict[str, str]:
    conf = tmp_path / "claude-conf"
    conf.mkdir()
    entry = {"accessToken": TOKEN, **({} if expires_ms is None else {"expiresAt": expires_ms})}
    (conf / training.CLAUDE_CREDENTIALS).write_text(json.dumps({"claudeAiOauth": entry}), encoding="utf-8")
    return {"CLAUDE_CONFIG_DIR": str(conf)}


def codex_env(tmp_path: Path, mode: str = "chatgpt") -> dict[str, str]:
    home = tmp_path / "codex-home"
    home.mkdir()
    auth = {"auth_mode": mode, "tokens": {"access_token": TOKEN, "account_id": "acct-1"}}
    (home / "auth.json").write_text(json.dumps(auth), encoding="utf-8")
    return {"CODEX_HOME": str(home)}


@pytest.mark.parametrize(("grove", "expected"), [(False, False), (True, True), ("no", None)])
def test_claude_reads_grove_enabled_with_oauth_header(tmp_path, grove, expected):
    seen: list[httpx.Request] = []
    got = training.read_claude(claude_env(tmp_path), client_for(answering({"grove_enabled": grove}, seen=seen)), NOW)

    assert got.training is expected
    assert seen[0].method == "GET" and str(seen[0].url) == training.CLAUDE_URL
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in json.dumps(got.item("t"))


def test_claude_token_env_wins_over_credentials_file(tmp_path):
    seen: list[httpx.Request] = []
    env = {**claude_env(tmp_path), training.CLAUDE_TOKEN_ENV: "env-token-abcdefgh"}

    training.read_claude(env, client_for(answering({"grove_enabled": False}, seen=seen)), NOW)

    assert seen[0].headers["Authorization"] == "Bearer env-token-abcdefgh"


@pytest.mark.parametrize(
    ("make_env", "reason"),
    [
        (lambda p: {**claude_env(p), "ANTHROPIC_API_KEY": "sk-x"}, "ANTHROPIC_API_KEY"),
        (lambda p: claude_env(p, expires_ms=(NOW - 60) * 1000), "期限切れ"),
        (lambda p: {"CLAUDE_CONFIG_DIR": str(p / "none")}, training.NO_TOKEN),
    ],
)
def test_claude_without_usable_oauth_is_unread_and_sends_nothing(tmp_path, make_env, reason):
    seen: list[httpx.Request] = []
    got = training.read_claude(make_env(tmp_path), client_for(answering({"grove_enabled": False}, seen=seen)), NOW)

    assert got.training is None and reason in got.reason
    assert seen == []
    assert "sk-x" not in got.reason


@pytest.mark.parametrize(
    ("handler", "reason"),
    [
        (answering({"grove_enabled": False}, status=401), "HTTP 401"),
        (lambda request: httpx.Response(200, text="<html>"), "JSON でない"),
        (lambda request: (_ for _ in ()).throw(httpx.ConnectError("down")), "通信の失敗"),
    ],
)
def test_claude_http_failures_are_unread(tmp_path, handler, reason):
    got = training.read_claude(claude_env(tmp_path), client_for(handler), NOW)

    assert got.training is None and reason in got.reason


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({"training_allowed": False, "codex_training_allowed": False}, False),
        ({"training_allowed": False, "codex_training_allowed_v2": True}, True),
        ({"training_allowed": True}, True),
        ({"training_allowed": False, "codex_training_allowed": "x"}, None),
        ({}, None),
    ],
)
def test_codex_reads_three_keys_with_account_header(tmp_path, settings, expected):
    seen: list[httpx.Request] = []
    got = training.read_codex(codex_env(tmp_path), client_for(answering({"settings": settings}, seen=seen)), NOW)

    assert got.training is expected
    assert str(seen[0].url) == training.CODEX_URL
    assert seen[0].headers["ChatGPT-Account-Id"] == "acct-1"
    assert seen[0].headers["User-Agent"] == training.CODEX_USER_AGENT


def test_codex_api_key_login_is_unread(tmp_path):
    got = training.read_codex(codex_env(tmp_path, mode="apikey"), client_for(answering({})), NOW)

    assert got.training is None and got.reason == training.NOT_CHATGPT


def test_check_saves_readings_and_is_ok_only_when_all_off(tmp_path):
    env = {**claude_env(tmp_path), **codex_env(tmp_path)}

    def handler(request: httpx.Request) -> httpx.Response:
        if "chatgpt" in str(request.url):
            return httpx.Response(200, json={"settings": {"training_allowed": True}})
        return httpx.Response(200, json={"grove_enabled": False})

    got = training.check(["claude", "codex"], env, client_for(handler), NOW)

    assert got["status"] == "stopped" and got["cause"]["kind"] == "refused" and "codex" in got["summary"]
    saved = json.loads(training.cache_path().read_text(encoding="utf-8"))
    assert saved["claude"]["training"] is False and saved["codex"]["training"] is True
    assert TOKEN not in training.cache_path().read_text(encoding="utf-8") + json.dumps(got)
    assert training.refusal("claude", NOW) is None
    assert "学習に使う" in training.refusal("codex", NOW)


def test_check_reports_unread_as_precondition(tmp_path):
    got = training.check(["claude"], {"CLAUDE_CONFIG_DIR": str(tmp_path)}, client_for(answering({})), NOW)

    assert got["status"] == "stopped" and got["cause"]["kind"] == "precondition"
    assert got["items"][0]["training"] is None


def test_check_all_off_is_ok(tmp_path):
    got = training.check(["claude"], claude_env(tmp_path), client_for(answering({"grove_enabled": False})), NOW)

    assert got["status"] == "ok" and got["items"][0]["runtime"] == "claude"


@pytest.mark.parametrize(
    ("entry", "said"),
    [
        (None, "控えが無い"),
        ({"training": False, "reason": "x", "checked_at": "2000-01-01T00:00:00+00:00"}, "期限切れ"),
        ({"training": None, "reason": "HTTP 500", "checked_at": None}, "時刻が読めない"),
        ({"training": None, "reason": "HTTP 500", "checked_at": training._stamp(NOW)}, "HTTP 500"),
        ({"training": True, "reason": "x", "checked_at": training._stamp(NOW)}, "学習に使う"),
    ],
)
def test_refusal_names_the_check_command(entry, said):
    path = training.cache_path()
    path.write_text(json.dumps({} if entry is None else {"claude": entry}), encoding="utf-8")

    why = training.refusal("claude", NOW)

    assert said in why and training.CHECK_COMMAND in why


def test_refusal_of_unreadable_cache():
    training.cache_path().write_text("{", encoding="utf-8")

    assert "読めない" in training.refusal("codex", NOW)


@pytest.mark.parametrize("runtime", ["kiro", "agy"])
def test_unsupported_runtimes_launch_without_cache(runtime):
    training.cache_path().unlink()

    assert training.refusal(runtime, NOW) is None


def test_call_claude_refuses_before_launch_without_cache(marker_launch):
    training.cache_path().unlink()
    marker, argv, _ = marker_launch("claude")

    got = claude.call_claude(argv, "q", 30)

    assert got.failure is claude.Failure.REFUSED and training.CHECK_COMMAND in got.error
    assert not marker.exists()


def test_run_agent_refuses_codex_without_cache(marker_launch):
    training.cache_path().unlink()
    marker, _, spec = marker_launch("codex")

    status = agents.run_agent(spec, poll=0.05)

    assert status.state == agents.AgentState.REFUSED and training.CHECK_COMMAND in status.detail
    assert not marker.exists()


def test_judge_maps_every_failure_to_a_cause():
    assert set(llm.FAILURE_CAUSES) == set(claude.Failure)
    assert llm.FAILURE_CAUSES[claude.Failure.REFUSED] == "precondition"
    assert llm.FAILURE_CAUSES[claude.Failure.NO_SLOT] == "infra_wait"


def test_main_rejects_unknown_runtime(capsys):
    code = training.main(["check", "--runtime", "kiro"])

    out = json.loads(capsys.readouterr().out)
    assert code != 0 and out["cause"]["kind"] == "invalid"
