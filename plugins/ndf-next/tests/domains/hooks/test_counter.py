"""セッションの数え手（M3-H7・IH5）。"""

from __future__ import annotations

import fcntl
import json
import os

from ndf.domains.hooks import counter as counter_mod
from ndf.domains.hooks.counter import Counter

from ._hooks import call_entry, deny_reason, records, sample, state_dir, stop

WAIT = "方針は 2 つある。どちらにしますか？"


def _prompt(tmp_path):
    return call_entry("claude", "UserPromptSubmit", sample("claude", "UserPromptSubmit", tmp_path))


def test_prose_wait_is_pushed_back_at_most_twice_per_user_input(tmp_path):
    outs = [call_entry("claude", "Stop", stop(tmp_path, WAIT)) for _ in range(5)]
    assert [o is not None for o in outs] == [True, True, False, False, False]
    assert [r["verdict"] for r in records() if r["event"] == "Stop"] == ["pushback", "pushback", "limited", "limited", "limited"]
    _prompt(tmp_path)
    assert call_entry("claude", "Stop", stop(tmp_path, WAIT)) is not None


def test_pushbacks_are_counted_across_reasons(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_FACTORY_RUN", "1")
    first = call_entry("claude", "Stop", stop(tmp_path, WAIT))
    assert "AskUserQuestion" in first["reason"] and "区間の報告の形" in first["reason"]
    second = call_entry("claude", "Stop", stop(tmp_path, "- 作業: x\n- 結果: 完了\n"))
    assert second is not None and "区間の報告の形" in second["reason"]
    assert call_entry("claude", "Stop", stop(tmp_path, "形の無い応答")) is None


def test_same_range_read_is_denied_twice_then_advised(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    payload = sample("claude", "PreToolUse", tmp_path, tool_name="Read", tool_input={"file_path": str(target)})
    outs = [call_entry("claude", "PreToolUse", payload) for _ in range(6)]
    assert outs[:2] == [None, None]
    assert [deny_reason(o) is not None for o in outs[2:4]] == [True, True]
    assert all("案内した" in o["hookSpecificOutput"]["additionalContext"] for o in outs[4:])
    _prompt(tmp_path)
    assert call_entry("claude", "PreToolUse", payload) is None


def test_relay_input_does_not_reset_the_counts(tmp_path):
    for _ in range(2):
        call_entry("claude", "Stop", stop(tmp_path, WAIT))
    session = sample("claude", "Stop", tmp_path)["session_id"]
    assert counter_mod.mark_relay(session, state_dir())
    _prompt(tmp_path)  # 中継が送った入力: 印を消すだけ
    assert call_entry("claude", "Stop", stop(tmp_path, WAIT)) is None
    _prompt(tmp_path)  # 人の入力: 数え直す
    assert call_entry("claude", "Stop", stop(tmp_path, WAIT)) is not None


def test_relay_mark_file_written_by_the_relay_does_not_reset_the_counts(tmp_path):
    from ndf.domains.relay import marks

    for _ in range(2):
        call_entry("claude", "Stop", stop(tmp_path, WAIT))
    session = sample("claude", "Stop", tmp_path)["session_id"]
    path = marks.mark(state_dir(), session)
    _prompt(tmp_path)  # 中継が送った入力: 中継の印を消すだけ
    assert not path.exists()
    assert call_entry("claude", "Stop", stop(tmp_path, WAIT)) is None


def test_counter_reset_and_limits(tmp_path):
    with Counter.open("abc-1", tmp_path) as c:
        assert c.allow_pushback() and c.allow_pushback() and not c.allow_pushback()
        assert c.allow_deny("k") and c.allow_deny("k") and not c.allow_deny("k") and c.allow_deny("other")
        assert c.bump("r") == 1 and c.bump("r") == 2
        assert c.reset() == "human"
        assert c.data["turn"] == 1 and c.data["pushbacks"] == 0 and c.data["denies"] == {} and c.data["counts"] == {}
    data = json.loads((tmp_path / "hooks" / "sessions" / "abc-1.json").read_text(encoding="utf-8"))
    assert data["turn"] == 1


def test_counter_that_cannot_lock_or_read_does_not_count(tmp_path):
    path = tmp_path / "hooks" / "sessions" / "s-2.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    with Counter.open("s-2", tmp_path) as c:
        assert not c.usable and not c.allow_pushback() and not c.allow_deny("k") and c.bump("x") == 0
    assert path.read_text(encoding="utf-8") == "{broken"
    path.write_text("{}", encoding="utf-8")
    fd = os.open(path, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with Counter.open("s-2", tmp_path, wait=0.05) as c:
            assert not c.usable
    finally:
        os.close(fd)
    with Counter.open("../x", tmp_path) as c:
        assert not c.usable
