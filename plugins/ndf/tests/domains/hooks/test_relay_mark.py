"""Stop の hook が活動（背景の作業の一覧とセッション）を書く（#4036）と、`activity.json` の読み書き。"""

from __future__ import annotations

import json

import pytest

from ndf.domains.relay import signals

from ._hooks import call_entry, stop

FENCE = "```ndf-next\n/goal 次のセグメント\n```"
SID = "00000000-0000-0000-0000-000000000000"
SUBAGENT = {"id": "a150335adf8797c51", "type": "subagent", "status": "running", "description": "d", "agent_type": "x"}
SHELL = {"id": "b7prporwe", "type": "shell", "status": "running", "description": "d", "command": "sleep 45"}


def test_write_activity_keeps_only_id_and_type(tmp_path):
    assert signals.write_activity(tmp_path, [SUBAGENT, SHELL], "s1") is True
    got = signals.read_activity(tmp_path)
    assert got["session_id"] == "s1" and isinstance(got["at"], str)
    assert got["tasks"] == [{"id": SUBAGENT["id"], "type": "subagent"}, {"id": SHELL["id"], "type": "shell"}]


def test_write_activity_keeps_the_reply_it_ended(tmp_path):
    assert signals.write_activity(tmp_path, [], "s1", "r1")
    assert signals.read_activity(tmp_path)["reply"] == "r1"


def test_stop_writes_the_uuid_of_the_reply_it_ended(tmp_path, relay):
    transcript = tmp_path / "t.jsonl"
    row = {
        "type": "assistant",
        "uuid": "r1",
        "timestamp": "2026-11-23T00:00:00.500Z",
        "message": {"stop_reason": "end_turn", "content": []},
    }
    transcript.write_text(json.dumps(row) + "\n", encoding="utf-8")
    call_entry("claude", "Stop", stop(tmp_path, "終えた", background_tasks=[], transcript_path=str(transcript)))
    assert signals.read_activity(relay)["reply"] == "r1"


def test_stop_without_a_task_list_does_not_read_the_reply_uuid(tmp_path, relay):
    # 一覧を観測できない記録は読む側が使わないので、会話の記録があっても uuid を読まず `reply` を null にする
    transcript = tmp_path / "t.jsonl"
    row = {"type": "assistant", "uuid": "r1", "message": {"stop_reason": "end_turn", "content": []}}
    transcript.write_text(json.dumps(row) + "\n", encoding="utf-8")
    payload = stop(tmp_path, "終えた", transcript_path=str(transcript))
    del payload["background_tasks"]
    call_entry("claude", "Stop", payload)
    got = signals.read_activity(relay)
    assert got["tasks"] is None and got["reply"] is None


def test_write_activity_keeps_the_count_of_broken_elements(tmp_path):
    assert signals.write_activity(tmp_path, [{"type": "shell"}, "x", {"id": 3, "type": None}], None)
    got = signals.read_activity(tmp_path)
    assert got["tasks"] == [{"id": "", "type": "shell"}, {"id": "", "type": ""}, {"id": "", "type": ""}]
    assert got["session_id"] is None


@pytest.mark.parametrize("tasks", [None, "x", {"id": "b1"}])
def test_write_activity_marks_unobserved_tasks_as_null(tmp_path, tasks):
    assert signals.write_activity(tmp_path, tasks, "s1")
    assert signals.read_activity(tmp_path)["tasks"] is None


def test_read_activity_tells_an_empty_list_from_null(tmp_path):
    empty, unknown = tmp_path / "e", tmp_path / "u"
    signals.write_activity(empty, [], "s1")
    signals.write_activity(unknown, None, "s1")
    assert signals.read_activity(empty)["tasks"] == []
    assert signals.read_activity(unknown)["tasks"] is None


def test_write_activity_returns_false_when_it_cannot_write(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert signals.write_activity(blocker, [], "s1") is False


@pytest.mark.parametrize(
    "data",
    [
        "{",
        '{"session_id": "s1", "tasks": []}',
        '{"at": "t", "session_id": "s1", "tasks": "x"}',
        '{"at": "t", "session_id": "s1", "tasks": [{"id": 1, "type": "shell"}]}',
        '{"at": "t", "session_id": 1, "tasks": []}',
        '{"at": "t", "session_id": "s1", "reply": 1, "tasks": []}',
        "[]",
    ],
)
def test_read_activity_returns_none_for_broken_records(tmp_path, data):
    (tmp_path / signals.ACTIVITY_FILE).write_text(data, encoding="utf-8")
    assert signals.read_activity(tmp_path) is None


def test_read_activity_returns_none_without_the_file(tmp_path):
    assert signals.read_activity(tmp_path) is None


def test_stop_without_background_tasks_writes_an_empty_list(tmp_path, relay):
    # 背景の作業が 0 件の Stop（入力が明示の空のリスト）は、空のリストと入力のセッションを書く（決定 2）
    call_entry("claude", "Stop", stop(tmp_path, f"終えた。\n\n{FENCE}", background_tasks=[]))
    got = signals.read_activity(relay)
    assert got["tasks"] == [] and got["session_id"] == SID
    assert signals.read_next(relay) is not None


def test_stop_with_background_tasks_writes_them_and_pushes_back(tmp_path, relay):
    out = call_entry("claude", "Stop", stop(tmp_path, FENCE, background_tasks=[SUBAGENT, SHELL]))
    assert "背景の作業が 2 件" in out["reason"]
    assert signals.read_activity(relay)["tasks"] == [
        {"id": SUBAGENT["id"], "type": "subagent"},
        {"id": SHELL["id"], "type": "shell"},
    ]


@pytest.mark.parametrize("reply", ["次のセグメントのブロックを持たない応答", ""])
def test_stop_writes_activity_before_the_early_returns(tmp_path, relay, reply):
    call_entry("claude", "Stop", stop(tmp_path, reply, background_tasks=[SHELL]))
    assert signals.read_activity(relay)["tasks"] == [{"id": SHELL["id"], "type": "shell"}]


def test_stop_without_the_field_writes_null(tmp_path, relay):
    payload = stop(tmp_path, "終えた")
    del payload["background_tasks"]
    call_entry("claude", "Stop", payload)
    assert signals.read_activity(relay)["tasks"] is None


def test_stop_with_a_string_field_writes_null(tmp_path, relay):
    call_entry("claude", "Stop", stop(tmp_path, "終えた", background_tasks="x"))
    assert signals.read_activity(relay)["tasks"] is None


def test_stop_keeps_its_verdict_when_activity_cannot_be_written(tmp_path, relay, monkeypatch):
    def broken(path, data):
        raise OSError("容量が無い")

    monkeypatch.setattr(signals, "replace_json", broken)
    out = call_entry("claude", "Stop", stop(tmp_path, FENCE, background_tasks=[SHELL]))
    assert "背景の作業が 1 件" in out["reason"]
    assert signals.read_activity(relay) is None


def test_stop_outside_the_relay_writes_nothing(tmp_path):
    call_entry("claude", "Stop", stop(tmp_path, FENCE))
    assert not list(tmp_path.rglob(signals.ACTIVITY_FILE))
