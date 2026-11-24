"""guard `relay-busy` が、子の会話の起動と作業の開始を起動ごとの記録へ書く（#4049 の受け入れ条件 3・4・5・7）。"""

from __future__ import annotations

import dataclasses
import shutil

import pytest
from env_contract import use_env_contract, without_key

from ndf.domains.hooks import budget, dispatch, main, tables
from ndf.domains.hooks.context import HookContext
from ndf.domains.hooks.counter import Counter
from ndf.domains.hooks.guards import context_limit, relay_busy
from ndf.domains.relay import signals

from ._hooks import LAUNCH, boom, busy, call_entry, deny_reason, records, sample, usage_transcript

QUEUE, WAIT, TRAIN = "ndf plan queue p.json", "ndf lanes wait p", "python train.py | grep loss"


def bash(command, background=True):
    return {"tool_name": "Bash", "tool_input": {"command": command, "run_in_background": background}}


def monitor(command):
    return {"tool_name": "Monitor", "tool_input": {"command": command}}


AGENT = {"tool_name": "Agent", "tool_input": {"prompt": "p"}}
READ = {"tool_name": "Read", "tool_input": {"file_path": "a.txt"}}
STARTS = {
    "agent": AGENT,
    "agent-in-subagent": {**AGENT, "agent_id": "a1"},
    "bash": bash(QUEUE),
    "monitor": monitor(TRAIN),
    "bash-without-command": {"tool_name": "Bash", "tool_input": {"run_in_background": True}},
    "bash-command-not-text": bash(3),
    "monitor-without-command": {"tool_name": "Monitor", "tool_input": {}},
    "monitor-input-not-dict": {"tool_name": "Monitor", "tool_input": "x"},
}
OTHERS = {
    "watch-bash": bash(WAIT),
    "watch-monitor": monitor(WAIT),
    "foreground": bash(QUEUE, False),
    "no-flag": {"tool_name": "Bash", "tool_input": {"command": QUEUE}},
    "flag-text": bash(QUEUE, "true"),
    "bash-input-not-dict": {"tool_name": "Bash", "tool_input": "x"},
    "tool-not-text": {"tool_name": ["Agent"], "tool_input": {}},
    "read": READ,
}
starts = pytest.mark.parametrize("call", STARTS.values(), ids=STARTS)
others = pytest.mark.parametrize("call", OTHERS.values(), ids=OTHERS)


def payload(cwd, call, **override):
    return sample("claude", "PreToolUse", cwd, **{"session_id": "s1", **call, **override})


def pre(cwd, call, **override):
    return deny_reason(call_entry("claude", "PreToolUse", payload(cwd, call, **override)))


def session_start(cwd, source):
    return call_entry("claude", "SessionStart", sample("claude", "SessionStart", cwd, session_id="s1", source=source))


@pytest.fixture(params=["outside", "old-wrapper", "nested"])
def not_child(request, relay_child, monkeypatch):
    """中継の外・会話の ID を渡さないラッパーの下・入れ子の claude（会話の ID が違う）。"""
    if request.param == "nested":
        monkeypatch.setenv("NDF_RELAY_SESSION", "s0")
    else:
        monkeypatch.delenv("NDF_RELAY_DIR" if request.param == "outside" else "NDF_RELAY_SESSION")
    return relay_child


def no_room(monkeypatch, **changes):
    found = budget.budget_for("claude", "PreToolUse")
    monkeypatch.setattr(budget, "budget_for", lambda runtime, event: dataclasses.replace(found, **changes))


@starts
def test_the_start_of_a_work_is_recorded_and_passes(relay_child, tmp_path, call):
    assert pre(tmp_path, call) is None
    assert busy(relay_child) == (1, 0, "start")


@pytest.mark.parametrize("name", ["watch-bash", "watch-monitor"])
def test_the_start_of_a_watch_is_recorded_as_a_monitor_only(relay_child, tmp_path, name):
    session_start(tmp_path, "startup")
    assert pre(tmp_path, OTHERS[name]) is None
    assert busy(relay_child) == (0, 1, "session")


@others
def test_other_calls_pass_when_the_record_cannot_be_written(relay_child, tmp_path, monkeypatch, call):
    # 監視の開始と、バックグラウンドで始めない呼び出しは、記録が書けない状態でも通る
    session_start(tmp_path, "startup")
    monkeypatch.setattr(signals, "record_start", boom)
    monkeypatch.setattr(signals, "record_monitor", boom)
    monkeypatch.setattr(signals, "relay_dir", boom)
    assert pre(tmp_path, call) is None
    monkeypatch.undo()
    assert signals.read_busy(relay_child, LAUNCH, "s1")[0] == 0


@pytest.mark.parametrize("name", [n for n in OTHERS if not n.startswith("watch-")])
def test_calls_that_cannot_start_a_work_do_not_read_the_env_contract(relay_child, tmp_path, monkeypatch, name):
    monkeypatch.setattr(relay_busy, "is_child", boom)
    call = payload(tmp_path, OTHERS[name])
    ctx = HookContext("claude", "PreToolUse", call)
    svc = dispatch.GuardServices(tmp_path, Counter.unusable(), 0.0, params=dict(relay_busy.table_params()))
    assert relay_busy.check(ctx, svc) is None and relay_busy.on_deadline(ctx.env, call) is None


ZERO = (0, 0, "session")


@pytest.mark.parametrize("source, expected", [("startup", ZERO), ("resume", ZERO), ("compact", None), ("clear", None)])
def test_a_started_conversation_is_recorded_as_having_no_work(relay_child, tmp_path, source, expected):
    session_start(tmp_path, source)
    assert busy(relay_child) == expected


def test_a_conversation_whose_start_cannot_be_recorded_is_left_unknown(relay_child, tmp_path, monkeypatch):
    monkeypatch.delenv("NDF_RELAY_LAUNCH")
    assert session_start(tmp_path, "startup") is None
    assert busy(relay_child) is None


@pytest.mark.parametrize("call", [AGENT, bash(QUEUE), monitor(TRAIN)], ids=["agent", "bash", "monitor"])
def test_other_conversations_pass_before_anything_that_can_fail(not_child, tmp_path, monkeypatch, call):
    # 対象の会話かを分類より先に確かめる。fail_closed なので、ここで例外が出れば拒否になる
    for name in ("task_kind", "table_params", "only_commands"):
        monkeypatch.setattr(relay_busy, name, boom)
    for name in ("record_start", "write_busy", "relay_launch"):
        monkeypatch.setattr(signals, name, boom)
    assert pre(tmp_path, call) is None and session_start(tmp_path, "startup") is None
    assert relay_busy.on_deadline(HookContext("claude", "PreToolUse", {}).env, payload(tmp_path, call)) is None
    assert all(g["error"] == "" for r in records() for g in r["guards"]) and list(not_child.glob("busy-*")) == []


def _directory_gone(relay, monkeypatch):
    shutil.rmtree(relay)


def _no_launch(relay, monkeypatch):
    monkeypatch.delenv("NDF_RELAY_LAUNCH")


def _stopped(relay, monkeypatch):
    signals.set_launch(relay, None)


def _unwritable(relay, monkeypatch):
    def fail(*_args):
        raise OSError("書けない")

    monkeypatch.setattr(signals, "replace_json", fail)


@pytest.mark.parametrize("breaking", [_directory_gone, _no_launch, _stopped, _unwritable])
def test_a_start_that_cannot_be_recorded_is_denied_every_time(relay_child, tmp_path, monkeypatch, breaking):
    breaking(relay_child, monkeypatch)
    assert [pre(tmp_path, AGENT) for _ in range(3)] == [relay_busy.BUSY_START_FAILED] * 3
    assert pre(tmp_path, READ) is None and pre(tmp_path, bash(QUEUE, False)) is None and pre(tmp_path, monitor(WAIT)) is None


def test_a_start_is_denied_while_the_wrapper_is_stopping_the_child(relay_child, tmp_path, monkeypatch):
    real = tables.policy_value
    monkeypatch.setattr(tables, "policy_value", lambda name, key: 0 if name == "hook.counter-lock" else real(name, key))
    with signals.held(relay_child / signals.STOP_LOCK, 0) as got:
        assert got and pre(tmp_path, AGENT) == relay_busy.BUSY_START_FAILED
    assert busy(relay_child) is None and pre(tmp_path, AGENT) is None


def test_an_error_while_recording_denies_the_start(relay_child, tmp_path, monkeypatch):
    monkeypatch.setattr(signals, "record_start", boom)
    assert pre(tmp_path, AGENT) == dispatch.GUARD_FAILED.format(name="relay-busy", error="RuntimeError")


def test_the_context_limit_verdict_and_the_record_survive_the_used_up_budget(relay_child, tmp_path, monkeypatch):
    # 先に拒否された開始も「1 件以上」に書く（多めに倒し、その応答の Stop が書き直す）
    usage_transcript(tmp_path, 500_000)
    found, now, real = budget.budget_for("claude", "PreToolUse"), [0.0], context_limit.check

    def slow(ctx, svc):
        got = real(ctx, svc)
        now[0] = found.usable + 1.0
        return got

    monkeypatch.setattr(context_limit, "check", slow)
    ctx = HookContext("claude", "PreToolUse", payload(tmp_path, bash(QUEUE)))
    specs = dispatch.select(dispatch.load_specs(), ctx, frozenset())
    svc = dispatch.GuardServices(tmp_path, Counter.unusable(), 0.0)
    out = dispatch.run(ctx, specs, found, svc, clock=lambda: now[0])
    assert [(r.name, r.kind) for r in out.runs] == [("context-limit", "deny"), ("relay-busy", "pass")]
    assert out.verdict.kind == "deny" and busy(relay_child) == (1, 0, "start")


def test_a_used_up_budget_still_denies_only_the_start_that_cannot_be_recorded(relay_child, tmp_path, monkeypatch):
    no_room(monkeypatch, margin=100.0)
    monkeypatch.setattr(signals, "record_start", lambda *args: False)
    assert pre(tmp_path, AGENT) == relay_busy.BUSY_START_FAILED and pre(tmp_path, READ) is None and pre(tmp_path, bash(WAIT)) is None


@pytest.fixture(params=["before", "inside"])
def deadline(request, monkeypatch):
    """guard を呼ぶ前か、先の guard の中で締め切りを迎える。`relay-busy` の `check` は 1 度も呼ばれない。"""
    monkeypatch.setattr(relay_busy, "check", boom)
    if request.param == "before":
        return no_room(monkeypatch, deadline=0.0)

    def late(ctx, svc):
        raise main._Deadline()

    monkeypatch.setattr(context_limit, "check", late)


@starts
def test_a_start_is_denied_at_the_deadline(relay_child, deadline, tmp_path, call):
    assert pre(tmp_path, call) == relay_busy.BUSY_START_FAILED
    assert records()[-1]["error"] == "deadline"


@others
def test_other_calls_pass_at_the_deadline(relay_child, deadline, tmp_path, call):
    assert pre(tmp_path, call) is None
    assert records()[-1]["error"] == "deadline"


def test_a_recorded_start_passes_at_the_deadline_without_classifying_again(relay_child, tmp_path, monkeypatch):
    ctx = HookContext("claude", "PreToolUse", payload(tmp_path, bash(QUEUE)))
    svc = dispatch.GuardServices(tmp_path, Counter.unusable(), 0.0, params=dict(relay_busy.table_params()))
    assert relay_busy.on_deadline(ctx.env, ctx.payload) == relay_busy.BUSY_START_FAILED
    assert relay_busy.check(ctx, svc) is None
    monkeypatch.setattr(relay_busy, "table_params", boom)
    monkeypatch.setattr(relay_busy, "task_kind", boom)
    assert relay_busy.on_deadline(ctx.env, ctx.payload) is None


def test_the_deadline_of_a_session_start_passes(relay_child, tmp_path):
    ctx = HookContext("claude", "SessionStart", sample("claude", "SessionStart", tmp_path, session_id="s1"))
    assert relay_busy.on_deadline(ctx.env, {**ctx.payload, **AGENT}) is None


@pytest.mark.parametrize("rows", [[], [{"name": "relay-busy", "params": None}], None], ids=["no-row", "no-params", "unreadable"])
def test_without_the_wait_commands_every_background_call_is_a_work(relay_child, tmp_path, monkeypatch, rows):
    monkeypatch.setattr(tables, "guards", boom if rows is None else lambda: rows)
    env = HookContext("claude", "PreToolUse", {}).env
    assert relay_busy.on_deadline(env, payload(tmp_path, bash(WAIT))) == relay_busy.BUSY_START_FAILED
    assert relay_busy.on_deadline(env, payload(tmp_path, bash(WAIT, False))) is None


@pytest.mark.parametrize("key", ["relay-dir", "relay-session"])
@pytest.mark.parametrize("call", [AGENT, READ], ids=["agent", "read"])
def test_a_broken_env_contract_stops_nothing_and_writes_nothing(relay_child, tmp_path, monkeypatch, key, call):
    # #4126 の決定 3: 変数の名前を引けなければ、対象外として通す
    use_env_contract(monkeypatch, tmp_path / "root", without_key(key))
    assert pre(tmp_path, call) is None and records()[-1]["error"] == ""
    assert session_start(tmp_path, "startup") is None
    no_room(monkeypatch, deadline=0.0)
    assert pre(tmp_path, call) is None and records()[-1]["error"] == "deadline"
    assert list(relay_child.glob("busy-*")) == []
