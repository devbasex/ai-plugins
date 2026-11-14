"""claude の起動の引数（full・最小構成・json-schema・権限ごとの Tool）と、権限の外の引数を拒む ClaudeCall。"""

from __future__ import annotations

import json

import pytest

from ndf import identity
from ndf.adapters import claude


MINIMAL_HEAD = ["-p", "--output-format", "json", "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]


def test_full_args_disable_installed_ndf_and_load_this_plugin(tmp_path):
    argv = claude.claude_args(claude.ClaudeCall(system="sys", permission=claude.Permission.FULL), root=tmp_path)

    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings == {"enabledPlugins": claude.DISABLED_PLUGINS}
    assert all(enabled is False for enabled in claude.DISABLED_PLUGINS.values())
    assert argv[argv.index("--plugin-dir") + 1] == str(tmp_path)
    assert argv[0] == claude.CLAUDE and "--append-system-prompt" in argv


def test_full_args_default_to_own_root():
    argv = claude.claude_args(claude.ClaudeCall(system="sys", permission=claude.Permission.FULL, resume="abc"))

    assert argv[argv.index("--plugin-dir") + 1] == str(identity.load().root)
    assert argv[-2:] == ["--resume", "abc"]


def test_minimal_args_stay_minimal_without_plugin_settings():
    no_tools = claude.claude_args(claude.ClaudeCall(system="sys"))
    with_tools = claude.claude_args(
        claude.ClaudeCall(system="sys", permission=claude.Permission.TOOLS, tools="Read,Grep", model="m"), base=("x", "y")
    )

    assert no_tools == ["claude", *MINIMAL_HEAD, "--disable-slash-commands", "--system-prompt", "sys", "--tools", ""]
    assert with_tools[:2] == ["x", "y"] and with_tools[-2:] == ["--model", "m"]
    for argv in (no_tools, with_tools):
        assert "--settings" not in argv and "--plugin-dir" not in argv


def test_json_schema_is_passed_as_json():
    argv = claude.claude_args(claude.ClaudeCall(system="sys", json_schema={"type": "object"}))

    assert json.loads(argv[argv.index("--json-schema") + 1]) == {"type": "object"}


DANGEROUS = ("--dangerously-skip-permissions", "bypassPermissions")


def test_each_permission_has_its_own_tools_and_never_skips_permissions(tmp_path):
    """判断は Tool を使わせず、Tool を許す起動も acceptEdits までにする（#3364）。"""
    judge = claude.claude_args(claude.ClaudeCall(system="sys"))
    tools = claude.claude_args(claude.ClaudeCall(system="sys", permission=claude.Permission.TOOLS, tools="Read"))
    full = claude.claude_args(claude.ClaudeCall(system="sys", permission=claude.Permission.FULL), root=tmp_path)

    assert judge[judge.index("--tools") + 1] == "" and "--permission-mode" not in judge and "--allowed-tools" not in judge
    assert tools[tools.index("--allowed-tools") + 1] == "Read" and tools[tools.index("--permission-mode") + 1] == "acceptEdits"
    assert full[full.index("--allowed-tools") + 1] == claude.FULL_TOOLS and full[full.index("--permission-mode") + 1] == "acceptEdits"
    for argv in (judge, tools, full):
        assert not set(argv) & set(DANGEROUS)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"permission": claude.Permission.TOOLS},
        {"permission": claude.Permission.JUDGE, "tools": "Read"},
        {"permission": claude.Permission.FULL, "tools": "Read"},
        {"permission": claude.Permission.JUDGE, "resume": "abc"},
    ],
)
def test_call_refuses_tools_or_resume_outside_their_permission(kwargs):
    with pytest.raises(ValueError):
        claude.ClaudeCall(system="sys", **kwargs)
