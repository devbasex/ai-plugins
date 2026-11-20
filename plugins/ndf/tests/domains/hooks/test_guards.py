"""guard の本体の判定（sleep・script-chain・context-limit・ask-forward・relay-marker・stop-wait）。unattended は test_unattended。"""

from __future__ import annotations

import json

import pytest

from ndf.domains.hooks import dispatch
from ndf.domains.hooks.guards import ask, sleep
from ndf.domains.relay import signals

from ._hooks import advise_text, bash, call_entry, deny_reason, records, sample, stop

FENCE = "```ndf-next\n/goal 次のセグメント\n```"


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
    handoff = sample(
        "claude", "PreToolUse", tmp_path, tool_name="Write", tool_input={"file_path": str(tmp_path / ".ndf/handoff/a.md"), "content": "x"}
    )
    assert call_entry("claude", "PreToolUse", handoff) is None


def test_context_limit_under_the_relay_asks_for_the_next_section(tmp_path, monkeypatch):
    # 文脈の量が上限を超えたオーガナイザー（中継の下）に、セグメントの切り替えを求める（#2595）
    relay = tmp_path / "relay"
    relay.mkdir()
    monkeypatch.setenv("NDF_RELAY_DIR", str(relay))
    _usage_transcript(tmp_path, 900000)
    assert "次のセグメントのブロック" in deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "ls")))
    assert records()[-1]["notes"]["under_relay"] is True


def test_context_limit_does_not_record_relay_when_it_did_not_judge(tmp_path):
    # 判定しなかった行に under_relay を書かない（#2789）
    _usage_transcript(tmp_path, 10)
    call_entry("claude", "PreToolUse", bash(tmp_path, "ls"))
    assert "under_relay" not in records()[-1]["notes"] and records()[-1]["notes"]["context_tokens"] == 10


def test_ask_writes_signals_without_waiting(tmp_path, monkeypatch):
    # 中継の下では今の起動（launch.json）と同じ NDF_RELAY_LAUNCH の hook だけが書ける（#3814 の I7）
    relay = tmp_path / "relay"
    relay.mkdir()
    signals.set_launch(relay, "L1")
    organizer = tmp_path / "org"
    monkeypatch.setenv("NDF_RELAY_DIR", str(relay))
    monkeypatch.setenv("NDF_RELAY_LAUNCH", "L1")
    monkeypatch.setenv("NDF_ORGANIZER_DIR", str(organizer))
    monkeypatch.setenv("NDF_ORGANIZER_LANE", "lflow")
    questions = [{"question": "設計を承認するか", "header": "設計", "options": [], "multiSelect": False}]
    payload = sample("claude", "PreToolUse", tmp_path, tool_name="AskUserQuestion", tool_input={"questions": questions})
    assert call_entry("claude", "PreToolUse", payload) is None
    for path in (relay / signals.QUESTION_FILE, organizer / "questions" / "lflow.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["questions"] == questions
    assert records()[-1]["notes"]["forwarded"] == ["relay", "organizer"]
    assert json.loads((relay / signals.QUESTION_FILE).read_text(encoding="utf-8"))["launch"] == "L1"


QUESTIONS = [{"question": "設計を承認するか", "header": "設計", "options": [], "multiSelect": False}]


def _ask_payload(cwd, tool_use_id="toolu_A"):
    return sample("claude", "PreToolUse", cwd, tool_name="AskUserQuestion", tool_input={"questions": QUESTIONS}, tool_use_id=tool_use_id)


@pytest.fixture
def relay(tmp_path, monkeypatch):
    """中継の作業ディレクトリ。今の起動は L1 で、hook の環境も L1。"""
    directory = tmp_path / "relay"
    directory.mkdir()
    signals.set_launch(directory, "L1")
    monkeypatch.setenv("NDF_RELAY_DIR", str(directory))
    monkeypatch.setenv("NDF_RELAY_LAUNCH", "L1")
    return directory


def test_i7_ask_is_denied_while_the_lock_is_held_even_when_repeated(relay, tmp_path):
    # 受け入れ条件 10・I7: ロックを別のプロセスが持つ間は拒否し、上限より多く繰り返しても案内へ下げない
    with signals.held(relay / f"{signals.QUESTION_FILE}.lock", 0) as got:
        assert got
        reasons = [deny_reason(call_entry("claude", "PreToolUse", _ask_payload(tmp_path))) for _ in range(4)]
    assert reasons == [ask.QUESTION_NOT_RECORDED] * 4
    assert not signals.question_present(relay)


def test_i7_ask_is_denied_when_writing_fails(relay, tmp_path, monkeypatch):
    # 受け入れ条件 10・I7: 書き込みが OSError を出したら拒否する
    def broken(*args, **kwargs):
        raise OSError("書けない")

    monkeypatch.setattr(signals, "write_question", broken)
    assert deny_reason(call_entry("claude", "PreToolUse", _ask_payload(tmp_path))) == ask.QUESTION_NOT_RECORDED


@pytest.mark.parametrize("launch", ["L1", None], ids=["stopped-launch", "no-launch"])
def test_i7_ask_of_a_stopped_launch_is_denied_and_keeps_the_current_question(relay, tmp_path, monkeypatch, launch):
    # I7: 止めた起動 L1 の hook は、次の起動 L2 の質問を上書きせず拒否する。起動の ID が無い hook も拒否する
    signals.set_launch(relay, "L2")
    q2 = {"tool_use_id": "toolu_Q2", "tool_input": {"questions": QUESTIONS}}
    assert signals.write_question(relay / signals.QUESTION_FILE, "s2", q2, 0.3, launch="L2")
    if launch is None:
        monkeypatch.delenv("NDF_RELAY_LAUNCH")
    assert deny_reason(call_entry("claude", "PreToolUse", _ask_payload(tmp_path))) == ask.QUESTION_NOT_RECORDED
    assert signals.read_question(relay)["tool_use_id"] == "toolu_Q2"


def test_i7_ask_guard_exception_denies_under_the_relay(relay, tmp_path, monkeypatch):
    # I7: guard の例外は拒否にする（fail_closed）
    def boom(env):
        raise RuntimeError("壊れた")

    monkeypatch.setattr(signals, "relay_launch", boom)
    reason = deny_reason(call_entry("claude", "PreToolUse", _ask_payload(tmp_path)))
    assert reason == dispatch.GUARD_FAILED.format(name="ask-forward", error="RuntimeError")


def test_ask_outside_the_relay_passes_even_when_the_organizer_lock_is_held(tmp_path, monkeypatch):
    # 中継の外では今と同じく、オーガナイザーの保存先に書けなくても通す
    organizer = tmp_path / "org"
    monkeypatch.setenv("NDF_ORGANIZER_DIR", str(organizer))
    monkeypatch.setenv("NDF_ORGANIZER_LANE", "lflow")
    path = organizer / "questions" / "lflow.json"
    path.parent.mkdir(parents=True)
    with signals.held(path.parent / f"{path.name}.lock", 0) as got:
        assert got
        assert call_entry("claude", "PreToolUse", _ask_payload(tmp_path)) is None
    assert records()[-1]["notes"]["forwarded"] == []


def test_relay_mark_writes_the_next_section_even_when_goal_continues(tmp_path, monkeypatch):
    relay = tmp_path / "relay"
    relay.mkdir()
    monkeypatch.setenv("NDF_RELAY_DIR", str(relay))
    # /goal の続き（stop_hook_active が真）でも切り替える（#2438）
    assert call_entry("claude", "Stop", stop(tmp_path, f"セグメントを終えた。\n\n{FENCE}", stop_hook_active=True)) is None
    mark = signals.read_next(relay)
    assert mark["block"] == "/goal 次のセグメント" and mark["goal_active"] is True


def test_relay_mark_pushes_back_while_background_tasks_remain(tmp_path, monkeypatch):
    # 背景の作業が残る応答は、/goal の続きでも変更を要求する（#1877）
    relay = tmp_path / "relay"
    relay.mkdir()
    monkeypatch.setenv("NDF_RELAY_DIR", str(relay))
    out = call_entry("claude", "Stop", stop(tmp_path, FENCE, stop_hook_active=True, background_tasks=[{"id": "b1"}]))
    assert "背景の作業が 1 件" in out["reason"] and signals.read_next(relay) is None


def test_relay_mark_does_nothing_outside_the_relay(tmp_path):
    assert call_entry("claude", "Stop", stop(tmp_path, FENCE)) is None


def test_reporting_another_sessions_approval_wait_is_not_pushed_back(tmp_path):
    # 別のセッションの承認待ちを報告しただけの応答を変更を要求しない（#2324）
    assert call_entry("claude", "Stop", stop(tmp_path, "レーン lflow が設計の承認を待っている。こちらの作業は終えた")) is None
    assert records()[-1]["notes"]["reply"] == "elsewhere_wait"


def test_session_start_and_prompt_do_not_inject_when_nothing_is_wrong(tmp_path):
    assert call_entry("claude", "SessionStart", sample("claude", "SessionStart", tmp_path)) is None
    assert advise_text(call_entry("claude", "UserPromptSubmit", sample("claude", "UserPromptSubmit", tmp_path))) is None
