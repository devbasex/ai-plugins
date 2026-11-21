"""中継の部品: ブロックの読み・シグナルファイル・中継のマーカー・子の環境・アカウント・ラッパーの見分け・会話の記録。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ndf.contracts import facts
from ndf.domains.relay import account_registry, accounts, blocks, markers, observe, registry, session_env, signals
from ndf.domains.relay import tmux as relay_tmux
from ndf.domains.relay.account_decide import Usage

FENCE = "ndf-next"


def test_fenced_blocks_are_read_through_the_markdown_adapter():
    text = f"前置き\n\n```{FENCE}\n/goal A\n```\n```python\nx = 1\n```\n~~~{FENCE}\n/goal B\n~~~\n"
    assert blocks.fenced(text, FENCE) == ["/goal A", "/goal B"]
    assert blocks.last_block(text, FENCE) == "/goal B"
    assert blocks.last_block("本文だけ", FENCE) is None
    assert blocks.fenced(f"```{FENCE}\n/goal A\n続き", FENCE) == ["/goal A\n続き"]


def test_next_mark_keeps_the_first_written_time(tmp_path, monkeypatch):
    # Stop のたびに書き直しても、最初に書いた時刻を保つ（#2438）
    monkeypatch.setattr(signals, "now_iso", lambda: "2026-11-14T00:00:00+00:00")
    assert signals.write_next(tmp_path, "s1", "/goal x", True, 0.1)
    monkeypatch.setattr(signals, "now_iso", lambda: "2026-11-14T00:05:00+00:00")
    assert signals.write_next(tmp_path, "s1", "/goal y", True, 0.1)
    got = signals.read_next(tmp_path)
    assert (got["block"], got["first_written_at"], got["written_at"]) == (
        "/goal y",
        "2026-11-14T00:00:00+00:00",
        "2026-11-14T00:05:00+00:00",
    )
    assert not list(tmp_path.glob(".*.tmp"))


def test_broken_next_mark_is_an_error_not_the_current_time(tmp_path):
    (tmp_path / signals.NEXT_FILE).write_text('{"block": "/goal x"}', encoding="utf-8")
    with pytest.raises(signals.SignalError):
        signals.read_next(tmp_path)
    (tmp_path / signals.NEXT_FILE).write_text("{", encoding="utf-8")
    with pytest.raises(signals.SignalError):
        signals.read_next(tmp_path)
    signals.drop(tmp_path, signals.NEXT_FILE)
    assert signals.read_next(tmp_path) is None


def test_signal_write_gives_up_when_the_lock_is_held(tmp_path, hold_lock):
    fd = hold_lock(tmp_path / f"{signals.NEXT_FILE}.lock")
    try:
        assert not signals.write_next(tmp_path, "s1", "/goal x", False, 0.05)
    finally:
        os.close(fd)


def test_question_and_locations_from_the_environment(tmp_path):
    payload = {"tool_use_id": "t", "tool_input": {"questions": [{"question": "q"}]}, "cwd": "/w"}
    assert signals.write_question(tmp_path / signals.QUESTION_FILE, "s1", payload, 0.1)
    assert signals.has_question(tmp_path)
    assert json.loads((tmp_path / signals.QUESTION_FILE).read_text(encoding="utf-8"))["questions"] == [{"question": "q"}]
    assert signals.relay_dir({}) is None and signals.relay_dir({"NDF_RELAY_DIR": str(tmp_path / "none")}) is None
    assert signals.relay_dir(signals.launch_env(tmp_path, "L1")) == tmp_path
    assert signals.organizer_question_path({"NDF_ORGANIZER_DIR": "/o", "NDF_ORGANIZER_LANE": "l1"}) == Path("/o/questions/l1.json")
    assert signals.organizer_question_path({"NDF_ORGANIZER_DIR": "/o", "NDF_ORGANIZER_LANE": "../x"}) is None


def test_launch_env_passes_the_launch_id_and_the_hook_reads_it(tmp_path):
    # 子へ起動の ID を渡し、hook が環境から読む（#3814）
    env = signals.launch_env(tmp_path, "L1")
    assert env == {"NDF_RELAY_DIR": str(tmp_path), "NDF_RELAY_LAUNCH": "L1"}
    assert signals.relay_launch(env) == "L1"
    assert signals.relay_launch({"NDF_RELAY_DIR": str(tmp_path)}) is None
    assert signals.relay_launch({"NDF_RELAY_LAUNCH": ""}) is None


def test_current_launch_is_none_when_missing_cleared_or_broken(tmp_path):
    # 今の起動の記録が無い・外した・壊れているときは None（書く側は書かない側へ倒れる。#3814）
    assert signals.current_launch(tmp_path) is None
    signals.set_launch(tmp_path, "L1")
    assert signals.current_launch(tmp_path) == "L1"
    signals.set_launch(tmp_path, None)
    assert signals.current_launch(tmp_path) is None
    (tmp_path / signals.LAUNCH_FILE).write_text("{", encoding="utf-8")
    assert signals.current_launch(tmp_path) is None
    (tmp_path / signals.LAUNCH_FILE).write_bytes(b'{"launch": "\xff"}')  # 不正な UTF-8
    assert signals.current_launch(tmp_path) is None


QUESTION = {"tool_use_id": "t", "tool_input": {"questions": [{"question": "q"}]}, "cwd": "/w"}


def test_i7_question_is_written_with_the_launch_only_for_the_current_launch(tmp_path):
    # I7 の前提・受け入れ条件 10: 今の起動の記録と同じ起動の ID のときだけ書き、質問に launch を残す
    signals.set_launch(tmp_path, "L1")
    assert signals.write_question(tmp_path / signals.QUESTION_FILE, "s1", QUESTION, 0.1, launch="L1")
    assert json.loads((tmp_path / signals.QUESTION_FILE).read_text(encoding="utf-8"))["launch"] == "L1"


@pytest.mark.parametrize(("recorded", "launch"), [("L2", "L1"), ("L1", None), (None, "L1"), ("missing", "L1"), ("broken", "L1")])
def test_i7_question_is_not_written_for_another_launch_or_without_a_launch(tmp_path, recorded, launch):
    # I7 の前提・受け入れ条件 10: 起動の ID が違う・None・今の起動が無い・記録が壊れていれば書かずに偽、前の中身は変わらない
    path = tmp_path / signals.QUESTION_FILE
    path.write_text('{"tool_use_id": "before"}', encoding="utf-8")
    if recorded == "broken":
        (tmp_path / signals.LAUNCH_FILE).write_text("{", encoding="utf-8")
    elif recorded != "missing":
        signals.set_launch(tmp_path, recorded)
    assert not signals.write_question(path, "s1", QUESTION, 0.1, launch=launch)
    assert path.read_text(encoding="utf-8") == '{"tool_use_id": "before"}'


def test_question_outside_the_relay_is_written_without_the_launch_check(tmp_path):
    # launch を渡さない書き手（オーガナイザーの質問の保存先）は今と同じく照らさずに書く
    assert signals.write_question(tmp_path / "l1.json", "s1", QUESTION, 0.1)
    assert "launch" not in json.loads((tmp_path / "l1.json").read_text(encoding="utf-8"))


def test_write_json_checks_allow_inside_the_lock(tmp_path):
    seen = []

    def allow():
        fd = os.open(tmp_path / "x.json.lock", os.O_RDWR)
        try:
            seen.append(signals.lock(fd, 0))
        finally:
            os.close(fd)
        return False

    assert not signals.write_json(tmp_path / "x.json", {"a": 1}, 0.1, allow=allow)
    assert seen == [False] and not (tmp_path / "x.json").exists()  # 呼ぶときにはロックを持っている


def _root() -> bool:
    return os.geteuid() == 0


def test_read_question_returns_the_content_or_none(tmp_path):
    assert signals.read_question(tmp_path) is None
    assert signals.write_question(tmp_path / signals.QUESTION_FILE, "s1", QUESTION, 0.1)
    assert signals.read_question(tmp_path)["tool_use_id"] == "t"


@pytest.mark.parametrize("content", [b"{", b"[]", b'{"q": "\xff"}'], ids=["json", "shape", "utf8"])
def test_read_question_raises_signal_error_for_a_broken_file(tmp_path, content):
    # 不正な UTF-8 も壊れたファイルと同じ SignalError で、QuestionUnreadable でない
    (tmp_path / signals.QUESTION_FILE).write_bytes(content)
    with pytest.raises(signals.SignalError) as got:
        signals.read_question(tmp_path)
    assert not isinstance(got.value, signals.QuestionUnreadable)


def test_read_question_raises_unreadable_for_a_directory(tmp_path):
    (tmp_path / signals.QUESTION_FILE).mkdir()
    with pytest.raises(signals.QuestionUnreadable):
        signals.read_question(tmp_path)


@pytest.mark.skipif(_root(), reason="root は権限の無いファイルも読める")
def test_read_question_raises_unreadable_without_permission(tmp_path):
    path = tmp_path / signals.QUESTION_FILE
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o000)
    try:
        with pytest.raises(signals.QuestionUnreadable):
            signals.read_question(tmp_path)
    finally:
        path.chmod(0o600)


def test_question_present_counts_a_broken_file_as_present(tmp_path):
    assert not signals.question_present(tmp_path)
    (tmp_path / signals.QUESTION_FILE).write_text("{", encoding="utf-8")
    assert signals.question_present(tmp_path)


@pytest.mark.skipif(_root(), reason="root は権限の無いディレクトリも辿れる")
def test_question_present_is_true_when_the_parent_cannot_be_traversed(tmp_path):
    d = tmp_path / "relay"
    d.mkdir()
    d.chmod(0o000)
    try:
        assert signals.question_present(d)
    finally:
        d.chmod(0o700)


def test_question_id_prefers_the_tool_use_id_then_the_time():
    assert signals.question_id({"tool_use_id": "toolu_1", "at": "x"}) == "toolu_1"
    assert signals.question_id({"tool_use_id": None, "at": "2026-11-20T00:00:00+00:00"}) == "2026-11-20T00:00:00+00:00"
    assert signals.question_id({"tool_use_id": 1}) is None


def _ask(directory: Path, tool_use_id: str = "t") -> None:
    signals.write_question(directory / signals.QUESTION_FILE, "s1", {**QUESTION, "tool_use_id": tool_use_id}, 0.1)


def test_i5_drop_question_drops_only_a_matching_or_any_question(tmp_path):
    # I5・受け入れ条件 4・6: ロックの中で読み直した質問が match に合うときだけ消す。match が None なら中身を問わず消す
    _ask(tmp_path, "A")
    assert signals.drop_question(tmp_path, {"tool_use_id": "B"}, 0.1) == "kept"
    assert signals.question_present(tmp_path)
    assert signals.drop_question(tmp_path, {"tool_use_id": "A"}, 0.1) == "dropped"
    assert not signals.question_present(tmp_path)
    assert signals.drop_question(tmp_path, {"tool_use_id": "A"}, 0.1) == "dropped"  # 既に無い
    _ask(tmp_path, "A")
    assert signals.drop_question(tmp_path, None, 0.1) == "dropped" and not signals.question_present(tmp_path)


def test_i5_drop_question_drops_a_broken_file_even_with_a_match(tmp_path):
    (tmp_path / signals.QUESTION_FILE).write_text("{", encoding="utf-8")
    assert signals.drop_question(tmp_path, {"tool_use_id": "A"}, 0.1) == "dropped"
    assert not signals.question_present(tmp_path)


@pytest.mark.parametrize("recorded", ["L2", None, "missing"])
def test_i5_drop_question_keeps_the_question_for_another_launch(tmp_path, recorded):
    # I5: 渡した起動の ID が今の起動の記録と違う（止めた子の遅れた hook）・記録が無いなら何も消さない
    _ask(tmp_path)
    if recorded != "missing":
        signals.set_launch(tmp_path, recorded)
    assert signals.drop_question(tmp_path, None, 0.1, launch="L1") == "kept"
    assert signals.question_present(tmp_path)
    signals.set_launch(tmp_path, "L1")
    assert signals.drop_question(tmp_path, None, 0.1, launch="L1") == "dropped"


def test_i5_drop_question_is_busy_while_the_lock_is_held(tmp_path, hold_lock):
    _ask(tmp_path)
    fd = hold_lock(tmp_path / f"{signals.QUESTION_FILE}.lock")
    try:
        assert signals.drop_question(tmp_path, None, 0.05) == "busy"
    finally:
        os.close(fd)
    assert signals.question_present(tmp_path)


def test_i5_drop_question_fails_when_the_question_cannot_be_read_again(tmp_path, monkeypatch):
    # 読み直せない回答待ちの質問を、別の質問の tool_use_id で消さない
    _ask(tmp_path, "B")
    real = Path.read_text

    def read_text(self, *args, **kwargs):
        if self.name == signals.QUESTION_FILE:
            raise OSError(5, "EIO")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    assert signals.drop_question(tmp_path, {"tool_use_id": "A"}, 0.1) == "failed"
    monkeypatch.undo()
    assert signals.question_present(tmp_path)


@pytest.mark.parametrize("match", [None, {"tool_use_id": "A"}])
def test_i5_drop_question_fails_without_raising_for_a_directory(tmp_path, match):
    (tmp_path / signals.QUESTION_FILE).mkdir()
    assert signals.drop_question(tmp_path, match, 0.1) == "failed"
    assert (tmp_path / signals.QUESTION_FILE).is_dir()


def test_relay_mark_is_taken_once(tmp_path):
    # 中継が送った入力では hook の数え手が数え直さない（決定 92）。hook は take でマーカーを消す
    markers.mark(tmp_path, "sess-1")
    assert markers.take(tmp_path, "sess-1") is True
    assert markers.take(tmp_path, "sess-1") is False
    with pytest.raises(markers.MarkerError):
        markers.mark(tmp_path, "../x")


def test_child_env_drops_editor_git_auth_and_sets_relay_values(tmp_path):
    parent = {"PATH": "/bin", "GIT_ASKPASS": "/vscode/askpass.sh", "VSCODE_GIT_IPC_HANDLE": "/tmp/x.sock", "VSCODE_GIT_ASKPASS_MAIN": "m"}
    got = session_env.child_env(parent, tmp_path)
    assert got["PATH"] == "/bin" and not set(facts.ids("env", child="drop")) & set(got)  # #2985
    # Claude Code の中から起動しても、入れ子の起動のマーカーと親のセッションの ID を子へ残さない
    nested = session_env.child_env({**parent, "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "parent"}, tmp_path)
    assert "CLAUDECODE" not in nested and "CLAUDE_CODE_SESSION_ID" not in nested
    assert got["CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION"] == "false"  # #2504
    assert "NDF_CLAUDE_ACCOUNT" not in got
    accounts.use(tmp_path, "work")
    assert session_env.child_env(parent, tmp_path)["NDF_CLAUDE_ACCOUNT"] == "work"  # #2464
    assert session_env.child_env({**parent, "NDF_CLAUDE_ACCOUNT": "main"}, tmp_path)["NDF_CLAUDE_ACCOUNT"] == "main"
    assert session_env.child_env(parent, tmp_path, "gmail")["NDF_CLAUDE_ACCOUNT"] == "gmail"


# 認証の 13 変数（子の環境の表の account-drop）を親が持つ環境
AUTH_13 = {
    "CLAUDE_CODE_OAUTH_TOKEN": "oauth-test",
    "CLAUDE_CODE_OAUTH_SCOPES": "user:inference",
    "ANTHROPIC_API_KEY": "sk-ant-test-parent",
    "ANTHROPIC_AUTH_TOKEN": "auth-test",
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "CLAUDE_CODE_USE_VERTEX": "1",
    "CLAUDE_CODE_USE_FOUNDRY": "1",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS": "1",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD": "1",
    "CLAUDE_CODE_USE_MANTLE": "1",
    "ANTHROPIC_PROFILE": "prof",
    "ANTHROPIC_FEDERATION_RULE_ID": "rule",
    "ANTHROPIC_ORGANIZATION_ID": "org",
}
AWS_KEYS = {"AWS_ACCESS_KEY_ID": "AKIATEST", "AWS_SECRET_ACCESS_KEY": "secret-test", "AWS_BEARER_TOKEN_BEDROCK": "bearer-test"}


def _account_child(tmp_path, parent):
    """`work1` の登録済みアカウントの子の環境（用意の済んだ設定ディレクトリと使用率の書き先を渡す）。"""
    env = session_env.child_env(parent, tmp_path, "work1", decided=True)
    return session_env.account_env(env, tmp_path / "store" / "work1", tmp_path / "usage" / "work1" / "g1.json")


def test_3604_2_i4_account_child_has_no_auth_variables_and_gets_the_account_values(tmp_path):
    """#3604 の 2・I4: 親に 13 変数があっても登録済みアカウントの子に無く、account-set の 4 変数が入る。"""
    parent = {"PATH": "/bin", "CLAUDE_CONFIG_DIR": str(tmp_path / "shared"), **AUTH_13, **AWS_KEYS}
    got = _account_child(tmp_path, parent)
    assert not set(AUTH_13) & set(got)
    assert set(facts.ids("env", child="account-drop")) == set(AUTH_13)  # 表の行を 1 つ外すと落ちる
    assert got["CLAUDE_CONFIG_DIR"] == str(tmp_path / "store" / "work1")
    assert got["NDF_SHARED_CONFIG_DIR"] == str(tmp_path / "shared")
    assert got["CLAUDE_CODE_PLUGIN_CACHE_DIR"] == str(tmp_path / "shared" / "plugins")
    assert got["NDF_ACCOUNT_USAGE_FILE"] == str(tmp_path / "usage" / "work1" / "g1.json")
    assert got["NDF_CLAUDE_ACCOUNT"] == "work1" and got["PATH"] == "/bin"
    assert {k: got[k] for k in AWS_KEYS} == AWS_KEYS  # 提供元の秘密の変数は登録済みアカウントの子では親のまま


def test_account_env_without_a_parent_config_dir_keeps_an_empty_shared_dir(tmp_path):
    got = _account_child(tmp_path, {"PATH": "/bin"})
    assert got["NDF_SHARED_CONFIG_DIR"] == ""
    assert got["CLAUDE_CODE_PLUGIN_CACHE_DIR"] == str(Path.home() / ".claude" / "plugins")


def test_account_env_in_an_account_child_inherits_the_shared_dir_and_the_plugin_cache(tmp_path):
    parent = {"CLAUDE_CONFIG_DIR": str(tmp_path / "store" / "work2"), "NDF_SHARED_CONFIG_DIR": str(tmp_path / "shared")}
    got = _account_child(tmp_path, parent)
    assert got["NDF_SHARED_CONFIG_DIR"] == str(tmp_path / "shared")
    assert got["CLAUDE_CODE_PLUGIN_CACHE_DIR"] == str(tmp_path / "shared" / "plugins")
    got = _account_child(tmp_path, {**parent, "CLAUDE_CODE_PLUGIN_CACHE_DIR": "/cache"})
    assert got["CLAUDE_CODE_PLUGIN_CACHE_DIR"] == "/cache"


def test_account_env_fails_when_the_table_names_a_variable_without_a_value(tmp_path, monkeypatch):
    """表の account-set にコードの知らない名前が足されたら KeyError（表とコードの食い違いを落とす）。"""
    real = facts.ids

    def ids(name, **where):
        got = real(name, **where)
        return (*got, "NDF_UNKNOWN") if where == {"child": "account-set"} else got

    monkeypatch.setattr(session_env.facts, "ids", ids)
    with pytest.raises(KeyError):
        session_env.account_env({}, tmp_path / "work1", tmp_path / "u.json")


def test_3604_3_default_login_child_is_unchanged_and_a_fallen_back_child_has_no_account(tmp_path):
    """#3604 の 3: 既定のログインの子は今と同じ。用意に落ちて既定のログインへ移った子は NDF_CLAUDE_ACCOUNT を持たない。"""
    parent = {"PATH": "/bin", "CLAUDE_CONFIG_DIR": str(tmp_path / "shared"), **AUTH_13}
    default = session_env.child_env(parent, tmp_path)
    assert session_env.child_env(parent, tmp_path, None, decided=True) == default
    assert default["CLAUDE_CONFIG_DIR"] == str(tmp_path / "shared") and {k: default[k] for k in AUTH_13} == AUTH_13
    # 環境変数の名前の用意に落ちた子
    named = {**parent, "NDF_CLAUDE_ACCOUNT": "work1"}
    fell = session_env.child_env(named, tmp_path, None, decided=True)
    assert "NDF_CLAUDE_ACCOUNT" not in fell
    assert fell == {k: v for k, v in session_env.child_env(named, tmp_path).items() if k != "NDF_CLAUDE_ACCOUNT"}
    # 指定の用意に落ちた子
    accounts.use(tmp_path, "work1")
    assert session_env.child_env(parent, tmp_path)["NDF_CLAUDE_ACCOUNT"] == "work1"
    assert "NDF_CLAUDE_ACCOUNT" not in session_env.child_env(parent, tmp_path, None, decided=True)


def _account_parent(tmp_path):
    """登録済みアカウントの子の中の親の環境（入れ子の中継）。"""
    return {
        "PATH": "/bin",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "store" / "work1"),
        "NDF_SHARED_CONFIG_DIR": str(tmp_path / "shared"),
        "NDF_ACCOUNT_USAGE_FILE": str(tmp_path / "usage" / "work1" / "g1.json"),
        "CLAUDE_CODE_PLUGIN_CACHE_DIR": str(tmp_path / "shared" / "plugins"),
        **AUTH_13,
        **AWS_KEYS,
    }


def test_3604_2_i9_metered_child_uses_the_shared_dir_and_has_no_auth_variables(tmp_path):
    """#3604 の 2・I4・I9: 従量の接続の子は共有の設定ディレクトリで動き、13 変数と保存した設定の秘密の変数が無い。"""
    extra = {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "p", "AWS_REGION": "us-east-1"}
    env = session_env.child_env(_account_parent(tmp_path), tmp_path, "metered", decided=True)
    got = session_env.metered_env(env, extra, saved=True)
    assert got["CLAUDE_CONFIG_DIR"] == str(tmp_path / "shared") and got["NDF_CLAUDE_ACCOUNT"] == "metered"
    assert "NDF_SHARED_CONFIG_DIR" not in got and "NDF_ACCOUNT_USAGE_FILE" not in got
    assert not (set(AUTH_13) - set(extra)) & set(got) and not set(account_registry.SECRET_ENV) & set(got)
    assert {k: got[k] for k in extra} == extra
    assert got["CLAUDE_CODE_PLUGIN_CACHE_DIR"] == str(tmp_path / "shared" / "plugins")  # 親の値のまま


def test_metered_env_from_the_environment_keeps_the_aws_keys(tmp_path):
    env = session_env.child_env(_account_parent(tmp_path), tmp_path, "metered", decided=True)
    got = session_env.metered_env(env, {"ANTHROPIC_API_KEY": "sk-ant-test-decl"}, saved=False)
    assert {k: got[k] for k in AWS_KEYS} == AWS_KEYS and got["ANTHROPIC_API_KEY"] == "sk-ant-test-decl"
    assert not (set(AUTH_13) - {"ANTHROPIC_API_KEY"}) & set(got)


def test_metered_env_keeps_or_drops_the_parent_config_dir(tmp_path):
    extra = {"CLAUDE_CODE_USE_BEDROCK": "1"}
    # 親がアカウントの子でなければ親のまま（無ければ無いまま）
    assert session_env.metered_env({"CLAUDE_CONFIG_DIR": "/c"}, extra, saved=True)["CLAUDE_CONFIG_DIR"] == "/c"
    assert "CLAUDE_CONFIG_DIR" not in session_env.metered_env({}, extra, saved=True)
    # 親の共有の設定ディレクトリが空（~/.claude）なら外す
    got = session_env.metered_env({"CLAUDE_CONFIG_DIR": "/acc", "NDF_SHARED_CONFIG_DIR": ""}, extra, saved=True)
    assert "CLAUDE_CONFIG_DIR" not in got and "NDF_SHARED_CONFIG_DIR" not in got


def test_i9_metered_env_values_fixed_by_the_wrapper_do_not_follow_the_declaration(tmp_path):
    """I9: 従量の接続の設定が CLAUDE_CONFIG_DIR・NDF_CLAUDE_ACCOUNT ほかを持っても、子の値はラッパーが決める。"""
    env = session_env.child_env(_account_parent(tmp_path), tmp_path, "metered", decided=True)
    extra = {
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "CLAUDE_CONFIG_DIR": "/elsewhere",
        "NDF_CLAUDE_ACCOUNT": "work9",
        "NDF_ACCOUNT_USAGE_FILE": "/x.json",
        "CLAUDE_CODE_PLUGIN_CACHE_DIR": "/other",
    }
    got = session_env.metered_env(env, extra, saved=False)
    assert got["CLAUDE_CONFIG_DIR"] == str(tmp_path / "shared") and got["NDF_CLAUDE_ACCOUNT"] == "metered"
    assert "NDF_ACCOUNT_USAGE_FILE" not in got and got["CLAUDE_CODE_PLUGIN_CACHE_DIR"] == str(tmp_path / "shared" / "plugins")
    assert "CLAUDE_CONFIG_DIR" in session_env.METERED_FIXED and "NDF_CLAUDE_ACCOUNT" in session_env.METERED_FIXED


def test_key_env_returns_to_the_shared_dir_without_auth_or_secret_variables(tmp_path):
    """API キーの子: 共有の設定ディレクトリへ戻り、13 変数と秘密の変数が無く、キーの値を環境に入れない。"""
    headers = {"ANTHROPIC_BASE_URL": "https://gateway.example", "ANTHROPIC_CUSTOM_HEADERS": "X-Route: a"}
    parent = {**_account_parent(tmp_path), **headers}
    env = session_env.child_env(parent, tmp_path, "key:key-main", decided=True)
    got = session_env.key_env(env, "key-main")
    assert got["CLAUDE_CONFIG_DIR"] == str(tmp_path / "shared") and got["NDF_CLAUDE_ACCOUNT"] == "key:key-main"
    assert not set(AUTH_13) & set(got)
    assert not (set(account_registry.SECRET_ENV) - {"ANTHROPIC_CUSTOM_HEADERS"}) & set(got)
    assert "sk-ant-test-parent" not in got.values()
    # 接続先とヘッダーは黙って外さない（起動の前の確かめが比べ、当たればそのキーを候補から外す）
    assert {k: got[k] for k in headers} == headers
    assert "ANTHROPIC_CUSTOM_HEADERS" not in session_env.key_env(session_env.child_env({}, tmp_path), "key-main")


def test_account_pin_round_trip_and_broken_pin_stops(tmp_path):
    assert accounts.pinned(tmp_path) is None and accounts.chosen({}, tmp_path) is None
    accounts.use(tmp_path, "sub-1")
    assert accounts.pinned(tmp_path) == "sub-1"
    with pytest.raises(accounts.AccountError):
        accounts.use(tmp_path, "a b")
    accounts.pin_path(tmp_path).write_text("{", encoding="utf-8")
    with pytest.raises(accounts.AccountError):
        accounts.chosen({}, tmp_path)
    assert accounts.clear(tmp_path) is True and accounts.clear(tmp_path) is False


def test_usage_cache_round_trip(tmp_path):
    assert accounts.usages(tmp_path) == []
    rows = [Usage("team", 94.0, False), Usage("main", 20.0, True)]
    accounts.write_usages(tmp_path, rows)
    assert accounts.usages(tmp_path) == rows
    accounts.usage_path(tmp_path).write_text('{"accounts": [{"name": "x"}]}', encoding="utf-8")
    with pytest.raises(accounts.AccountError):
        accounts.usages(tmp_path)


def test_running_wrappers_and_old_ndf_wrappers_are_told_apart(tmp_path, hold_lock):
    state = tmp_path / "state"
    env = {"NDF_STATE_HOME": str(tmp_path / "old")}
    d = registry.wrapper_dir(state, "t1")
    registry.write_state(d, {"version": "11.0.0-dev.4"})
    registry.wrapper_dir(state, "t2").mkdir(parents=True)
    held = [hold_lock(d / registry.LOCK_FILE), hold_lock(tmp_path / "old" / "relay" / "20261113T1" / registry.LOCK_FILE)]
    try:
        got = registry.all_running(state, env)
    finally:
        for fd in held:
            os.close(fd)
    assert [(r.name, r.version) for r in got] == [("t1", "11.0.0-dev.4"), ("20261113T1", None)]
    assert registry.all_running(state, env) == []
    assert registry.legacy_root({"XDG_STATE_HOME": "/x"}) == Path("/x/ndf/relay")
    with pytest.raises(registry.RegistryError):
        registry.wrapper_dir(state, "../x")


def test_context_tokens_come_from_the_last_reply(tmp_path):
    path = observe.transcript_path({"CLAUDE_CONFIG_DIR": str(tmp_path)}, Path("/w/a.b"), "s1")
    assert path == tmp_path / "projects" / "-w-a-b" / "s1.jsonl"
    assert observe.context_tokens(path, 4096) is None
    path.parent.mkdir(parents=True)
    rows = [
        {"message": {"usage": {"input_tokens": 1, "cache_read_input_tokens": 2}}},
        {"message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 700000, "cache_creation_input_tokens": 100000}}},
        {"type": "user"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n{", encoding="utf-8")
    assert observe.context_tokens(path, 4096) == 800010


def test_screen_facts():
    assert observe.auth_failed(observe.plain(b"\x1b[31mPlease run /login\x1b[0m"))
    assert observe.auth_failed("Goal cleared after an unrecoverable error (authentication failed)")
    assert not observe.auth_failed("all good")


# 現状固定（I-001）: 書き込みはどれも親の保存先を作り、同じ保存先の一時の名前から置き換え、一時の名前を残さない
def _no_temporary(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.name.endswith(".tmp")]


def test_relay_writes_create_the_parent_and_leave_no_temporary_file(tmp_path, monkeypatch):
    from ndf.domains.relay import hub

    monkeypatch.setattr(signals, "now_iso", lambda: "2026-11-14T00:00:00+00:00")
    state = tmp_path / "state"
    path = state / "a" / "b" / "sig.json"
    assert signals.write_json(path, {"k": "日本語"}, 0.1)
    assert path.read_text(encoding="utf-8") == '{"k": "日本語"}'
    assert signals.write_json(path, {"k": "後", "n": 1}, 0.1, keep=("k", "x"))
    assert json.loads(path.read_text(encoding="utf-8")) == {"k": "日本語", "n": 1}
    accounts.use(state, "main")
    assert accounts.pin_path(state).read_text(encoding="utf-8") == '{"account": "main"}'
    accounts.write_usages(state, [Usage("チーム", 1.5, False)])
    assert accounts.usage_path(state).read_text(encoding="utf-8") == '{"accounts": [{"name": "チーム", "percent": 1.5, "limited": false}]}'
    d = registry.wrapper_dir(state, "t1")
    registry.write_state(d, {"state": "running", "reason": "セグメント 1"})
    assert (d / registry.STATE_FILE).read_text(encoding="utf-8") == (
        '{"state": "running", "reason": "セグメント 1", "at": "2026-11-14T00:00:00+00:00"}'
    )
    registry.save_registry(hub.hub_dir(state), {"t1": {"cwd": "/w/作業"}})
    assert (hub.hub_dir(state) / registry.REGISTRY_FILE).read_text(
        encoding="utf-8"
    ) == '{"version": 1, "entries": {"t1": {"cwd": "/w/作業"}}}'
    assert registry.read_registry(hub.hub_dir(state)) == {"t1": {"cwd": "/w/作業"}}
    m = markers.mark(state, "s1")
    assert m.read_text(encoding="utf-8") == '{"session_id": "s1", "at": "2026-11-14T00:00:00+00:00"}'
    assert _no_temporary(tmp_path) == []


def test_install_writes_set_the_mode_and_leave_no_temporary_file(tmp_path):
    from ndf.domains.relay import install

    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "XDG_BIN_HOME": str(tmp_path / "bin"), "PATH": ""}
    got = install.install(env, tmp_path / "plugin", 9, replace_foreign=False, version="11.0.0")
    assert got.copy.path.stat().st_mode & 0o777 == 0o755
    assert got.entry.path.stat().st_mode & 0o777 == 0o755
    root_file = got.copy.path.parent / install.ROOT_FILE
    assert root_file.stat().st_mode & 0o777 == 0o644
    assert root_file.read_text(encoding="utf-8") == f"{tmp_path / 'plugin'}\n"
    assert got.copy.path.read_text(encoding="utf-8") == install.launcher(9)
    assert _no_temporary(tmp_path) == []


# 現状固定（I-002）: 中継の保存先は状態の保存先の下にあり、設定（locations・ndf-files）のパスと一致する
def test_relay_locations_match_the_declared_paths():
    from ndf.domains.relay import hub

    state = Path("/s")
    got = {
        "relay-hub": hub.hub_dir(state),
        "relay-wrappers": registry.wrappers_dir(state),
        "relay-accounts": accounts.accounts_dir(state),
        "relay-markers": markers.marker_path(state, "s1").parent,
    }
    assert {k: str(v) for k, v in got.items()} == {
        "relay-hub": "/s/relay/hub",
        "relay-wrappers": "/s/relay/wrappers",
        "relay-accounts": "/s/relay/accounts",
        "relay-markers": "/s/relay/markers",
    }
    for location, path in got.items():
        assert facts.value("locations", location, "root") == "state", location
        assert path == state / facts.value("locations", location, "path"), location
    assert registry.wrapper_dir(state, "t1") == Path("/s/relay/wrappers/t1")
    assert accounts.pin_path(state) == Path("/s/relay/accounts/pinned.json")
    assert accounts.usage_path(state) == Path("/s/relay/accounts/usage.json")
    assert markers.marker_path(state, "s1") == Path("/s/relay/markers/s1.json")
    declared = {r["path"] for r in facts.rows("ndf-files", location="relay-hub")}
    assert {hub.STATE_FILE, registry.REGISTRY_FILE, hub.LOG_FILE} <= declared
    wrapper = {r["path"] for r in facts.rows("ndf-files", location="relay-wrappers")}
    assert {f"<名前>/{n}" for n in (registry.STATE_FILE, registry.LOCK_FILE, registry.LOG_FILE)} <= wrapper


def test_child_env_drops_the_ndf_environment_so_uv_in_the_session_uses_its_own(tmp_path):
    # bin/ndf と uv run が入れた NDF の依存の環境を子へ渡すと、セッションの中の uv run が NDF の環境を書き換える
    parent = {
        "UV_PROJECT_ENVIRONMENT": "/c/ndf/venv/11.0.2",
        "VIRTUAL_ENV": "/c/ndf/venv/11.0.2",
        "UV_RUN_RECURSION_DEPTH": "1",
        "CLAUDE_PLUGIN_ROOT": "/p/ndf/11.0.2",
        "PYTHONPATH": os.pathsep.join([str(session_env.NDF_SRC), "/home/u/lib"]),
        "HOME": "/home/u",
    }
    got = session_env.child_env(parent, tmp_path)
    assert not {"UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV", "UV_RUN_RECURSION_DEPTH", "CLAUDE_PLUGIN_ROOT"} & set(got)
    assert got["PYTHONPATH"] == "/home/u/lib" and got["HOME"] == "/home/u"
    only_ndf = session_env.child_env({"PYTHONPATH": str(session_env.NDF_SRC)}, tmp_path)
    assert "PYTHONPATH" not in only_ndf


# --- 子の PATH を起動先のバージョンにそろえる（#3909） ---

OLD, NEW = "11.0.1", "11.0.2"


def _ndf_roots(tmp_path: Path, marketplace: str = "ai-plugins") -> tuple[Path, Path]:
    """キャッシュの下に 2 つのバージョンの NDF の根（実行できる `bin/ndf` を持つ）を作り、(N, M) を返す。"""
    roots = []
    for version in (OLD, NEW):
        root = tmp_path / "cache" / marketplace / "ndf" / version
        (root / "bin").mkdir(parents=True)
        (root / "bin" / "ndf").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "bin" / "ndf").chmod(0o755)
        (root / ".claude-plugin").mkdir()
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "ndf", "version": version}), encoding="utf-8")
        roots.append(root)
    return roots[0], roots[1]


def _path(*items: object) -> str:
    return os.pathsep.join(str(i) for i in items)


def _resolved(env: dict[str, str], *names: str) -> list[str]:
    """渡した環境の `sh` で `command -v` を打った結果（子の Bash が解決するコマンド）。"""
    script = "; ".join(f"command -v {n}" for n in names)
    got = subprocess.run([shutil.which("sh") or "/bin/sh", "-c", script], env=env, capture_output=True, text=True, timeout=10)
    return got.stdout.splitlines()


def test_launched_ndf_drops_other_versions_and_the_venv_and_puts_the_launched_bin_first(tmp_path):
    old, new = _ndf_roots(tmp_path)
    home = tmp_path / "home"
    venv = home / ".cache" / "ndf" / "venv" / OLD / "bin"
    env = {"PATH": _path("/usr/bin", old / "bin", venv, "/home/u/.local/bin", new / "bin"), "HOME": str(home), "X": "y"}
    got = session_env.with_launched_ndf(env, new)
    assert got["PATH"] == _path(new / "bin", "/usr/bin", "/home/u/.local/bin", new / "bin")
    assert got["X"] == "y" and env["PATH"] == _path("/usr/bin", old / "bin", venv, "/home/u/.local/bin", new / "bin")
    # XDG_CACHE_HOME があればその下の仮想環境を外す（bin/ndf の保存先と同じ規則）
    xdg = tmp_path / "xdg" / "ndf" / "venv" / OLD / "bin"
    got = session_env.with_launched_ndf({"PATH": _path(xdg, "/usr/bin"), "HOME": str(home), "XDG_CACHE_HOME": str(tmp_path / "xdg")}, new)
    assert got["PATH"] == _path(new / "bin", "/usr/bin")


def test_launched_ndf_resolves_ndf_to_the_launched_version_beside_a_symlink_to_the_old_one(tmp_path):
    # PATH の上に旧版の bin/ndf を指すリンクがあっても ndf は起動先へ、同じディレクトリのほかのコマンドはそのまま解決する
    old, new = _ndf_roots(tmp_path)
    local = tmp_path / "local" / "bin"
    local.mkdir(parents=True)
    (local / "ndf").symlink_to(old / "bin" / "ndf")
    (local / "other").write_text("#!/bin/sh\n", encoding="utf-8")
    (local / "other").chmod(0o755)
    env = {"PATH": _path(local, old / "bin", "/usr/bin", "/bin"), "HOME": str(tmp_path / "home")}
    got = session_env.with_launched_ndf(env, new)
    assert _resolved(got, "ndf", "other") == [str(new / "bin" / "ndf"), str(local / "other")]


def test_launched_ndf_drops_every_copy_of_a_repeated_element(tmp_path):
    old, new = _ndf_roots(tmp_path)
    got = session_env.with_launched_ndf({"PATH": _path(*[old / "bin"] * 5, "/usr/bin")}, new)
    assert got["PATH"] == _path(new / "bin", "/usr/bin")


def test_launched_ndf_keeps_the_ndf_of_another_marketplace(tmp_path):
    _, new = _ndf_roots(tmp_path)
    other, _ = _ndf_roots(tmp_path, "other-market")
    got = session_env.with_launched_ndf({"PATH": _path(other / "bin", "/usr/bin")}, new)
    assert got["PATH"] == _path(new / "bin", other / "bin", "/usr/bin")


def test_launched_ndf_drops_a_symlink_to_another_versions_bin(tmp_path):
    old, new = _ndf_roots(tmp_path)
    link = tmp_path / "linked-bin"
    link.symlink_to(old / "bin")
    got = session_env.with_launched_ndf({"PATH": _path(link, "/usr/bin")}, new)
    assert got["PATH"] == _path(new / "bin", "/usr/bin")


def test_launched_ndf_keeps_an_element_that_cannot_be_resolved(tmp_path):
    # 循環するリンクを通る要素は NDF と判定できないので残し、残りを整える（3.13 以降の既定の resolve は例外を出さない）
    old, new = _ndf_roots(tmp_path)
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    got = session_env.with_launched_ndf({"PATH": _path(loop / "bin", old / "bin", "/usr/bin")}, new)
    assert got["PATH"] == _path(new / "bin", loop / "bin", "/usr/bin")


def test_launched_ndf_keeps_a_relative_element_when_the_cwd_is_gone(tmp_path, monkeypatch):
    # 作業ディレクトリが削除されて相対の要素を解けなくても例外を出さず、その要素を残して残りを整える
    old, new = _ndf_roots(tmp_path)
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    got = session_env.with_launched_ndf({"PATH": _path("./bin", old / "bin", "/usr/bin")}, new)
    assert got["PATH"] == _path(new / "bin", "./bin", "/usr/bin")


def test_launched_ndf_keeps_a_loop_followed_by_dotdot(tmp_path):
    # 循環するリンクの直後の `..` で旧版の bin を指す字面でも、循環を消さずに解決できない要素として残す
    old, new = _ndf_roots(tmp_path)
    loop = old.parent / "loop"
    loop.symlink_to(loop)
    item = f"{loop}/../{old.name}/bin"
    got = session_env.with_launched_ndf({"PATH": _path(item, "/usr/bin")}, new)
    assert got["PATH"] == _path(new / "bin", item, "/usr/bin")


def test_launched_ndf_drops_a_dangling_symlink_into_the_venv(tmp_path):
    # 削除済みの仮想環境のディレクトリを指すリンク（相対・多段も）は、リンク名でなくリンク先で判定して外す
    _, new = _ndf_roots(tmp_path)
    cache = tmp_path / "cache"
    (cache / "ndf" / "venv").mkdir(parents=True)
    links = tmp_path / "links"
    links.mkdir()
    (links / "abs").symlink_to(cache / "ndf" / "venv" / OLD / "bin")
    (links / "rel").symlink_to(Path("..") / "cache" / "ndf" / "venv" / OLD)
    (links / "chain").symlink_to(links / "abs")
    path = _path(links / "abs", links / "rel" / "bin", links / "chain", "/usr/bin")
    got = session_env.with_launched_ndf({"PATH": path, "XDG_CACHE_HOME": str(cache)}, new)
    assert got["PATH"] == _path(new / "bin", "/usr/bin")


@pytest.mark.parametrize("var", ["XDG_CACHE_HOME", "HOME"])
def test_launched_ndf_keeps_going_when_the_venv_home_cannot_be_resolved(tmp_path, var):
    # 仮想環境の保存先が循環するリンクを通っても、例外を出さずに別のバージョンの bin を外して起動先を先頭に置く。
    # 同じ循環の下の要素を仮想環境の下と取り違えて外さない
    old, new = _ndf_roots(tmp_path)
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    under = loop / ".cache" / "ndf" / "venv" / OLD / "bin" if var == "HOME" else loop / "ndf" / "venv" / OLD / "bin"
    got = session_env.with_launched_ndf({"PATH": _path(old / "bin", under, "/usr/bin"), var: str(loop)}, new)
    assert got["PATH"] == _path(new / "bin", under, "/usr/bin")


def test_launched_ndf_keeps_a_loop_reached_through_dotdot_after_a_missing_element(tmp_path):
    # 存在しない要素の後の `..` で実在する側へ戻り、その先が循環するリンクなら解決できない要素として残す
    _, new = _ndf_roots(tmp_path)
    cache = tmp_path / "cache"
    venv = cache / "ndf" / "venv"
    venv.mkdir(parents=True)
    (venv / "loop").symlink_to(venv / "loop")
    item = f"{venv}/absent/../loop/bin"
    got = session_env.with_launched_ndf({"PATH": _path(item, "/usr/bin"), "XDG_CACHE_HOME": str(cache)}, new)
    assert got["PATH"] == _path(new / "bin", item, "/usr/bin")


def test_launched_ndf_without_path_returns_a_copy_of_the_environment(tmp_path):
    _, new = _ndf_roots(tmp_path)
    env = {"HOME": str(tmp_path)}
    got = session_env.with_launched_ndf(env, new)
    assert got == env and got is not env


def test_launched_ndf_without_home_keeps_the_venv_and_still_drops_other_versions(tmp_path):
    old, new = _ndf_roots(tmp_path)
    venv = tmp_path / "home" / ".cache" / "ndf" / "venv" / OLD / "bin"
    got = session_env.with_launched_ndf({"PATH": _path(old / "bin", venv, "/usr/bin")}, new)
    assert got["PATH"] == _path(new / "bin", venv, "/usr/bin")


def test_launched_ndf_does_not_put_a_bin_without_an_executable_ndf_first(tmp_path):
    # 入れ替えの途中で起動先の bin/ndf が実行できなければ、外すだけで先頭に置かない
    old, new = _ndf_roots(tmp_path)
    (new / "bin" / "ndf").chmod(0o644)
    local = tmp_path / "local" / "bin"
    local.mkdir(parents=True)
    (local / "ndf").symlink_to(old / "bin" / "ndf")
    env = {"PATH": _path(local, old / "bin", "/usr/bin", "/bin"), "HOME": str(tmp_path / "home")}
    got = session_env.with_launched_ndf(env, new)
    assert got["PATH"] == _path(local, "/usr/bin", "/bin")
    assert _resolved(got, "ndf") == [str(local / "ndf")]


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux が無い")
def test_the_wrapper_session_has_no_status_line(tmp_path):
    # 結び付いた利用者の画面の下にラッパーのステータス行を出さない。サーバ全体の設定は変えない（#3833）
    sock = f"ndf-test-{os.getpid()}"
    tmux = ["tmux", "-f", "/dev/null", "-L", sock]
    try:
        subprocess.run([*tmux, *relay_tmux.new_session_args("t2", tmp_path, ["sleep", "30"], {})], check=True, timeout=10)
        shown = subprocess.run([*tmux, "show-options", "-t", "=t2:", "status"], capture_output=True, text=True, timeout=10)
        whole = subprocess.run([*tmux, "show-options", "-g", "status"], capture_output=True, text=True, timeout=10)
    finally:
        subprocess.run([*tmux, "kill-server"], capture_output=True, timeout=10)
    assert shown.stdout.strip() == "status off"
    assert whole.stdout.strip() == "status on"
