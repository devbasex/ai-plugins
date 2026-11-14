"""中継の部品: ブロックの読み・シグナルファイル・中継の印・子の環境・アカウント・ラッパーの見分け・会話の記録。"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

import pytest

from ndf.contracts import facts
from ndf.domains.relay import accounts, blocks, marks, observe, registry, session_env, signals
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


def test_signal_write_gives_up_when_the_lock_is_held(tmp_path):
    fd = os.open(tmp_path / f"{signals.NEXT_FILE}.lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert not signals.write_next(tmp_path, "s1", "/goal x", False, 0.05)
    finally:
        os.close(fd)


def test_question_and_places_from_the_environment(tmp_path):
    payload = {"tool_use_id": "t", "tool_input": {"questions": [{"question": "q"}]}, "cwd": "/w"}
    assert signals.write_question(tmp_path / signals.QUESTION_FILE, "s1", payload, 0.1)
    assert signals.has_question(tmp_path)
    assert json.loads((tmp_path / signals.QUESTION_FILE).read_text(encoding="utf-8"))["questions"] == [{"question": "q"}]
    assert signals.relay_dir({}) is None and signals.relay_dir({"NDF_RELAY_DIR": str(tmp_path / "none")}) is None
    assert signals.relay_dir(signals.dir_env(tmp_path)) == tmp_path
    assert signals.organizer_question_path({"NDF_ORGANIZER_DIR": "/o", "NDF_ORGANIZER_LANE": "l1"}) == Path("/o/questions/l1.json")
    assert signals.organizer_question_path({"NDF_ORGANIZER_DIR": "/o", "NDF_ORGANIZER_LANE": "../x"}) is None


def test_relay_mark_is_taken_once(tmp_path):
    # 中継が送った入力では hook の数え手が数え直さない（決定 92）。hook は take で印を消す
    marks.mark(tmp_path, "sess-1")
    assert marks.take(tmp_path, "sess-1") is True
    assert marks.take(tmp_path, "sess-1") is False
    with pytest.raises(marks.MarkError):
        marks.mark(tmp_path, "../x")


def test_child_env_drops_editor_git_auth_and_sets_relay_values(tmp_path):
    parent = {"PATH": "/bin", "GIT_ASKPASS": "/vscode/askpass.sh", "VSCODE_GIT_IPC_HANDLE": "/tmp/x.sock", "VSCODE_GIT_ASKPASS_MAIN": "m"}
    got = session_env.child_env(parent, tmp_path)
    assert got["PATH"] == "/bin" and not set(facts.ids("env", child="drop")) & set(got)  # #2985
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


def _hold(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def test_running_wrappers_and_old_ndf_wrappers_are_told_apart(tmp_path):
    state = tmp_path / "state"
    env = {"NDF_STATE_HOME": str(tmp_path / "old")}
    d = registry.wrapper_dir(state, "t1")
    registry.write_state(d, {"version": "11.0.0-dev.4"})
    registry.wrapper_dir(state, "t2").mkdir(parents=True)
    held = [_hold(d / registry.LOCK_FILE), _hold(tmp_path / "old" / "relay" / "20261113T1" / registry.LOCK_FILE)]
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
