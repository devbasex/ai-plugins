"""Stop の `relay-marker` が作業の件数を起動ごとの記録へ書き、作業が残るときだけ変更要求する（#4049 の受け入れ条件 5・8・13）。"""

from __future__ import annotations

import pytest

from ndf.domains.hooks.guards import relay_busy
from ndf.domains.relay import signals

from ._hooks import SAMPLES, boom, busy, call_entry, read_sample, records, stop

FENCE = "```ndf-next\n/goal 次のセグメント\n```"
SUBAGENT = {"id": "a1", "type": "subagent", "status": "running", "description": "d", "agent_type": "x"}
WATCH = {"id": "b1", "type": "shell", "status": "running", "description": "d", "command": "ndf plan wait p.json"}


def ended(cwd, tasks, reply=FENCE, **override):
    return call_entry("claude", "Stop", stop(cwd, reply, **{"session_id": "s1", "background_tasks": tasks, **override}))


@pytest.mark.parametrize("reply", [FENCE, "ブロックの無い応答", ""], ids=["block", "no-block", "empty"])
@pytest.mark.parametrize(
    "tasks, work, monitors",
    [([], 0, 0), ([SUBAGENT, {"type": "workflow"}], 2, 0), ([WATCH], 0, 1), ([SUBAGENT, WATCH], 1, 1), (["x"], 1, 0)],
)
def test_the_stop_of_the_child_writes_its_counts(relay_child, tmp_path, reply, tasks, work, monitors):
    ended(tmp_path, tasks, reply)
    assert busy(relay_child) == (work, monitors, "stop")
    assert records()[-1]["notes"]["relay_busy"] is True


@pytest.mark.parametrize("command, counts", [(None, (2, 0, "stop")), ("ndf lanes wait p", (0, 2, "stop"))], ids=["as-captured", "waits"])
def test_a_captured_stop_with_a_monitor_and_a_background_shell_is_counted(relay_child, tmp_path, command, counts):
    # 実物（Claude Code 2.1.331）の `background_tasks` は、Monitor もバックグラウンドの Bash も `type` と `command` を持つ
    payload = read_sample(SAMPLES / "claude" / "2.1.331" / "Stop.json", tmp_path, session_id="s1")
    assert [t["type"] for t in payload["background_tasks"]] == ["shell", "monitor"]
    for task in payload["background_tasks"]:
        task["command"] = command or task["command"]
    call_entry("claude", "Stop", payload)
    assert busy(relay_child) == counts


def test_only_monitors_left_write_the_next_section(relay_child, tmp_path):
    assert ended(tmp_path, [WATCH]) is None
    assert signals.read_next(relay_child)["block"] == "/goal 次のセグメント"


def test_a_work_left_asks_for_a_change_counting_only_the_work(relay_child, tmp_path):
    out = ended(tmp_path, [SUBAGENT, WATCH])
    assert "バックグラウンドの作業が 1 件" in out["reason"] and "監視 1 件" in out["reason"]
    assert signals.read_next(relay_child) is None


@pytest.mark.parametrize("tasks", [None, "x"], ids=["null", "text"])
def test_an_unobserved_list_keeps_the_previous_record_and_writes_the_marker(relay_child, tmp_path, tasks):
    ended(tmp_path, [SUBAGENT], "続ける")
    assert ended(tmp_path, tasks) is None
    assert busy(relay_child) == (1, 0, "stop") and signals.read_next(relay_child) is not None


def test_a_stop_without_the_list_keeps_the_previous_record(relay_child, tmp_path):
    ended(tmp_path, [SUBAGENT], "続ける")
    payload = stop(tmp_path, "続ける", session_id="s1")
    del payload["background_tasks"]
    call_entry("claude", "Stop", payload)
    assert busy(relay_child) == (1, 0, "stop")


@pytest.mark.parametrize("name, value", [("NDF_RELAY_SESSION", "s0"), ("NDF_HOOK_OFF", "relay-busy")], ids=["nested", "off"])
def test_a_stop_whose_starts_are_not_recorded_writes_no_count_but_the_marker(relay_child, tmp_path, monkeypatch, name, value):
    # 開始が記録されない会話では 0 件を書かない（記録は不明のまま）。作業と監視の分け方は同じ
    monkeypatch.setenv(name, value)
    assert "1 件" in ended(tmp_path, [SUBAGENT, WATCH])["reason"] and ended(tmp_path, [WATCH]) is None
    assert signals.read_next(relay_child) is not None and list(relay_child.glob("busy-*")) == []


def test_every_guard_off_writes_nothing(relay_child, tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_HOOK_OFF", "all")
    assert ended(tmp_path, []) is None and list(relay_child.glob("busy-*")) == []


@pytest.mark.parametrize("outside, reply", [(True, FENCE), (False, "ブロックの無い応答")], ids=["outside", "nested"])
def test_other_conversations_do_not_classify(relay_child, tmp_path, monkeypatch, outside, reply):
    if outside:
        monkeypatch.delenv("NDF_RELAY_DIR")
    else:
        monkeypatch.setenv("NDF_RELAY_SESSION", "s0")
    monkeypatch.setattr(relay_busy, "table_params", boom)
    monkeypatch.setattr(relay_busy, "work_count", boom)
    ended(tmp_path, [SUBAGENT], reply)
    assert all(g["error"] == "" for g in records()[-1]["guards"]) and list(relay_child.glob("busy-*")) == []


def test_the_stop_classifies_once(relay_child, tmp_path, monkeypatch):
    calls, real = [], relay_busy.work_count
    monkeypatch.setattr(relay_busy, "work_count", lambda tasks, params: calls.append(tasks) or real(tasks, params))
    ended(tmp_path, [WATCH])
    assert len(calls) == 1


def test_a_failed_write_keeps_the_marker_handling(relay_child, tmp_path, monkeypatch):
    monkeypatch.setattr(signals, "write_busy", lambda *args: False)
    assert ended(tmp_path, [WATCH]) is None
    assert signals.read_next(relay_child) is not None and records()[-1]["notes"]["relay_busy"] is False


def test_a_stop_without_the_place_or_the_launch_still_writes_the_marker(relay_child, tmp_path, monkeypatch):
    monkeypatch.delenv("NDF_RELAY_LAUNCH")
    assert ended(tmp_path, []) is None
    assert signals.read_next(relay_child) is not None and records()[-1]["notes"]["relay_busy"] is False
