from __future__ import annotations

import json
from pathlib import Path

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
