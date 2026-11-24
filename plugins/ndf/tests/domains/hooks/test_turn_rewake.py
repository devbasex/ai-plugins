"""UserPromptSubmit の guard（`turn`）と、起床の hook の世代・起床の文の入力（#4068 の受け入れ条件 2・10）。

`test_guards.py` が 1,000 行の規則に近いため、#4068 の分をこのファイルに置く（実装計画の「修正対象」の頭）。
"""

from __future__ import annotations

from ndf.domains.hooks.counter import Counter

from ._hooks import call_entry, sample, state_dir


def _turn(session: str) -> int:
    with Counter.open(session, state_dir()) as c:
        return c.data.get("turn", 0)


def _prompt(tmp_path, text: str, **override):
    payload = sample("claude", "UserPromptSubmit", tmp_path, prompt=text, **override)
    call_entry("claude", "UserPromptSubmit", payload)
    return payload["session_id"]


def test_user_input_resets_the_counter_outside_the_relay(tmp_path):
    session = _prompt(tmp_path, "やって")
    before = _turn(session)
    _prompt(tmp_path, "次もやって")
    assert _turn(session) == before + 1


def test_user_input_resets_the_counter_under_the_relay(tmp_path, relay_child, monkeypatch):
    session = sample("claude", "UserPromptSubmit", tmp_path)["session_id"]
    monkeypatch.setenv("NDF_RELAY_SESSION", session)
    _prompt(tmp_path, "やって")
    before = _turn(session)
    _prompt(tmp_path, "次もやって")
    assert _turn(session) == before + 1


# ---------------------------------------------------------------- 起床の状態の世代（#4068 の受け入れ条件 2・10）

import pytest  # noqa: E402

from ndf.domains.hooks.guards import turn  # noqa: E402
from ndf.domains.relay import keep_cache, signals  # noqa: E402

from ._hooks import LAUNCH  # noqa: E402

SID = "0f0e0d0c-0b0a-4908-8706-050403020100"
NOW = 1_795_000_000.0
WAKE = keep_cache.WAKE_HEAD + "キャッシュを保つための起床（1/33）"


@pytest.fixture
def rewake_child(relay_child, monkeypatch):
    """起床の hook の登録を渡した今の起動の子（会話は UUID の形の `SID`）。"""
    signals.set_launch(relay_child, LAUNCH, rewake=True)
    monkeypatch.setenv("NDF_RELAY_SESSION", SID)
    monkeypatch.setattr(turn, "clock", lambda: NOW)
    return relay_child


def _input(tmp_path, text="やって"):
    return _prompt(tmp_path, text, session_id=SID)


def test_input_writes_a_turn_generation_even_without_state(tmp_path, rewake_child):
    _input(tmp_path)
    assert signals.read_keep(rewake_child, SID) == {"generation": {"at": NOW, "by": "turn", "message_id": None, "sleeps": False}}


def test_older_stop_cannot_take_the_generation_after_the_input(tmp_path, rewake_child):
    _input(tmp_path)
    got, _ = signals.bump_generation(rewake_child, LAUNCH, SID, signals.Generation(NOW - 5, "h1", "m1"), works=1)
    assert got == "older" and not (rewake_child / signals.CACHE_LOG).exists()


def test_input_revokes_a_sleeping_reservation(tmp_path, rewake_child):
    signals.update_keep(rewake_child, SID, {"generation": signals.Generation(NOW - 100, "h1", "m1", True)._asdict()})
    _input(tmp_path)
    assert signals.generation_of(signals.read_keep(rewake_child, SID)).by == "turn"


def test_no_state_for_another_launch_or_an_unregistered_launch(tmp_path, rewake_child):
    signals.set_launch(rewake_child, "b" * 32, rewake=True)
    _input(tmp_path)
    assert signals.read_keep(rewake_child, SID) is None
    signals.set_launch(rewake_child, LAUNCH, rewake=False)
    _input(tmp_path)
    assert signals.read_keep(rewake_child, SID) is None


def test_nested_claude_does_not_touch_the_state(tmp_path, rewake_child):
    _prompt(tmp_path, "やって", session_id="11111111-2222-4333-8444-555555555555")
    assert list(rewake_child.glob("keep-*.json")) == []


def test_opened_is_written_for_a_normal_input_during_a_wait(tmp_path, rewake_child):
    signals.update_keep(rewake_child, SID, {"generation": {"at": NOW - 9, "by": "h1"}, "wait_id": NOW - 9, "k": 0})
    _input(tmp_path)
    assert signals.read_keep(rewake_child, SID)["opened"] == NOW


@pytest.mark.parametrize(("text", "opened"), [(WAKE, False), ("割り込み", True)])
def test_pending_wake_input_is_relay_and_writes_no_opened(tmp_path, rewake_child, text, opened):
    keep = {"generation": {"at": NOW - 9, "by": "h1"}, "wait_id": NOW - 99, "k": 1, "pending": {"wake_id": "w1"}}
    signals.update_keep(rewake_child, SID, keep)
    before = _turn(SID)
    _input(tmp_path, text)
    assert ("opened" in signals.read_keep(rewake_child, SID)) is opened
    assert _turn(SID) == before + (1 if opened else 0)


def test_pasted_wake_text_counts_as_a_user_input_without_a_pending_wake(tmp_path, rewake_child):
    _input(tmp_path)
    before = _turn(SID)
    _input(tmp_path, WAKE)
    assert _turn(SID) == before + 1


def test_pasted_wake_text_outside_the_relay_counts_as_a_user_input(tmp_path):
    session = _prompt(tmp_path, "やって")
    before = _turn(session)
    _prompt(tmp_path, WAKE)
    assert _turn(session) == before + 1
