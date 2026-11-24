"""起床の hook（`hooks/rewake.py`）と、その登録・予算の検査（#4068・#4199 の詳細設計の起床の hook と hook の登録のセクション）。

時計と眠りの関数を差し替えて進め、実際には待たない。並行は hook の呼び出しの順を差し替えて流す。
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ndf.domains.hooks import budget, registration, rewake, tables
from ndf.domains.relay import keep_cache, signals

SID = "0f0e0d0c-0b0a-4908-8706-050403020100"
LAUNCH = "a" * 32
T0 = 1_795_000_000.0
WORK = [{"type": "agent", "id": "x1"}]
MONITOR = [{"type": "shell", "command": "ndf lanes wait"}]


def stamp(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")


def use(read=0, w1h=0, w5m=0, inp=1):
    return {
        "input_tokens": inp,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": w1h + w5m,
        "output_tokens": 3,
        "cache_creation": {"ephemeral_1h_input_tokens": w1h, "ephemeral_5m_input_tokens": w5m},
    }


def reply(mid, text="待つ", usage=None, at=T0):
    msg = {
        "id": mid,
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "usage": usage or use(read=99_000, w1h=1_000),
    }
    return json.dumps({"type": "assistant", "timestamp": stamp(at), "message": msg})


def said(text, at=T0 - 5):
    return json.dumps({"type": "user", "timestamp": stamp(at), "message": {"role": "user", "content": text}})


class Clock:
    """差し替えの時計。`sleep` は時刻を進めるだけで、`during` を渡すと眠るたびに呼ぶ。"""

    def __init__(self, now: float, during=None):
        self.now, self.during, self.slept = now, during, 0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept += 1
        self.now += seconds
        if self.during:
            self.during(self)


@pytest.fixture
def child(tmp_path):
    directory = tmp_path / "relay"
    directory.mkdir()
    signals.set_launch(directory, LAUNCH, rewake=True, claude_version="2.1.330")
    return directory


def env_of(directory: Path, **over) -> dict:
    base = {"NDF_RELAY_REWAKE": "1", "NDF_RELAY_DIR": str(directory), "NDF_RELAY_LAUNCH": LAUNCH, "NDF_RELAY_SESSION": SID}
    return {k: v for k, v in {**base, **over}.items() if v is not None}


def write_tail(directory: Path, *lines: str) -> Path:
    path = directory.parent / "transcript.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def stop(directory: Path, last="待つ", tasks=WORK, clock=None, environ=None, session=SID, path=None) -> tuple:
    clock = clock or Clock(T0 + 1)
    path = path or directory.parent / "transcript.jsonl"
    payload = {"session_id": session, "transcript_path": str(path), "last_assistant_message": last}
    if tasks is not None:
        payload["background_tasks"] = tasks
    err = io.StringIO()
    code = rewake.run(
        ["entry.py", "claude", "Stop", "rewake"], io.StringIO(json.dumps(payload)), err, environ or env_of(directory), clock, clock.sleep
    )
    return code, err.getvalue(), clock


def keep(directory: Path) -> dict:
    return signals.read_keep(directory, SID) or {}


def log(directory: Path) -> list:
    path = directory / signals.CACHE_LOG
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def kinds(directory: Path) -> list:
    return [row["kind"] for row in log(directory)]


@pytest.fixture
def tails(monkeypatch):
    got = {"n": 0, "size": None}
    real = rewake.transcript.read_tail

    def counted(path, size):
        got["n"] += 1
        got["size"] = size
        return real(path, size)

    monkeypatch.setattr(rewake.transcript, "read_tail", counted)
    return got


# ---------------------------------------------------------------- 起動しない・読まない


@pytest.mark.parametrize(
    "environ",
    [
        {"NDF_RELAY_REWAKE": None},
        {"NDF_RELAY_REWAKE": "0"},
        {"NDF_RELAY_DIR": None},
        {"NDF_RELAY_SESSION": "11111111-2222-4333-8444-555555555555"},
    ],
)
def test_no_sleep_and_no_reads_outside_a_registered_child(child, tails, environ):
    write_tail(child, said("x"), reply("m1"))
    code, _, clock = stop(child, environ=env_of(child, **environ))
    assert (code, clock.slept, tails["n"]) == (0, 0, 0) and keep(child) == {} and log(child) == []


def test_a_session_id_not_shaped_like_a_uuid_is_ignored(child, tails):
    write_tail(child, said("x"), reply("m1"))
    code, _, clock = stop(child, environ=env_of(child, NDF_RELAY_SESSION="s1"), session="s1")
    assert (code, clock.slept, tails["n"]) == (0, 0, 0) and not list(child.glob("keep-*"))


@pytest.mark.parametrize("tasks", [None, [], MONITOR, MONITOR + MONITOR])
def test_monitor_only_or_idle_stop_reads_nothing(child, tails, tasks):
    write_tail(child, said("x"), reply("m1"))
    code, _, clock = stop(child, tasks=tasks)
    assert (code, clock.slept, tails["n"]) == (0, 0, 0) and keep(child) == {}


# ---------------------------------------------------------------- 待つ子を起床させる


def test_waiting_child_is_woken_at_ttl_minus_margin(child, tails):
    write_tail(child, said("x"), reply("m1"))
    code, err, clock = stop(child, tasks=WORK + MONITOR)
    assert code == 2 and keep_cache.WAKE_HEAD in err and "（1/" in err
    assert clock.now == T0 + 3000
    assert tails["n"] == 1 and tails["size"] == tables.policy_value("hook.transcript-tail", "count")
    state = keep(child)
    assert state["k"] == 1 and state["pending"]["after"] == "m1" and state["pending"]["tokens"] == 100_001
    assert state["spent"] == pytest.approx(0.05 * 100_001 + 2000)
    assert kinds(child) == ["work_stop", "wait_start", "wake"]
    assert all(row["claude_version"] == "2.1.330" for row in log(child))
    assert [row["wait_id"] for row in log(child)] == [None, T0, T0]


def test_new_generation_during_sleep_cancels_the_wake(child):
    write_tail(child, said("x"), reply("m1"))

    def user_input(clock):
        if clock.slept == 2:
            signals.bump_generation(child, LAUNCH, SID, signals.Generation(clock.now, "turn"))

    code, err, _ = stop(child, clock=Clock(T0 + 1, user_input))
    assert (code, err) == (0, "") and "pending" not in keep(child) and "wake" not in kinds(child)


def test_question_file_means_no_wait_and_no_sleep(child):
    write_tail(child, said("x"), reply("m1"))
    (child / signals.QUESTION_FILE).write_text("{}", encoding="utf-8")
    code, _, clock = stop(child)
    assert (code, clock.slept) == (0, 0) and "wait_id" not in keep(child) and kinds(child) == ["work_stop"]


def test_short_ttl_and_past_do_not_sleep(child):
    write_tail(child, said("x"), reply("m1", usage=use(read=9, w5m=5)))
    code, _, clock = stop(child)
    assert (code, clock.slept) == (0, 0) and "stop_waking" not in kinds(child)
    assert keep(child)["ttl"] == "5m"


def test_wake_time_already_past_is_noted_once(child):
    write_tail(child, said("x"), reply("m1"))
    code, _, clock = stop(child, clock=Clock(T0 + 3000))
    assert (code, clock.slept) == (0, 0) and keep(child)["noted"] == ["past"]
    write_tail(child, said("x"), reply("m1"), said("y", at=T0 + 3100), reply("m2", at=T0 + 3200))
    stop(child, clock=Clock(T0 + 9999))
    assert kinds(child).count("stop_waking") == 1


def test_unknown_ttl_is_filled_from_state_or_noted(child):
    write_tail(child, said("x"), reply("m1", usage=use(read=9)))
    code, _, _ = stop(child)
    assert code == 0 and "wait_id" not in keep(child) and keep(child)["noted"] == ["ttl_unknown"] and "ttl" not in keep(child)
    signals.update_keep(child, SID, {**keep(child), "ttl": "1h"})
    write_tail(
        child, said("x"), reply("m1", usage=use(read=90_000)), said("y", at=T0 + 10), reply("m2", usage=use(read=90_000), at=T0 + 20)
    )
    code, _, _ = stop(child, clock=Clock(T0 + 21))
    assert code == 2 and keep(child)["last"]["ttl"] == "1h"


def test_zero_write_reply_keeps_the_ttl_an_earlier_hook_wrote_before_the_lock(child, monkeypatch):
    """#4199 の条件 1 の順: 後の応答の hook がロックの外で状態を読む（寿命は不明）→ 先の応答の hook が 1 時間の待ちを
    始める → 後の hook がロックを取る。後の hook の後も寿命は 1 時間で、起床の時点は 1 時間の寿命から決まる。"""
    early = child.parent / "early.jsonl"
    early.write_text(said("x") + "\n" + reply("m1") + "\n", encoding="utf-8")
    write_tail(child, said("x"), reply("m1"), said("y", at=T0 + 10), reply("m2", "続き", use(read=99_000), at=T0 + 20))
    real = rewake._settle
    seen = {}

    def halt(clock):
        raise RuntimeError("先の hook は待ちを始めて眠りに入ったまま")

    def settle(t, turn, tail, q_for, clock):
        if turn.message_id == "m2":
            seen["outside"] = keep(child)
            seen["earlier"] = stop(child, path=early, clock=Clock(T0 + 1, halt))[0]
            seen["ttl_before_lock"] = keep(child).get("ttl")
        return real(t, turn, tail, q_for, clock)

    monkeypatch.setattr(rewake, "_settle", settle)
    code, _, clock = stop(child, last="続き", clock=Clock(T0 + 21))
    assert seen == {"outside": {}, "earlier": 0, "ttl_before_lock": "1h"}
    assert code == 2 and clock.now == T0 + 20 + 3000
    assert keep(child)["ttl"] == "1h" and keep(child)["last"]["ttl"] == "1h" and "ttl_unknown" not in keep(child).get("noted", [])


def test_contested_reply_wakes_nobody(child):
    write_tail(child, said("x"), reply("m1"))
    holder = {}

    def other_hook(clock):
        if clock.slept == 1:
            holder["code"] = stop(child, clock=Clock(clock.now))[0]

    code, _, _ = stop(child, clock=Clock(T0 + 1, other_hook))
    assert code == 0 and holder["code"] == 0 and keep(child)["generation"]["by"] == "contested"
    assert "wait_id" not in keep(child) and kinds(child).count("wait_void") == 1 and kinds(child).count("contest") == 1


def test_limit_stops_the_wait_without_stopping_the_session(child, monkeypatch):
    monkeypatch.setattr(keep_cache, "n_max", lambda tokens, q: 0)
    write_tail(child, said("x"), reply("m1"))
    code, _, _ = stop(child)
    assert code == 0 and keep(child)["wait_stop"] == "limit" and "stopped" not in keep(child)
    assert log(child)[-1]["kind"] == "stop_waking" and log(child)[-1]["scope"] == "wait"


def test_older_stop_after_the_input_writes_nothing(child):
    signals.bump_generation(child, LAUNCH, SID, signals.Generation(T0 + 10, "turn"))
    write_tail(child, said("x"), reply("m1"))
    code, _, clock = stop(child, clock=Clock(T0 + 11))
    assert (code, clock.slept) == (0, 0) and keep(child)["generation"]["by"] == "turn" and log(child) == []


def test_another_launch_writes_nothing(child):
    signals.set_launch(child, "b" * 32, rewake=True)
    write_tail(child, said("x"), reply("m1"))
    assert stop(child)[0] == 0 and keep(child) == {} and log(child) == []


# ---------------------------------------------------------------- 次の Stop の精算


@pytest.fixture
def wake_head(monkeypatch):
    def judge(line):
        content = line["message"].get("content")
        return isinstance(content, str) and content.startswith(keep_cache.WAKE_HEAD)

    monkeypatch.setattr(keep_cache, "is_wake_input", judge)


def _woken(child, first_read):
    write_tail(child, said("x"), reply("m1"))
    assert stop(child)[0] == 2
    at = T0 + 3000
    write_tail(
        child,
        said("x"),
        reply("m1"),
        said(keep_cache.WAKE_HEAD + "起床", at=at),
        reply("w1", "待機を続ける", use(read=first_read, w1h=10), at=at + 2),
    )
    return stop(child, last="待機を続ける", clock=Clock(at + 3))


def test_woken_reply_that_hit_keeps_waking(child, wake_head):
    code, _, _ = _woken(child, 90_001)
    rows = [r for r in log(child) if r["kind"] == "wake_result"]
    assert code == 2 and rows[0]["outcome"] == "woke" and rows[0]["hit"] is True and rows[0]["priced"] is True
    assert rows[0]["tokens"] == 100_001 and rows[0]["model"] == "claude-opus-5-5" and rows[0]["w_1h"] == 2.0
    assert keep(child)["k"] == 2 and "stopped" not in keep(child)


def test_next_wake_counts_from_the_last_reply_even_when_the_woken_child_used_a_tool(child, wake_head):
    """#4068 の条件 14: 起きた子が Tool を呼んでも、次の起床の時点はその応答の最後の応答の時刻から数える。"""
    write_tail(child, said("x"), reply("m1"))
    assert stop(child)[0] == 2
    at = T0 + 3000
    call = {"id": "w1", "model": "claude-opus-5-5", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]}
    call["usage"] = use(read=95_000, w1h=10)
    result = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}
    write_tail(
        child,
        said("x"),
        reply("m1"),
        said(keep_cache.WAKE_HEAD + "起床", at=at),
        json.dumps({"type": "assistant", "timestamp": stamp(at + 2), "message": call}),
        json.dumps({"type": "user", "timestamp": stamp(at + 3), "message": result}),
        reply("w2", "待機を続ける", use(read=96_000, w1h=5), at=at + 40),
    )
    code, _, clock = stop(child, last="待機を続ける", clock=Clock(at + 41))
    rows = [r for r in log(child) if r["kind"] == "wake_result"]
    assert rows[0]["calls"] == 2 and rows[0]["hit"] is True
    assert code == 2 and clock.now == at + 40 + 3000 and keep(child)["k"] == 2


def test_woken_reply_that_missed_stops_the_session(child, wake_head):
    code, _, _ = _woken(child, 89_000)
    rows = [r for r in log(child) if r["kind"] == "wake_result"]
    assert code == 0 and rows[0]["hit"] is False and keep(child)["stopped"] == "miss" and "pending" not in keep(child)


def test_normal_input_ends_the_wait_with_the_last_reply(child, wake_head):
    write_tail(child, said("x"), reply("m1"))
    stop(child)
    at = T0 + 3000
    write_tail(
        child,
        said("x"),
        reply("m1"),
        said(keep_cache.WAKE_HEAD + "起床", at=at),
        reply("w1", "待機を続ける", use(read=95_000), at=at + 2),
        said("割り込み", at=at + 100),
        reply("m3", "done", use(read=20_000, w5m=50), at=at + 110),
    )
    code, _, _ = stop(child, last="done", tasks=[], clock=Clock(at + 111))
    end = [r for r in log(child) if r["kind"] == "wait_end"][0]
    assert code == 0 and end["opened_at"] == at + 100 and end["cache_read"] == 20_000 and end["hit"] is False
    # 起床の応答の Stop を流していないので、待ちの終わりの直前の応答は待ちを始めた m1
    assert (end["tokens"], end["ttl"], end["model"], end["w"], end["r"]) == (100_001, "1h", "claude-opus-5-5", 2.0, 0.05)
    assert "wait_id" not in keep(child) and keep(child)["last"]["message_id"] == "m3"


def test_next_wait_after_a_five_minute_ending_reply_does_not_wake(child, wake_head):
    """#4199 の条件 10: 待ちを終えた応答が 5 分へだけ書けば、その応答で始まる次の待ちでは起床させない。"""
    write_tail(child, said("x"), reply("m1"))
    stop(child)
    at = T0 + 3000
    write_tail(
        child,
        said("x"),
        reply("m1"),
        said(keep_cache.WAKE_HEAD + "起床", at=at),
        reply("w1", "待機を続ける", use(read=95_000), at=at + 2),
        said("割り込み", at=at + 100),
        reply("m3", "done", use(read=20_000, w5m=50), at=at + 110),
    )
    code, _, clock = stop(child, last="done", clock=Clock(at + 111))
    assert (code, clock.slept) == (0, 0) and kinds(child).count("wake") == 1
    assert [r["ttl"] for r in log(child) if r["kind"] == "wait_start"] == ["1h", "5m"]
    assert keep(child)["ttl"] == "5m" and keep(child)["wait_id"] == at + 110


def test_unknown_input_after_a_wake_keeps_the_wait_and_stops_it(child, monkeypatch):
    write_tail(child, said("x"), reply("m1"))
    stop(child)
    at = T0 + 3000
    write_tail(child, said("x"), reply("m1"), said("?", at=at), reply("w1", "待機を続ける", at=at + 2))
    code, _, _ = stop(child, last="待機を続ける", tasks=[], clock=Clock(at + 3))
    rows = [r for r in log(child) if r["kind"] == "wake_result"]
    assert code == 0 and rows[0]["outcome"] == "unknown" and rows[0]["priced"] is False
    assert keep(child)["wait_stop"] == "unmatched" and keep(child)["k"] == 1 and "wait_end" not in kinds(child)


# ---------------------------------------------------------------- 記録に無い・形が違う


def test_unrecorded_reply_revokes_a_sleeping_reservation(child):
    write_tail(child, said("x"), reply("m1"))
    held = {}

    def pushback_stop(clock):
        if clock.slept == 1:
            held["code"] = stop(child, last="続き", tasks=[], clock=Clock(clock.now))[0]

    code, _, _ = stop(child, clock=Clock(T0 + 1, pushback_stop))
    gen = keep(child)["generation"]
    assert code == 0 and held["code"] == 0 and gen["by"] == "arrival" and gen["at"] == T0 and gen["message_id"] == "m1"
    arrival = [r for r in log(child) if r["kind"] == "arrival"][0]
    assert arrival["revoked"] is True and arrival["voided"] is True and "wait_id" not in keep(child)
    assert "wait_void" in kinds(child) and keep(child)["noted"] == ["unrecorded"]


def test_unrecorded_with_a_woken_wait_keeps_it(child):
    write_tail(child, said("x"), reply("m1"))
    signals.bump_generation(child, LAUNCH, SID, signals.Generation(T0 - 50, "h0", "m0", True))
    signals.update_keep(child, SID, {**keep(child), "wait_id": T0 - 50, "k": 1, "n_max": 9, "spent": 1})
    code, _, _ = stop(child, last="未記録")
    assert code == 0 and keep(child)["wait_id"] == T0 - 50 and "wait_void" not in kinds(child)


def test_unreadable_transcript_does_not_wake_and_reads_again_on_the_next_stop(child):
    """#4068 の条件 7 の 1 行目: 会話の記録が読めなければ起床させず、会話では止めず、次の Stop で読み直す。"""
    code, _, clock = stop(child)
    assert (code, clock.slept) == (0, 0) and "stopped" not in keep(child) and keep(child)["noted"] == ["unrecorded"]
    write_tail(child, said("x"), reply("m1"))
    code, _, clock = stop(child, clock=Clock(T0 + 2))
    assert code == 2 and clock.now == T0 + 3000


def test_form_stops_the_session_once(child):
    broken = json.loads(reply("m1"))
    del broken["message"]["usage"]["output_tokens"]
    write_tail(child, said("x"), json.dumps(broken))
    assert stop(child)[0] == 0 and keep(child)["stopped"] == "form"
    assert stop(child, clock=Clock(T0 + 2))[0] == 0
    assert [r["scope"] for r in log(child) if r["kind"] == "stop_waking"] == ["session"]


# ---------------------------------------------------------------- 登録・予算・エントリポイント


def test_rewake_settings_hold_one_background_stop_with_an_absolute_entry():
    got = registration.rewake_settings(tables.PLUGIN_ROOT)
    (entry,) = got["hooks"]["Stop"][0]["hooks"]
    assert list(got["hooks"]) == ["Stop"] and entry["asyncRewake"] is True
    assert entry["timeout"] == tables.policy_value("hook.budget.stop-rewake", "timeout")
    assert str(tables.PLUGIN_ROOT.resolve() / "hooks" / "entry.py") in entry["command"] and entry["command"].endswith(" claude Stop rewake")
    assert "${" + "CLAUDE_PLUGIN_ROOT}" not in entry["command"]


def test_budget_checks_hold_for_the_shipped_values():
    assert budget.check_budget(budget.registrations_of(tables.PLUGIN_ROOT)) == []
    assert registration.rewake_unreachable() is None
    assert [r for r in budget.registrations_of(tables.PLUGIN_ROOT) if r.get("async_rewake")] == [
        {"runtime": "claude", "event": "Stop", "timeout": 3300, "async_rewake": True}
    ]


@pytest.mark.parametrize(("seconds", "interval"), [(120, 60), (600, 0), (600, 9), (550, 10)])
def test_unreachable_wake_is_a_budget_violation(monkeypatch, seconds, interval):
    real = tables.policy_value

    def swapped(name, key):
        if name == "cache.wake":
            return {"seconds": seconds, "interval": interval}[key]
        return real(name, key)

    monkeypatch.setattr(tables, "policy_value", swapped)
    assert registration.rewake_unreachable() is not None
    assert any("バックグラウンド" in e for e in budget.check_budget(budget.registrations_of(tables.PLUGIN_ROOT)))


def test_async_rewake_in_the_plugin_registration_is_a_violation():
    regs = budget.registrations_of(tables.PLUGIN_ROOT) + [
        {"runtime": "claude", "event": "Stop", "timeout": 12, "async_rewake": True, "file": True}
    ]
    assert any("asyncRewake" in e for e in budget.check_budget(regs))


def test_missing_async_rewake_row_refuses_the_entry(monkeypatch):
    monkeypatch.setattr(budget, "rewake_budget", lambda runtime, event: None)
    with pytest.raises(tables.TableError):
        registration.rewake_entry("claude", "Stop")


def test_entry_routes_rewake_and_keeps_the_three_arguments(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("hook_entry", tables.PLUGIN_ROOT / "hooks" / "entry.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    calls = []
    monkeypatch.setattr(entry, "call", lambda module, func, argv: calls.append((module, func, argv)) or (2 if func == "run" else 0))
    assert entry.main(["entry.py", "claude", "PreToolUse"]) == 0
    assert entry.main(["entry.py", "claude", "Stop", "rewake"]) == 2
    assert calls == [
        ("ndf.domains.hooks.main", "run_entry", ["entry.py", "claude", "PreToolUse"]),
        ("ndf.domains.hooks.rewake", "run", ["entry.py", "claude", "Stop", "rewake"]),
    ]
