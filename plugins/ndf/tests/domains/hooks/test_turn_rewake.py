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
