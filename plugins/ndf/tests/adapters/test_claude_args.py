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
    argv = claude.claude_args(claude.ClaudeCall(system="sys", permission=claude.Permission.FULL, session=claude.Session("abc", True)))

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


@pytest.mark.parametrize("permission", [claude.Permission.TOOLS])
def test_stream_prints_one_line_per_step_with_verbose(permission, tmp_path):
    # #3945: 結果をファイルで受ける担当は逐次の出力で起動し、作業中に標準出力が増える（--verbose が無いと起動できない）
    tools = "Read" if permission is claude.Permission.TOOLS else None
    argv = claude.claude_args(claude.ClaudeCall(system="sys", permission=permission, tools=tools, stream=True), root=tmp_path)
    plain = claude.claude_args(claude.ClaudeCall(system="sys", permission=permission, tools=tools), root=tmp_path)
    at = argv.index("--output-format")
    assert argv[at + 1 : at + 3] == ["stream-json", "--verbose"]
    assert plain[plain.index("--output-format") + 1] == "json" and "--verbose" not in plain


def test_issue_4013_session_args_start_or_continue_a_conversation():
    """#4013: 続ける指定は `--resume <ID>`、新しい会話は `--session-id <ID>`、指定が無ければ空。"""
    assert claude.session_args(claude.Session("abc", True)) == ["--resume", "abc"]
    assert claude.session_args(claude.Session("abc", False)) == ["--session-id", "abc"]
    assert claude.session_args(None) == []


@pytest.mark.parametrize("permission", [claude.Permission.TOOLS, claude.Permission.JUDGE])
@pytest.mark.parametrize("resume", [True, False])
def test_issue_4013_minimal_launch_with_a_session_keeps_the_conversation(permission, resume):
    """#4013: 最小構成に会話の指定を渡すと `--no-session-persistence` を付けず、`--system-prompt` の後ろに会話の引数を置く。"""
    tools = "Read" if permission is claude.Permission.TOOLS else None
    session = claude.Session("abc", resume)
    argv = claude.claude_args(claude.ClaudeCall(system="sys", permission=permission, tools=tools, session=session))
    assert "--no-session-persistence" not in argv
    at = argv.index("--system-prompt")
    assert argv[at : at + 4] == ["--system-prompt", "sys", *claude.session_args(session)]


def test_issue_4013_minimal_args_without_a_session_are_unchanged():
    """#4013: 会話の指定を渡さない最小構成の引数は今のまま（`--no-session-persistence` つき・会話の引数なし）。"""
    assert claude.minimal_args("sys") == [*MINIMAL_HEAD, "--disable-slash-commands", "--system-prompt", "sys"]
    assert claude.minimal_args("sys", session=None) == claude.minimal_args("sys")


def test_json_schema_is_passed_as_json():
    argv = claude.claude_args(claude.ClaudeCall(system="sys", json_schema={"type": "object"}))

    assert json.loads(argv[argv.index("--json-schema") + 1]) == {"type": "object"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"permission": claude.Permission.TOOLS},
        {"permission": claude.Permission.JUDGE, "tools": "Read"},
        {"permission": claude.Permission.FULL, "tools": "Read"},
    ],
)
def test_call_refuses_tools_outside_their_permission(kwargs):
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


def test_plugin_args_skips_the_list_when_own_name_is_the_final_name(tmp_path):
    """自分の名前が入れ替え後の名前なら外す登録は必ず無いので、一覧を読まない（`claude plugin list` を打たない）。"""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": identity.FINAL_NAME}), encoding="utf-8")

    def never() -> None:
        raise AssertionError("一覧を読んだ")

    assert claude.plugin_args(tmp_path, never) == claude.PluginArgs(["--plugin-dir", str(tmp_path)], None)


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


def test_full_launch_carries_the_notice_when_the_list_is_unreadable(tmp_path, monkeypatch):
    """full の起動は一覧を読めなかった知らせを引数と一緒に返す（I10a）。full でない起動は一覧を読まず知らせも無い。"""
    monkeypatch.setattr(claude, "list_plugins", lambda **k: None)
    full = claude.claude_launch(claude.ClaudeCall(system="sys", permission=claude.Permission.FULL), root=tmp_path)
    assert full.notice == claude.LIST_UNREADABLE
    assert full.args == claude.claude_args(claude.ClaudeCall(system="sys", permission=claude.Permission.FULL), root=tmp_path)
    assert claude.claude_launch(claude.ClaudeCall(system="sys")).notice is None


# ---------------------------------------------------------------- #3955: 読める場所を限った読むだけの起動


def test_issue_3955_readable_places_restrict_the_launch(tmp_path):
    """読める場所を渡すと `--restricted`・`--add-dir`・`dontAsk` を組み、`acceptEdits` を使わない。Tool の値は名前だけ。"""
    place = tmp_path / "review-input-x"
    got = claude.permission_args(claude.Permission.TOOLS, "Read,Grep,Glob", (place,))
    assert got == [
        "--tools", "Read,Grep,Glob", "--allowed-tools", "Read,Grep,Glob",
        "--restricted", "--add-dir", str(place), "--permission-mode", "dontAsk",
    ]  # fmt: skip
    assert "acceptEdits" not in got


def test_issue_3955_no_readable_places_keep_the_current_args():
    assert claude.permission_args(claude.Permission.TOOLS, "Read") == [
        "--tools",
        "Read",
        "--allowed-tools",
        "Read",
        "--permission-mode",
        "acceptEdits",
    ]


def test_issue_3955_readable_places_go_through_claude_call(tmp_path):
    argv = claude.claude_args(claude.ClaudeCall(system="s", permission=claude.Permission.TOOLS, tools="Read", readable=(tmp_path,)))
    assert argv[argv.index("--add-dir") + 1] == str(tmp_path) and argv[-2:] == ["--permission-mode", "dontAsk"]


def test_issue_3955_readable_places_need_the_tools_permission(tmp_path):
    with pytest.raises(ValueError):
        claude.ClaudeCall(system="s", readable=(tmp_path,))
