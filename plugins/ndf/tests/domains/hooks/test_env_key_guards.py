"""コントラクトの誤りで中継の変数の名前を引けないとき、guard は例外にせず、どこにも書かずに通す（#4126 の決定 3・受け入れ条件 5）。"""

from __future__ import annotations

import dataclasses

import pytest
from env_contract import use_env_contract, without_key

from ndf.domains.hooks import budget
from ndf.domains.hooks.guards import ask
from ndf.domains.relay import signals

from ._hooks import call_entry, deny_reason, records, sample, usage_transcript

ASK = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "q"}]}, "tool_use_id": "toolu_A"}


@pytest.fixture(params=["relay-dir", "relay-session"])
def broken(request, relay, tmp_path, monkeypatch):
    """子の会話の環境のまま、キーを 1 つ欠いたコントラクトを置く。返すのはオーガナイザーの保存先と、送った知らせの並び。"""
    monkeypatch.setenv("NDF_RELAY_SESSION", "s1")
    monkeypatch.setenv("NDF_ORGANIZER_DIR", str(tmp_path / "org"))
    monkeypatch.setenv("NDF_ORGANIZER_LANE", "l1")
    sent = []
    monkeypatch.setattr(ask.outbox, "notify_configured", lambda env: True)
    monkeypatch.setattr(ask.outbox, "enqueue", lambda *args: sent.append(args))
    monkeypatch.setattr(ask.outbox, "kick", lambda *args: None)
    use_env_contract(monkeypatch, tmp_path / "root", without_key(request.param))
    return tmp_path / "org", sent


def _pre(cwd, **override):
    return call_entry("claude", "PreToolUse", sample("claude", "PreToolUse", cwd, session_id="s1", **override))


def _assert_nothing_written(relay, broken):
    organizer, sent = broken
    assert not signals.question_present(relay) and not organizer.exists() and sent == []


def test_ask_passes_without_writing_anywhere(relay, broken, tmp_path):
    assert _pre(tmp_path, **ASK) is None
    assert records()[-1]["notes"]["forwarded"] == []
    _assert_nothing_written(relay, broken)


def test_ask_passes_when_the_deadline_has_passed(relay, broken, tmp_path, monkeypatch):
    found = budget.budget_for("claude", "PreToolUse")
    monkeypatch.setattr(budget, "budget_for", lambda runtime, event: dataclasses.replace(found, deadline=0.0))
    assert _pre(tmp_path, **ASK) is None
    assert records()[-1]["error"] == "deadline"
    _assert_nothing_written(relay, broken)


def test_other_tools_pass(relay, broken, tmp_path):
    assert _pre(tmp_path, tool_name="Read", tool_input={"file_path": str(tmp_path / "a.txt")}) is None
    _assert_nothing_written(relay, broken)


def test_the_context_limit_still_denies_with_the_advice_for_outside_the_relay(broken, tmp_path):
    usage_transcript(tmp_path, 900_000)
    reason = deny_reason(_pre(tmp_path, tool_name="Read", tool_input={"file_path": str(tmp_path / "a.txt")}))
    assert reason is not None and records()[-1]["notes"]["under_relay"] is False
    handoff = {"file_path": str(tmp_path / ".ndf/handoff/a.md"), "content": "x"}
    assert deny_reason(_pre(tmp_path, tool_name="Write", tool_input=handoff)) is None
