"""claude の起動の引数（full・最小構成・json-schema・権限ごとの Tool）と、権限の外の引数を拒む ClaudeCall。"""

from __future__ import annotations

import json

import pytest

from ndf import identity
from ndf.adapters import claude


MINIMAL_HEAD = ["-p", "--output-format", "json", "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]


LISTED = [
    {"id": "ndf@other-market", "enabled": True},
    {"id": "ndf@disabled-market", "enabled": False},
    {"id": "mcp-serena@other-market", "enabled": True},
]


def test_full_args_disable_installed_ndf_and_load_this_plugin(tmp_path, monkeypatch):
    monkeypatch.setattr(claude, "list_plugins", lambda **_: LISTED)
    argv = claude.claude_args(claude.ClaudeCall(system="sys", permission=claude.Permission.FULL), root=tmp_path)

    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings == {"enabledPlugins": {"ndf@other-market": False}}
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


def test_disabled_plugins_follow_the_installed_list_not_a_fixed_name():
    """旧い NDF がどの登録名でも外れ、無効の登録・別の名前・自分の名前は外さない（#3617 の受け入れ条件 13・I10）。"""
    assert claude.disabled_plugins(LISTED, None) == {"ndf@other-market": False}
    assert claude.disabled_plugins(LISTED, identity.FINAL_NAME) == {}
    assert claude.disabled_plugins([{"id": "mcp-serena@x", "enabled": True}], None) == {}
    assert claude.disabled_plugins([], None) == {}


def test_plugin_args_without_old_ndf_adds_no_settings(tmp_path):
    assert claude.plugin_args(tmp_path, lambda: []) == claude.PluginArgs(["--plugin-dir", str(tmp_path)], None)


def test_plugin_args_keeps_going_with_a_notice_when_the_list_is_unreadable(tmp_path):
    """一覧が読めなければ外さずに起動の引数を返し、知らせを入れる（I10a）。"""
    got = claude.plugin_args(tmp_path, lambda: None)
    assert got.args == ["--plugin-dir", str(tmp_path)]
    assert got.notice == claude.LIST_UNREADABLE


class _Done:
    def __init__(self, code: int, out: str) -> None:
        self.returncode, self.stdout = code, out


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        (lambda *a, **k: _Done(0, json.dumps(LISTED)), LISTED),
        (lambda *a, **k: _Done(0, "[]"), []),
        (lambda *a, **k: _Done(1, ""), None),
        (lambda *a, **k: _Done(0, "not json"), None),
        (lambda *a, **k: _Done(0, json.dumps([{"name": "x"}])), None),
    ],
)
def test_list_plugins_reads_the_cli_json(run, expected):
    assert claude.list_plugins(run) == expected


def test_list_plugins_gives_up_on_launch_failure_and_timeout():
    def missing(*a, **k):
        raise FileNotFoundError("claude")

    def slow(*a, **k):
        raise claude.subprocess.TimeoutExpired("claude", 1)

    assert claude.list_plugins(missing) is None
    assert claude.list_plugins(slow) is None


def test_full_args_read_the_installed_list_where_the_worker_starts(tmp_path, monkeypatch):
    """導入済みの一覧は、これから起動する claude と同じ cwd で読む（プロジェクトの設定で一覧が変わる）。"""
    seen = []
    monkeypatch.setattr(claude, "list_plugins", lambda **k: seen.append(k.get("cwd")) or LISTED)
    claude.claude_args(claude.ClaudeCall(system="sys", permission=claude.Permission.FULL), root=tmp_path, cwd=tmp_path / "w")
    assert seen == [tmp_path / "w"]


def test_list_plugins_runs_in_the_given_cwd(tmp_path):
    got = []

    def run(*a, **k):
        got.append(k.get("cwd"))
        return _Done(0, "[]")

    assert claude.list_plugins(run, cwd=tmp_path) == []
    assert got == [tmp_path]
