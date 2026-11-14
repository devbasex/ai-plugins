from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ndf import identity
from ndf.adapters import cli

MINIMAL_HEAD = ["-p", "--output-format", "json", "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]


def test_full_args_disable_installed_ndf_and_load_this_plugin(tmp_path):
    argv = cli.claude_args(cli.ClaudeCall(system="sys", full=True), root=tmp_path)

    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings == {"enabledPlugins": cli.DISABLED_PLUGINS}
    assert all(enabled is False for enabled in cli.DISABLED_PLUGINS.values())
    assert argv[argv.index("--plugin-dir") + 1] == str(tmp_path)
    assert argv[0] == cli.CLAUDE and "--append-system-prompt" in argv


def test_full_args_default_to_own_root():
    argv = cli.claude_args(cli.ClaudeCall(system="sys", full=True, resume="abc"))

    assert argv[argv.index("--plugin-dir") + 1] == str(identity.load().root)
    assert argv[-2:] == ["--resume", "abc"]


def test_minimal_args_stay_minimal_without_plugin_settings():
    no_tools = cli.claude_args(cli.ClaudeCall(system="sys"))
    with_tools = cli.claude_args(cli.ClaudeCall(system="sys", tools="Read,Grep", model="m"), base=("x", "y"))

    assert no_tools == ["claude", *MINIMAL_HEAD, "--disable-slash-commands", "--system-prompt", "sys", "--tools", ""]
    assert with_tools[:2] == ["x", "y"] and with_tools[-2:] == ["--model", "m"]
    for argv in (no_tools, with_tools):
        assert "--settings" not in argv and "--plugin-dir" not in argv


def test_json_schema_is_passed_as_json():
    argv = cli.claude_args(cli.ClaudeCall(system="sys", json_schema={"type": "object"}))

    assert json.loads(argv[argv.index("--json-schema") + 1]) == {"type": "object"}


def test_launch_args_subcommand_prints_the_same_values(capsys):
    assert cli.main([]) == 0

    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "ok"
    assert out["items"] == [{"args": cli.plugin_args(identity.load().root)}]


def test_launch_args_refuses_arguments(capsys):
    assert cli.main(["x"]) != 0
    assert json.loads(capsys.readouterr().out)["cause"]["kind"] == "invalid"


def test_call_claude_reads_structured_output(tmp_path):
    fake = tmp_path / "claude"
    payload = {"result": "", "structured_output": {"decision": "fix"}, "total_cost_usd": 0.25}
    fake.write_text(f"#!/bin/sh\ncat > /dev/null\necho '{json.dumps(payload)}'\n")
    fake.chmod(0o755)

    got = cli.call_claude([str(fake)], "問い", 30)

    assert got == cli.ClaudeReply(True, {"decision": "fix"}, "", 0.25)


def test_call_claude_failures_are_not_ok(tmp_path):
    assert not cli.call_claude([str(tmp_path / "missing")], "問い", 30).ok
    assert not cli._parse_reply("not json").ok
    assert not cli._parse_reply(json.dumps({"is_error": True, "result": "boom"})).ok
    assert cli._parse_reply(json.dumps({"result": '{"a": 1}'})).answer == {"a": 1}
    assert cli._parse_reply(json.dumps({"result": "fix です"})).answer is None


def test_agent_paths_at_names_files_next_to_stem():
    paths = cli.AgentPaths.at(Path("/w/kiro"))

    assert (paths.out.name, paths.err.name, paths.launch.name) == ("kiro.out", "kiro.err.log", "kiro-launch.json")


# ---------------------------------------------------------------- 現状固定: 子へ渡す環境と失敗の形（I-011・I-012）


@pytest.fixture
def no_run_env(monkeypatch):
    """実行の記録を開いていない状態から始め、終わったら手放す。"""
    for name in (cli.ENV_RUN_ID, cli.ENV_RUN_LOG, cli.ENV_RUN_LOG_SINK):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PIN_PARENT", "kept")
    cli.close()
    yield
    cli.close()


ENV_DUMP = (
    "import json, os, sys\n"
    "prompt = sys.stdin.read()\n"
    "seen = {k: os.environ.get(k) for k in ('NDF_RUN_ID', 'NDF_RUN_LOG', 'PIN_PARENT')}\n"
    "print(json.dumps({'result': json.dumps({'prompt': prompt, 'cwd': os.getcwd(), 'env': seen})}))\n"
)


def test_call_claude_passes_parent_env_prompt_and_cwd_without_run_log(tmp_path, no_run_env):
    got = cli.call_claude([sys.executable, "-c", ENV_DUMP], "問い", 30, cwd=tmp_path)

    assert (got.ok, got.cost, got.error) == (True, 0.0, None)
    env = {"NDF_RUN_ID": None, "NDF_RUN_LOG": None, "PIN_PARENT": "kept"}
    assert got.answer == {"prompt": "問い", "cwd": str(tmp_path), "env": env}


def test_call_claude_passes_open_run_log_to_child(no_run_env):
    log = cli.open_run("entry", run_id="r1", location="repo")

    got = cli.call_claude([sys.executable, "-c", ENV_DUMP], "q", 30)

    assert got.answer["env"] == {"NDF_RUN_ID": "r1", "NDF_RUN_LOG": str(log.path), "PIN_PARENT": "kept"}


def test_call_claude_nonzero_exit_without_stdout_reports_stderr(no_run_env):
    script = "import sys; sys.stdin.read(); sys.stderr.write('boom'); sys.exit(3)"

    got = cli.call_claude([sys.executable, "-c", script], "q", 30)

    assert got == cli.ClaudeReply(False, None, "boom", 0.0, "claude が終了コード 3 で終えた: boom")


def test_call_claude_nonzero_exit_with_stdout_still_parses(no_run_env):
    script = 'import sys; sys.stdin.read(); print(\'{"result": "{}", "total_cost_usd": 1}\'); sys.exit(1)'

    got = cli.call_claude([sys.executable, "-c", script], "q", 30)

    assert got == cli.ClaudeReply(True, {}, "{}", 1.0)


def test_call_claude_timeout_is_not_ok(no_run_env):
    got = cli.call_claude([sys.executable, "-c", "import time; time.sleep(5)"], "q", 0.2)

    assert (got.ok, got.answer, got.raw, got.cost) == (False, None, "", 0.0)
    assert got.error.startswith("claude を起動できない・期限を超えた: ")
