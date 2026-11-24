"""子の hook のエントリポイントから、ラッパーの見回りまでを通す（#4049 の受け入れ条件 3・4・5・6・7・8・11・12）。

ラッパーが子へ渡した環境変数で hook のエントリポイントを呼び、hook が書いた記録でラッパーが手を決める。時計は差し替える。
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
from ndf.domains.hooks import main
from ndf.domains.relay import accounts, signals, switch_decide
from ndf.domains.relay.account_decide import Pick
from relay_children import register

SAMPLES = Path(__file__).resolve().parents[1] / "hooks" / "samples" / "claude"
FENCE = "```ndf-next\n/goal 次\n```"
FULL = 800_000
AGENT = {"tool_name": "Agent", "tool_input": {"prompt": "p"}}


def task(kind: str, command: str | None = None) -> dict:
    row = {"id": "t1", "type": kind, "status": "running", "description": "d"}
    return row if command is None else {**row, "command": command}


SUBAGENT = task("subagent")
LANES = task("shell", "ndf lanes wait p.json --after 3")  # 監視を張り続けるオーガナイザーの形
WATCHES = [task("monitor", "ndf lanes wait p.json"), task("shell", "ndf plan wait p.json")]
QUEUE = task("shell", "ndf plan queue p.json")
TRAIN = task("monitor", "python train.py | grep loss")


class Hooks:
    """ラッパーが起動した子の hook。`env` は呼ぶ時点の子の環境（`at` で前の子の環境を選ぶ）。"""

    def __init__(self, p, cwd: Path, monkeypatch):
        self.p, self.cwd, self.monkeypatch = p, cwd, monkeypatch

    def env(self, at: int = -1) -> dict:
        return {k: v for k, v in self.p.spawn.calls[at][1].items() if k.startswith(("NDF_", "CLAUDE_"))}

    def call(self, version: str, event: str, at: int = -1, env: dict | None = None, **fields) -> dict | None:
        text = (SAMPLES / version / f"{event}.json").read_text(encoding="utf-8")
        text = text.replace("<cwd>", str(self.cwd)).replace("<transcript>", str(self.cwd / "transcript.jsonl"))
        given = {**self.env(at), **(env or {})}
        payload = {**json.loads(text), "session_id": given["NDF_RELAY_SESSION"], **fields}
        out = io.StringIO()
        with self.monkeypatch.context() as m:
            for name in [n for n in os.environ if n.startswith(("NDF_", "CLAUDE", "SLACK_"))]:
                m.delenv(name)
            for name, value in given.items():
                m.setenv(name, value)
            assert main.run_entry(["entry.py", "claude", event], io.StringIO(json.dumps(payload)), out) == 0
        return json.loads(out.getvalue()) if out.getvalue().strip() else None

    def session(self, **kw) -> None:
        self.call("2.1.325", "SessionStart", source="startup", **kw)

    def stop(self, *tasks, reply: str = "終えた", **kw) -> dict | None:
        return self.call("2.1.331", "Stop", last_assistant_message=reply, **{"background_tasks": list(tasks), **kw})

    def denied(self, call: dict = AGENT, **kw) -> bool:
        """作業を始める呼び出しが拒否されたか。"""
        out = self.call("2.1.325", "PreToolUse", **call, **kw)
        return (out or {}).get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def record(self) -> tuple | None:
        return signals.read_busy(self.p.w.dir, self.p.sec.launch, self.p.sec.session_id)


@pytest.fixture
def child(patrol, tmp_path, monkeypatch):
    """見回りと、子の hook。既定は運用上限に達した子。"""

    def make(tokens: int = FULL):
        p = patrol()
        p.tokens = tokens
        return p, Hooks(p, tmp_path, monkeypatch)

    return make


SHAPES = pytest.mark.parametrize(
    "tasks, waits",
    [(WATCHES, False), ([LANES], False), ([SUBAGENT], True), ([TRAIN], True), ([LANES, QUEUE], True)],
    ids=["watches", "organizer", "work", "work-monitor", "mixed"],
)


@SHAPES
def test_1_11_a_stopped_child_is_asked_for_the_handover_unless_works_are_left(child, tasks, waits):
    p, hooks = child()
    hooks.session()
    hooks.stop(*tasks)
    assert p.later(10.0).kind == ("wait_background" if waits else "handover")
    assert len(p.spawn.calls) == (1 if waits else 2)


@SHAPES
def test_2_11_a_stopped_child_with_works_or_watches_is_not_restarted_by_the_stall(child, tasks, waits):
    p, hooks = child(tokens=FULL - 1)
    hooks.stop(*tasks)
    steps = [p.later(1000.0) for _ in range(3)]
    assert {s.kind for s in steps} == {"wait_background"} and len(p.spawn.calls) == 1
    assert steps[0].reason.startswith("監視 ") is not waits


OFF = {"NDF_HOOK_OFF": "relay-busy"}


@pytest.mark.parametrize(
    "start, stop, env, kind",
    [
        (True, False, {}, "handover"),
        (False, True, {}, "handover"),  # SessionStart を書けなかった子も、最初の Stop が書く
        (False, False, {}, "wait_background"),  # hook の旧い子
        (True, True, OFF, "wait_background"),  # 開始を記録しない子
    ],
    ids=["session-only", "stop-only", "no-hook", "guard-off"],
)
def test_4_a_fresh_child_has_no_works_only_when_its_hook_says_so(child, start, stop, env, kind):
    p, hooks = child()
    if start:
        hooks.session(env=env)
    if stop:
        hooks.stop(env=env)
    assert p.later(10.0).kind == kind
    if kind != "handover":
        assert p.state() == ("waiting", switch_decide.BACKGROUND_UNKNOWN)
        assert p.later(1000.0).kind == "restart"


@pytest.mark.parametrize(
    "call",
    [
        AGENT,
        {"tool_name": "Bash", "tool_input": {"command": QUEUE["command"], "run_in_background": True}},
        {"tool_name": "Monitor", "tool_input": {"command": TRAIN["command"]}},
    ],
    ids=["agent", "bash", "monitor"],
)
def test_3_a_work_started_after_the_last_stop_is_waited_for(child, call):
    # Esc で中断して次の Stop が来ない子も、運用上限でも止まりの秒でも止めない
    p, hooks = child()
    hooks.stop()
    assert not hooks.denied(call)
    assert "1 件以上" in p.later(10.0).reason
    assert p.later(1000.0).kind == "wait_background" and len(p.spawn.calls) == 1


def test_3_a_watch_started_after_the_last_stop_does_not_hold_the_handover(child):
    p, hooks = child()
    hooks.stop()
    assert not hooks.denied({"tool_name": "Monitor", "tool_input": {"command": LANES["command"]}})
    assert p.later(10.0).kind == "handover"


def test_2_a_watch_started_after_the_last_stop_is_not_lost_by_the_stall(child):
    # 監視を張った応答を Esc で中断して次の Stop が来ない子は、止まりの秒でも起動し直さない
    p, hooks = child(tokens=FULL - 1)
    hooks.stop()
    assert not hooks.denied({"tool_name": "Monitor", "tool_input": {"command": LANES["command"]}})
    assert p.later(1000.0).kind == "wait_background" and len(p.spawn.calls) == 1


def test_2_a_watch_started_between_the_stall_decision_and_the_stop_keeps_the_child(child):
    p, hooks = child(tokens=FULL - 1)
    hooks.stop()
    p.now += 1000.0
    watch = {"tool_name": "Monitor", "tool_input": {"command": LANES["command"]}}
    assert p.look(lambda: hooks.denied(watch)).kind == "restart"
    assert p.sec.child.alive and p.look().kind == "wait_background"


def test_3_a_work_started_between_the_decision_and_the_stop_keeps_the_child(child):
    p, hooks = child()
    hooks.stop()
    p.now += 10.0
    started = []
    assert p.look(lambda: started.append(not hooks.denied())).kind == "handover" and started == [True]
    assert p.sec.child.alive and p.look().kind == "wait_background"


def test_3_a_work_is_refused_while_the_child_is_being_stopped_and_after(child, monkeypatch):
    p, hooks = child()
    hooks.stop()
    real, refused = p.w._stop_child, []
    monkeypatch.setattr(p.w, "_stop_child", lambda sec: refused.append(hooks.denied()) or real(sec))
    assert p.later(10.0).kind == "handover" and len(p.spawn.calls) == 2
    assert refused == [True] and hooks.denied(at=0)


def test_7_a_work_whose_start_cannot_be_recorded_is_refused_and_does_not_hold_the_handover(child, monkeypatch):
    p, hooks = child()
    hooks.stop()
    with monkeypatch.context() as m:
        m.setattr(signals, "write_busy", lambda *a, **kw: False)
        assert hooks.denied()
        assert not hooks.denied({"tool_name": "Monitor", "tool_input": {"command": LANES["command"]}})
        assert hooks.stop(SUBAGENT) is None  # 書けない Stop は変更要求しない
    assert hooks.record()[:2] == (0, "stop") and p.later(10.0).kind == "handover"


def test_5_the_late_hooks_of_the_stopped_child_and_a_nested_claude_do_not_touch_the_record(child):
    p, hooks = child()
    hooks.stop()
    assert p.later(10.0).kind == "handover"  # 起動 A を止めて、同じ会話を B で続ける
    hooks.stop(SUBAGENT)
    before = hooks.record()
    hooks.stop(at=0)
    assert hooks.denied(at=0)
    hooks.stop(session_id="nested")
    assert not hooks.denied(session_id="nested")  # 入れ子の claude の作業には関わらない
    assert hooks.record() == before and before[0] == 1
    assert p.later(1000.0).kind == "wait_background"


def test_6_8_the_wait_ends_at_the_stop_that_tells_no_work_is_left(child, monkeypatch):
    p, hooks = child()
    hooks.session()
    hooks.denied()
    assert "1 件以上" in p.later(10.0).reason
    hooks.stop(SUBAGENT)
    assert "作業 1 件" in p.look().reason
    hooks.stop(background_tasks=None)  # 列を観測できない Stop
    with monkeypatch.context() as m:
        m.setattr(signals, "write_busy", lambda *a, **kw: False)
        hooks.stop()  # 書けない Stop
    assert "作業 1 件" in p.look().reason
    hooks.stop()
    assert p.look().kind == "handover"
    hooks.session()
    assert p.later(10.0).kind == "active"


def test_11_a_next_block_with_only_a_watch_left_switches_even_after_the_handover_was_asked(child):
    p, hooks = child()
    hooks.session()
    assert p.later(10.0).kind == "handover"
    assert "1 件" in hooks.stop(SUBAGENT, WATCHES[1], reply=FENCE)["reason"]
    assert p.look().kind == "active"
    assert hooks.stop(WATCHES[1], reply=FENCE) is None
    assert p.later(10.0).kind == "switch"
    assert p.spawn.calls[2][0][-1] == "/goal 次" and p.sec.number == 2


def test_12_the_guard_moves_an_organizer_and_tells_to_rearm_its_watch(patrol, tmp_path, monkeypatch):
    register(tmp_path, "work1", "work2")
    accounts.use(tmp_path / "state", "work1")
    p = patrol()
    hooks = Hooks(p, tmp_path, monkeypatch)
    p.w._guard_pick = lambda sec, now: Pick("switch", "work2", "閾値を越えた")
    p.w._safe_point = lambda sec, idle: True
    hooks.stop(LANES, QUEUE)
    assert p.later(10.0).kind == "wait_background"
    hooks.stop(LANES)
    assert p.look().kind == "account"
    argv, env = p.spawn.calls[1]
    assert argv[1] == "--resume" and env["NDF_CLAUDE_ACCOUNT"] == "work2"
    assert argv[-1] == f"{switch_decide.GUARD_RESUME_PROMPT}。{switch_decide.MONITOR_RESUME}" and "--after" in argv[-1]
