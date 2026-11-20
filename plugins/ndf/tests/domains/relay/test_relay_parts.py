"""中継の部品: ブロックの読み・シグナルファイル・中継のマーカー・子の環境・アカウント・ラッパーの見分け・会話の記録。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ndf.contracts import facts
from ndf.domains.relay import accounts, blocks, markers, observe, registry, session_env, signals
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
