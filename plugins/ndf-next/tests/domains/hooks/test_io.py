"""エントリポイントの処理の間は通信せず、外部コマンドは NDF 自身の切り離した処理だけ（M3-H9・IH7）。送信待ち。"""

from __future__ import annotations

import json
import socket
import subprocess

import pytest

from ndf.domains.hooks import outbox, sender

from ._hooks import call_entry, read_sample, sample, sample_paths, state_dir, stop


@pytest.fixture
def no_network(monkeypatch):
    opened = []

    def refuse(*args, **kwargs):
        opened.append(args)
        raise OSError("hook のエントリポイントの中で socket を作った")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    return opened


@pytest.fixture
def spawned(monkeypatch):
    started = []

    class Recorder:
        def __init__(self, args, **kwargs):
            started.append((list(args), kwargs))

    def no_run(*args, **kwargs):
        raise AssertionError(f"hook のエントリポイントが外部コマンドを待った: {args}")

    monkeypatch.setattr(subprocess, "Popen", Recorder)
    monkeypatch.setattr(subprocess, "run", no_run)
    return started


def test_all_samples_run_without_sockets_or_waited_commands(tmp_path, no_network, spawned):
    for path in sample_paths():
        call_entry(path.parts[-3], path.stem, read_sample(path, tmp_path))
    assert no_network == [] and spawned == []


def test_session_start_does_not_fetch_or_touch_learning_settings(tmp_path, no_network, spawned):
    assert call_entry("claude", "SessionStart", sample("claude", "SessionStart", tmp_path)) is None
    assert spawned == []


def test_notifications_are_queued_and_the_sender_is_started_detached(tmp_path, monkeypatch, no_network, spawned):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "x")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")
    payload = sample("claude", "PreToolUse", tmp_path, tool_name="AskUserQuestion", tool_input={"questions": [{"question": "進めるか"}]})
    call_entry("claude", "PreToolUse", payload)
    files = list(outbox.outbox_dir(state_dir(), {"SLACK_BOT_TOKEN": "x", "SLACK_CHANNEL_ID": "C1"}).glob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text(encoding="utf-8"))["payload"]["text"] == "進めるか"
    ((args, kwargs),) = spawned
    assert args[-2:] == [outbox.SENDER_MODULE, str(state_dir())] and kwargs["start_new_session"] is True


def test_pushback_limit_queues_a_wait_notice(tmp_path, monkeypatch, spawned):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "x")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")
    for _ in range(3):
        call_entry("claude", "Stop", stop(tmp_path, "どちらにしますか？"))
    box = outbox.outbox_dir(state_dir(), {"SLACK_BOT_TOKEN": "x", "SLACK_CHANNEL_ID": "C1"})
    items = [json.loads(p.read_text(encoding="utf-8")) for p in box.glob("*.json")]
    assert [i["kind"] for i in items] == ["wait"]


DEST = {"SLACK_BOT_TOKEN": "t", "SLACK_CHANNEL_ID": "C"}


def test_kick_does_not_start_a_second_sender(tmp_path, spawned):
    box = outbox.outbox_dir(tmp_path, DEST)
    fd = outbox.try_lock(box)
    assert fd is not None
    try:
        assert outbox.kick(tmp_path, DEST) is False and spawned == []
    finally:
        outbox.release(fd)
    assert outbox.kick(tmp_path, DEST) is True and len(spawned) == 1


def test_sender_drops_without_slack_and_keeps_failed_sends(tmp_path, monkeypatch):
    bare = outbox.outbox_dir(tmp_path, {})
    outbox.enqueue(tmp_path, "wait", {"text": "a"}, {})
    assert sender.drain(bare, "", "") == 1 and not list(bare.glob("*.json"))
    box = outbox.outbox_dir(tmp_path, DEST)
    outbox.enqueue(tmp_path, "wait", {"text": "b"}, DEST)
    monkeypatch.setattr(sender.notify, "post_json", lambda *a, **k: None)
    assert sender.drain(box, "t", "C") == 0 and len(list(box.glob("*.json"))) == 1
    sent = []
    monkeypatch.setattr(sender.notify, "post_json", lambda url, body, headers, timeout: sent.append(body) or {"ok": True})
    assert sender.drain(box, "t", "C") == 1 and sent[0]["channel"] == "C" and "b" in sent[0]["text"]


def test_queues_are_kept_per_destination(tmp_path, monkeypatch):
    # プロジェクト A の宛先で残った通知を、プロジェクト B の接続で起動した送り手が送らない
    other = {"SLACK_BOT_TOKEN": "t-b", "SLACK_CHANNEL_ID": "C-B"}
    outbox.enqueue(tmp_path, "question", {"text": "A の問い"}, DEST)
    sent = []
    monkeypatch.setattr(sender.notify, "post_json", lambda url, body, headers, timeout: sent.append((headers, body)) or {"ok": True})
    assert sender.run(tmp_path, other) == 0 and sent == []
    assert sender.run(tmp_path, DEST) == 1 and sent[0][1]["channel"] == "C" and sent[0][0]["Authorization"] == "Bearer t"


def test_sender_does_not_send_while_another_holds_the_lock(tmp_path, monkeypatch):
    outbox.enqueue(tmp_path, "wait", {"text": "a"}, DEST)
    monkeypatch.setattr(sender.notify, "post_json", lambda *a, **k: pytest.fail("錠を持たない送り手が送った"))
    fd = outbox.try_lock(outbox.outbox_dir(tmp_path, DEST))
    try:
        assert sender.run(tmp_path, DEST) == 0
    finally:
        outbox.release(fd)


def test_sender_reads_the_queue_again_after_releasing_the_lock(tmp_path, monkeypatch):
    # 送っている間に届いた通知（その kick は走っている送り手を見て起動しない）も、同じ送り手が送る
    outbox.enqueue(tmp_path, "wait", {"text": "first"}, DEST)
    sent = []

    def post(url, body, headers, timeout):
        sent.append(body["text"])
        if len(sent) == 1:
            outbox.enqueue(tmp_path, "wait", {"text": "second"}, DEST)
        return {"ok": True}

    monkeypatch.setattr(sender.notify, "post_json", post)
    assert sender.run(tmp_path, DEST) == 2 and ["first" in sent[0], "second" in sent[1]] == [True, True]
