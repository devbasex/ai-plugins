"""guard の本体の判定（sleep・process-wait・script-chain・context-limit・ask-forward・relay-marker・stop-wait）。unattended は test_unattended。"""

from __future__ import annotations

import json

import pytest

from ndf.domains.hooks.guards import process_wait, sleep
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


# 課題 #3851 の再現手順 1 のコマンド（本体に前景の sleep を持つプロセス名の待ち）
_REPRO = 'until ! pgrep -f "ndf ci watch --pr 1" >/dev/null; do sleep 20; done'
_PROCESS_WAIT = 'until ! pgrep -f "ndf ci watch --pr 1" >/dev/null; do :; done'


@pytest.mark.parametrize(
    ("command", "denied"),
    [
        # ループの本体の sleep は秒によらず止める（入れ子・until を含む）
        ("while true; do sleep 1; done", True),
        ("until [ -s out ]; do sleep 5; done", True),
        ("while :; do while :; do sleep 1; done; done", True),
        ("while :; do for i in 1 2; do sleep 1; done; done", True),
        # ループの外と for の中は上限（hook.sleep の 30 秒）で判定する
        ("sleep 30", False),
        ("sleep 31", True),
        ("for i in 1 2; do sleep 3; done", False),
        ("for i in 1 2; do sleep 600; done", True),
        # bash -c 系・eval の中身とラッパーのコマンドの後ろ
        ("bash -c 'while true; do sleep 1; done'", True),
        ("sh -c 'sleep 600'", True),
        ("zsh -c 'until false; do sleep 2; done'", True),
        ("dash -c 'sleep 5'", False),
        ("eval 'while :; do sleep 1; done'", True),
        ("timeout 590 sleep 600", True),
        ("timeout --signal TERM 590 sleep 600", True),
        ("nohup sleep 600", True),
        ("env X=1 nice -n 10 sleep 600", True),
        ("time sleep 600", True),
        ("exec sleep 600", True),
        ("command sleep 600", True),
        ("while :; do timeout 5 sleep 1; done", True),
        ("timeout 590 sleep 5", False),
        # 背景になる形は止めない（外側の & を中身が引き継ぐ）
        ("while true; do sleep 1; done &", False),
        ("bash -c 'while :; do sleep 1; done' &", False),
        ("nohup sleep 600 &", False),
        ("(sleep 600) &", False),
        ("{ sleep 600; } &", False),
        # 引数の中の語・読めないラッパーの引数は見ない
        ("echo bash -c 'sleep 600'", False),
        ("timeout --bogus 590 sleep 600", False),
        # ラッパーを外した後の名前はパスを外して読む（ラッパーだけで中身が無い形は見ない）
        ("/bin/sleep 600", True),
        ("timeout 590 /usr/bin/sleep 600", True),
        ("while :; do /bin/sleep 1; done", True),
        ("/bin/bash -c 'sleep 600'", True),
        ("/usr/bin/env X=1 /bin/sh -c 'while :; do sleep 1; done'", True),
        ("while :; do nohup timeout 5; done", False),
        ("/bin/sleeper 600", False),
    ],
)
def test_sleep_guard_reads_loops_inner_scripts_and_wrappers(tmp_path, command, denied):
    assert (deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command))) is not None) is denied


def test_sleep_in_a_loop_names_the_alternative_waits(tmp_path):
    reason = deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "while true; do sleep 1; done")))
    assert "Monitor" in reason and "run_in_background" in reason


def test_sleep_in_a_loop_passes_in_a_background_call(tmp_path):
    payload = bash(tmp_path, "while true; do sleep 1; done", run_in_background=True)
    assert call_entry("claude", "PreToolUse", payload) is None


@pytest.mark.parametrize(
    ("command", "denied"),
    [
        (_PROCESS_WAIT, True),
        ("while pkill --full x; do :; done", True),
        ("while pgrep -af x >/dev/null; do :; done", True),
        ("until ! pgrep -u root -f x; do :; done", True),
        ("while :; do pgrep -f x || break; done", True),
        ("bash -c 'until ! pgrep -f x; do :; done'", True),
        ("while timeout 5 pgrep -f x; do :; done", True),
        # ループの外の単発・-f の無い形・-f がオプションでない形は止めない
        ("pgrep -f x", False),
        ("pgrep -x dockerd >/dev/null 2>&1 || echo x", False),
        ("until ! pgrep -x dockerd; do sleep 1; done &", False),
        ("until ! pgrep -- -f; do :; done", False),
        ("until ! pgrep -d f x; do :; done", False),
        # ラッパーを外した後の名前はパスを外して読む（ラッパーだけで中身が無い形は見ない）
        ("while /usr/bin/pgrep -f x; do :; done", True),
        ("while /usr/bin/timeout 5 /usr/bin/pkill -f x; do :; done", True),
        ("/bin/bash -c 'until ! /usr/bin/pgrep -f x; do :; done'", True),
        ("while timeout 5; do :; done", False),
        ("while command -v pgrep; do :; done", False),
    ],
)
def test_process_wait_guard(tmp_path, command, denied):
    assert (deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command))) is not None) is denied


@pytest.mark.parametrize("command", [_REPRO, _PROCESS_WAIT, "until ! pgrep -f x; do :; done &"])
def test_process_wait_is_denied_in_a_background_call_with_the_file_wait(tmp_path, command):
    reason = deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, command, run_in_background=True)))
    assert reason is not None and "完了マーカー" in reason and "Monitor" in reason


def test_process_wait_repro_is_denied_in_the_foreground(tmp_path):
    reason = deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, _REPRO)))
    assert reason is not None and "完了マーカー" in reason


def test_process_wait_is_turned_off_by_its_name(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_HOOK_OFF", "process-wait")
    assert call_entry("claude", "PreToolUse", bash(tmp_path, _PROCESS_WAIT)) is None
    assert all(g["kind"] != "deny" for g in records()[-1]["guards"])
    # 本体の前景の sleep は別の guard（sleep）が止め、2 つとも外すと通る
    assert "完了マーカー" not in deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, _REPRO)))
    monkeypatch.setenv("NDF_HOOK_OFF", "process-wait,sleep")
    assert call_entry("claude", "PreToolUse", bash(tmp_path, _REPRO)) is None


@pytest.mark.parametrize(
    ("command", "full"),
    [
        ("pgrep -f x", True),
        ("pgrep -af x", True),
        ("pgrep x -f", True),
        ("pgrep -u root -f x", True),
        ("pkill -9 -f x", True),
        ("pkill -TERM -f x", True),
        ("pkill --full x", True),
        ("pgrep -- -f", False),
        ("pgrep -d f x", False),
        ("pgrep -df x", False),
        ("pgrep --delimiter -f x", False),
        ("pgrep -x f", False),
    ],
)
def test_full_match_reads_options_in_getopt_order(command, full):
    name, *args = command.split()
    assert process_wait._full_match(name, tuple(args)) is full


@pytest.mark.parametrize("command", ["if [ ; then", "env -S '\"sleep 600'"])
def test_shell_text_that_cannot_be_read_is_not_denied(tmp_path, command):
    assert call_entry("claude", "PreToolUse", bash(tmp_path, command)) is None


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
