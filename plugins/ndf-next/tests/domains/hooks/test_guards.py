"""guard の本体の判定（sleep・script-chain・context-limit・ask-forward・relay-mark・unattended・stop-wait）。"""

from __future__ import annotations

import json

import pytest

from ndf.domains.hooks.guards import sleep
from ndf.domains.relay import signals

from ._hooks import advise_text, bash, call_entry, deny_reason, records, sample, stop

FENCE = "```ndf-next\n/goal 次の区間\n```"


@pytest.mark.parametrize(
    ("command", "denied"),
    [
        ("sleep 600", True),
        ("sleep 5", False),
        ("sleep 1m", True),
        ("sleep 600 &", False),
        ("sleep 30 && ndf plan status", True),
        ("ls; sleep 2; ndf relay status", True),
        ("sleep $X", True),
    ],
)
def test_sleep_guard(tmp_path, command, denied):
    assert (deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command))) is not None) is denied


def test_sleep_before_ndf_is_denied_even_in_a_background_call(tmp_path):
    payload = bash(tmp_path, "sleep 60; ndf plan status", run_in_background=True)
    assert "NDF のコマンド" in deny_reason(call_entry("claude", "PreToolUse", payload))
    assert call_entry("claude", "PreToolUse", bash(tmp_path, "sleep 600", run_in_background=True)) is None


def test_sleep_seconds():
    assert sleep.seconds(("1", "2s", "1m")) == 63 and sleep.seconds(("0.5",)) == 0.5 and sleep.seconds(("x",)) == float("inf")


@pytest.mark.parametrize(
    ("command", "denied"),
    [
        ("ndf plan status && ndf plan wait p1", True),
        ("until ndf plan done p1; do echo .; done", True),
        ("ndf plan wait p1", False),
        ("ls && git status", False),
        ("/opt/x/bin/ndf ci wait 1 | ndf relay status", True),
    ],
)
def test_script_chain_guard(tmp_path, command, denied):
    assert (deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command))) is not None) is denied


def _usage_transcript(tmp_path, tokens):
    line = {"type": "assistant", "message": {"usage": {"input_tokens": tokens}}}
    (tmp_path / "transcript.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")


def test_context_limit_denies_with_one_next_step_outside_the_relay(tmp_path):
    _usage_transcript(tmp_path, 900000)
    reason = deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "ls")))
    assert reason is not None and ".ndf/handoff/" in reason
    assert records()[-1]["notes"]["under_relay"] is False
    handoff = sample("claude", "PreToolUse", tmp_path, tool_name="Write", tool_input={"file_path": str(tmp_path / ".ndf/handoff/a.md"), "content": "x"})
    assert call_entry("claude", "PreToolUse", handoff) is None


def test_context_limit_under_the_relay_asks_for_the_next_section(tmp_path, monkeypatch):
    # 文脈の量が上限を超えたオーガナイザー（中継の下）に、区間の切り替えを求める（#2595）
    relay = tmp_path / "relay"
    relay.mkdir()
    monkeypatch.setenv("NDF_RELAY_DIR", str(relay))
    _usage_transcript(tmp_path, 900000)
    assert "次の区間のブロック" in deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "ls")))
    assert records()[-1]["notes"]["under_relay"] is True


def test_context_limit_does_not_record_relay_when_it_did_not_judge(tmp_path):
    # 判定しなかった行に under_relay を書かない（#2789）
    _usage_transcript(tmp_path, 10)
    call_entry("claude", "PreToolUse", bash(tmp_path, "ls"))
    assert "under_relay" not in records()[-1]["notes"] and records()[-1]["notes"]["context_tokens"] == 10


def test_ask_writes_signals_without_waiting(tmp_path, monkeypatch):
    relay = tmp_path / "relay"
    relay.mkdir()
    organizer = tmp_path / "org"
    monkeypatch.setenv("NDF_RELAY_DIR", str(relay))
    monkeypatch.setenv("NDF_ORGANIZER_DIR", str(organizer))
    monkeypatch.setenv("NDF_ORGANIZER_LANE", "lflow")
    questions = [{"question": "設計を承認するか", "header": "設計", "options": [], "multiSelect": False}]
    payload = sample("claude", "PreToolUse", tmp_path, tool_name="AskUserQuestion", tool_input={"questions": questions})
    assert call_entry("claude", "PreToolUse", payload) is None
    for path in (relay / signals.QUESTION_FILE, organizer / "questions" / "lflow.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["questions"] == questions
    assert records()[-1]["notes"]["forwarded"] == ["relay", "organizer"]


def test_relay_mark_writes_the_next_section_even_when_goal_continues(tmp_path, monkeypatch):
    relay = tmp_path / "relay"
    relay.mkdir()
    monkeypatch.setenv("NDF_RELAY_DIR", str(relay))
    # /goal の続き（stop_hook_active が真）でも切り替える（#2438）
    assert call_entry("claude", "Stop", stop(tmp_path, f"区間を終えた。\n\n{FENCE}", stop_hook_active=True)) is None
    mark = signals.read_next(relay)
    assert mark["block"] == "/goal 次の区間" and mark["goal_active"] is True


def test_relay_mark_pushes_back_while_background_tasks_remain(tmp_path, monkeypatch):
    # 背景の作業が残る応答は、/goal の続きでも差し戻す（#1877）
    relay = tmp_path / "relay"
    relay.mkdir()
    monkeypatch.setenv("NDF_RELAY_DIR", str(relay))
    out = call_entry("claude", "Stop", stop(tmp_path, FENCE, stop_hook_active=True, background_tasks=[{"id": "b1"}]))
    assert "背景の作業が 1 件" in out["reason"] and signals.read_next(relay) is None


def test_relay_mark_does_nothing_outside_the_relay(tmp_path):
    assert call_entry("claude", "Stop", stop(tmp_path, FENCE)) is None


def test_reporting_another_sessions_approval_wait_is_not_pushed_back(tmp_path):
    # 別のセッションの承認待ちを報告しただけの応答を差し戻さない（#2324）
    assert call_entry("claude", "Stop", stop(tmp_path, "レーン lflow が設計の承認を待っている。こちらの作業は終えた")) is None
    assert records()[-1]["notes"]["reply"] == "elsewhere_wait"


def test_unattended_requires_the_report_labels(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_FACTORY_RUN", "1")
    ok = "- 作業: 実装\n- 結果: 完了\n- 次にすること: 無し"
    assert call_entry("claude", "Stop", stop(tmp_path, ok)) is None
    assert "次にすること:" in call_entry("claude", "Stop", stop(tmp_path, "- 作業: 実装\n- 結果: 完了"))["reason"]
    assert call_entry("claude", "Stop", stop(tmp_path, "途中", background_tasks=[{"id": "b"}])) is None


def test_session_start_and_prompt_do_not_inject_when_nothing_is_wrong(tmp_path):
    assert call_entry("claude", "SessionStart", sample("claude", "SessionStart", tmp_path)) is None
    assert advise_text(call_entry("claude", "UserPromptSubmit", sample("claude", "UserPromptSubmit", tmp_path))) is None
