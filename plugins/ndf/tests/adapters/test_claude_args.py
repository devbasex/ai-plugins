"""claude の起動の引数（full・最小構成・json-schema・権限ごとの Tool）と、権限の外の引数を拒む ClaudeCall。"""

from __future__ import annotations

import json

import pytest

from ndf import identity
from ndf.adapters import claude, claude_precheck


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
    """読める場所を渡すと `--restricted`・`--add-dir`・`dontAsk` を組み、`acceptEdits` を使わない。Tool の値は名前だけ。渡さなければ
    今の引数のまま。`ClaudeCall` を通しても同じで、読める場所は Tool を限った起動（`Permission.TOOLS`）にしか渡せない。"""
    place = tmp_path / "review-input-x"
    got = claude.permission_args(claude.Permission.TOOLS, "Read,Grep,Glob", (place,))
    assert got == [
        "--tools", "Read,Grep,Glob", "--allowed-tools", "Read,Grep,Glob",
        "--restricted", "--add-dir", str(place), "--permission-mode", "dontAsk",
    ]  # fmt: skip
    assert "acceptEdits" not in got
    assert claude.permission_args(claude.Permission.TOOLS, "Read") == [
        "--tools", "Read", "--allowed-tools", "Read", "--permission-mode", "acceptEdits",
    ]  # fmt: skip
    argv = claude.claude_args(claude.ClaudeCall(system="s", permission=claude.Permission.TOOLS, tools="Read", readable=(tmp_path,)))
    assert argv[argv.index("--add-dir") + 1] == str(tmp_path) and argv[-2:] == ["--permission-mode", "dontAsk"]
    with pytest.raises(ValueError):
        claude.ClaudeCall(system="s", readable=(tmp_path,))


# ---------------------------------------------------------------- #1959: full の起動を承認した Tool と MCP に絞る


@pytest.mark.parametrize("resume", [True, False])
def test_issue_1959_full_launch_keeps_the_user_settings_and_loads_only_the_listed_tools(tmp_path, monkeypatch, resume):
    """#1959: full の起動は利用者の設定を読み（`--setting-sources` を付けない）、読み込む Tool を 16 個に絞り、同梱の 13 個を 1 件 1 引数で
    尋ねずに許し、環境の MCP を読まない（`--strict-mcp-config`）。会話の指定は最後に置く。Skill・サブエージェント・編集の Tool は残る。"""
    monkeypatch.setattr(claude, "list_plugins", lambda **_: [])
    session = claude.Session("abc", resume)
    argv = claude.claude_args(claude.ClaudeCall(system="sys", permission=claude.Permission.FULL, session=session), root=tmp_path)

    assert "--setting-sources" not in argv and "--mcp-config" not in argv
    allowed = claude.FULL_TOOLS.split(",")
    loaded = claude.LOAD_TOOLS.split(",")
    assert len(allowed) == 13 and len(loaded) == 16 and {"Skill", "Agent", "Edit", "Write"} <= set(allowed) <= set(loaded)
    assert argv[1:6] == [*claude.PRINT_JSON, "--permission-mode", "acceptEdits"]
    assert argv[6:8] == ["--tools", claude.LOAD_TOOLS]
    assert argv[8 : 9 + len(allowed)] == ["--allowed-tools", *allowed]
    assert argv[9 + len(allowed)] == "--strict-mcp-config"
    assert argv[-4:] == ["--append-system-prompt", "sys", *claude.session_args(session)]


class _Grant:
    def __init__(self, load=(), allow=(), inherit=False, mcp_files=()):
        self.load, self.allow, self.inherit, self.mcp_files = tuple(load), tuple(allow), inherit, tuple(mcp_files)


def _full(grant, tmp_path, monkeypatch):
    monkeypatch.setattr(claude, "list_plugins", lambda **_: [])
    return claude.claude_args(claude.ClaudeCall(system="sys", permission=claude.Permission.FULL, grant=grant), root=tmp_path)


def _values(argv, flag):
    """値を複数取る引数 `flag` の値（次の `--` で始まる引数の前まで）。"""
    at = argv.index(flag) + 1
    end = next(i for i in range(at, len(argv)) if argv[i].startswith("--"))
    return argv[at:end]


def test_issue_1959_load_goes_only_to_tools_and_allow_only_to_allowed_tools(tmp_path, monkeypatch):
    """条件 12: `load` の名前は `--tools` だけに、`allow` の指定は `--allowed-tools` だけに入る。空白を含む指定も 1 つの引数になる。"""
    argv = _full(_Grant(load=("LSP",), allow=("Bash(git log:*)", "mcp__search__query")), tmp_path, monkeypatch)
    assert _values(argv, "--tools") == [claude.LOAD_TOOLS + ",LSP"]
    assert _values(argv, "--allowed-tools") == [*claude.FULL_TOOLS.split(","), "Bash(git log:*)", "mcp__search__query"]
    assert "--strict-mcp-config" in argv and "--mcp-config" not in argv


def test_issue_1959_an_empty_grant_is_the_same_as_no_grant(tmp_path, monkeypatch):
    assert _full(_Grant(), tmp_path, monkeypatch) == _full(None, tmp_path, monkeypatch)


def test_issue_1959_definition_files_go_to_mcp_config_with_strict(tmp_path, monkeypatch):
    """条件 4・5: 定義ファイルを指定した工程は、環境の MCP を読まずに指定の定義（コピー）だけを `--mcp-config` で読む。"""
    copies = (tmp_path / "0.json", tmp_path / "1.json")
    argv = _full(_Grant(mcp_files=copies), tmp_path, monkeypatch)
    at = argv.index("--strict-mcp-config")
    assert argv[at + 1 : at + 4] == ["--mcp-config", str(copies[0]), str(copies[1])]
    assert argv[at + 4].startswith("--")  # 値を複数取る引数の終わり（プラグインの引数か --append-system-prompt）


def test_issue_1959_inherit_reads_the_environment_mcp(tmp_path, monkeypatch):
    """`inherit` の工程は `--strict-mcp-config` を付けず、環境の MCP をそのまま読む。Tool の絞り込みは残る。"""
    argv = _full(_Grant(inherit=True), tmp_path, monkeypatch)
    assert "--strict-mcp-config" not in argv and "--mcp-config" not in argv and "--tools" in argv


@pytest.mark.parametrize("permission", [claude.Permission.JUDGE, claude.Permission.TOOLS])
def test_issue_1959_a_grant_is_only_for_the_full_launch(permission):
    tools = "Read" if permission is claude.Permission.TOOLS else None
    with pytest.raises(ValueError):
        claude.ClaudeCall(system="sys", permission=permission, tools=tools, grant=_Grant())


def _launch(monkeypatch, verify):
    """スロット・下限・実プロセスを差し替えて `call_claude` を 1 回打ち、起動した子の数と回答を返す。"""
    from contextlib import contextmanager

    @contextmanager
    def slot(agent, *, cwd=None, env=None, wait=None, ensure=True):
        yield {}

    launched: list[list[str]] = []
    monkeypatch.setattr(claude.launch_slot, "gate", slot)
    monkeypatch.setattr(claude_precheck.cli_floor, "require", lambda *a, **k: None)
    monkeypatch.setattr(claude, "_run_claude", lambda argv, *a: launched.append(list(argv)) or claude.ClaudeReply(True, None, "", 0.0))
    monkeypatch.setattr(claude, "relay_checks", None)
    reply = claude.call_claude(["claude", "-p"], "質問", 1.0, options=claude.LaunchOptions(verify=verify))
    return launched, reply


def test_issue_1959_the_check_right_before_the_launch_stops_a_changed_approval(monkeypatch):
    """条件 11・I5: 子を起動する直前の照らし直しが理由を返せば起動せず `Failure.LAUNCH`（停止理由は precondition）。"""
    launched, reply = _launch(monkeypatch, lambda: "承認の後に変わった")
    assert launched == [] and reply.failure is claude.Failure.LAUNCH and reply.error == "承認の後に変わった"
    launched, reply = _launch(monkeypatch, lambda: None)
    assert len(launched) == 1 and reply.ok
