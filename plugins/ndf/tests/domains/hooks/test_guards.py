"""guard の本体の判定（sleep・process-wait・script-chain・context-limit・ask-forward・relay-marker・stop-wait）。unattended は test_unattended。"""

from __future__ import annotations

import dataclasses
import json

import pytest

from ndf.domains.hooks import budget, dispatch, tables
from ndf.domains.hooks.guards import ask, process_wait, sleep
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
    # 既存の文書の Edit / Write は同じ会話での Read を前提にするので、引継ぎ文書の Read も通す（PR 4220 の r6-1）
    (tmp_path / ".git").mkdir()
    read = sample("claude", "PreToolUse", tmp_path, tool_name="Read", tool_input={"file_path": str(tmp_path / ".ndf/handoff/a.md")})
    assert call_entry("claude", "PreToolUse", read) is None
    other = sample("claude", "PreToolUse", tmp_path, tool_name="Read", tool_input={"file_path": str(tmp_path / "README.md")})
    assert deny_reason(call_entry("claude", "PreToolUse", other)) is not None
    # Read は解決後の読み取り先で照らす。`..` で引継ぎディレクトリの外を指す Read は止める（PR 4220 の r7-1）
    (tmp_path / ".ndf/handoff").mkdir(parents=True)
    (tmp_path / ".ndf/handoff/link.md").symlink_to(tmp_path / "secret")
    for outside in (".ndf/handoff/../../secret", ".ndf/handoff/sub/a.md", ".ndf/handoff/link.md"):
        escape = sample("claude", "PreToolUse", tmp_path, tool_name="Read", tool_input={"file_path": str(tmp_path / outside)})
        assert call_entry("claude", "PreToolUse", escape) is not None  # 拒否（続けば案内へ下がる）で、黙って通さない


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


@pytest.mark.parametrize(
    ("tokens", "env", "denied"),
    [
        (249_999, None, False),
        (250_000, None, False),
        (499_999, None, False),
        (500_000, None, True),
        (299_999, "300000", False),
        (300_000, "300000", True),
    ],
)
def test_context_limit_stops_at_the_operating_limit(tmp_path, monkeypatch, tokens, env, denied):
    # #3995 の 3995:4・3995:9: PreToolUse は運用上限（500,000。NDF_CONTEXT_LIMIT で替える）から止め、判断開始の値（250,000）では止めない
    if env is not None:
        monkeypatch.setenv("NDF_CONTEXT_LIMIT", env)
    _usage_transcript(tmp_path, tokens)
    assert (deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "ls"))) is not None) is denied


def test_context_limit_reads_the_limit_from_the_limits_table(tmp_path, monkeypatch):
    # #3995 の 3995:1: 上限の設定の 2 つの値を差し替えると、guard は差し替えた運用上限で止める（コードに値を持たない）
    real = tables.policy_value
    swapped = {"context.decide-from": 100_000, "context.limit": 200_000}
    monkeypatch.setattr(tables, "policy_value", lambda name, key: swapped[name] if name in swapped else real(name, key))
    _usage_transcript(tmp_path, 199_999)
    assert deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "ls"))) is None
    _usage_transcript(tmp_path, 200_000)
    assert deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "ls"))) is not None


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


def test_ask_under_a_wrapper_from_before_the_launch_id_is_written_without_it(relay, tmp_path, monkeypatch):
    # 更新前のラッパー（NDF_RELAY_LAUNCH を渡さず launch.json を書かない）の下で、更新後の hook が質問を出せる
    (relay / signals.LAUNCH_FILE).unlink()
    monkeypatch.delenv("NDF_RELAY_LAUNCH")
    assert call_entry("claude", "PreToolUse", _ask_payload(tmp_path)) is None
    written = signals.read_question(relay)
    assert written["tool_use_id"] == "toolu_A" and "launch" not in written


def test_i7_ask_is_denied_when_the_deadline_passes_before_the_guards(relay, tmp_path, monkeypatch):
    # I7: guard を呼ぶ前に締め切りの残りが無くても、中継の下の質問は通さず拒否する
    found = budget.budget_for("claude", "PreToolUse")
    monkeypatch.setattr(budget, "budget_for", lambda runtime, event: dataclasses.replace(found, deadline=0.0))
    assert deny_reason(call_entry("claude", "PreToolUse", _ask_payload(tmp_path))) == ask.QUESTION_NOT_RECORDED
    assert records()[-1]["error"] == "deadline" and not signals.question_present(relay)


def test_deadline_outside_the_relay_still_passes_the_question(tmp_path, monkeypatch):
    # 中継の外では締め切りを過ぎても今と同じく通す
    found = budget.budget_for("claude", "PreToolUse")
    monkeypatch.setattr(budget, "budget_for", lambda runtime, event: dataclasses.replace(found, deadline=0.0))
    assert call_entry("claude", "PreToolUse", _ask_payload(tmp_path)) is None
    assert records()[-1]["error"] == "deadline"


def test_i7_ask_guard_exception_denies_under_the_relay(relay, tmp_path, monkeypatch):
    # I7: guard の例外は拒否にする（fail_closed）
    def boom(env):
        raise RuntimeError("壊れた")

    monkeypatch.setattr(signals, "relay_launch", boom)
    reason = deny_reason(call_entry("claude", "PreToolUse", _ask_payload(tmp_path)))
    assert reason == dispatch.GUARD_FAILED.format(name="ask-forward", error="RuntimeError")


@pytest.mark.parametrize("under_relay", [True, False], ids=["relay", "outside"])
def test_ask_passes_when_the_organizer_and_the_notice_fail(tmp_path, monkeypatch, under_relay):
    # 拒否にするのは中継の質問ファイルの記録の失敗だけ。オーガナイザーと待ちの知らせの OSError は通して計測に残す
    if under_relay:
        relay = tmp_path / "relay"
        relay.mkdir()
        signals.set_launch(relay, "L1")
        monkeypatch.setenv("NDF_RELAY_DIR", str(relay))
        monkeypatch.setenv("NDF_RELAY_LAUNCH", "L1")
    monkeypatch.setenv("NDF_ORGANIZER_DIR", str(tmp_path / "org"))
    monkeypatch.setenv("NDF_ORGANIZER_LANE", "lflow")
    real = signals.write_question

    def organizer_broken(path, *args, **kwargs):
        if "launch" not in kwargs:
            raise OSError("オーガナイザーへ書けない")
        return real(path, *args, **kwargs)

    def enqueue_broken(*args, **kwargs):
        raise OSError("送信待ちへ書けない")

    monkeypatch.setattr(signals, "write_question", organizer_broken)
    monkeypatch.setattr(ask.outbox, "notify_configured", lambda env: True)
    monkeypatch.setattr(ask.outbox, "enqueue", enqueue_broken)
    assert call_entry("claude", "PreToolUse", _ask_payload(tmp_path)) is None
    notes = records()[-1]["notes"]
    assert notes["forwarded"] == (["relay"] if under_relay else [])
    assert notes["forward_errors"] == {"organizer": "OSError", "notify": "OSError"}


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
