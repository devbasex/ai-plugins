"""セッションの数え手（M3-H7・IH5）。"""

from __future__ import annotations

import json

from ndf import identity
from ndf.domains.hooks import counter as counter_mod
from ndf.domains.hooks import report_text
from ndf.domains.hooks.counter import Counter

from ._hooks import call_entry, deny_reason, records, sample, state_dir, stop

WAIT = "方針は 2 つある。どちらにしますか？"


def _prompt(tmp_path):
    return call_entry("claude", "UserPromptSubmit", sample("claude", "UserPromptSubmit", tmp_path))


def test_prose_wait_is_pushed_back_at_most_twice_per_user_input(tmp_path):
    outs = [call_entry("claude", "Stop", stop(tmp_path, WAIT)) for _ in range(5)]
    assert [o is not None for o in outs] == [True, True, False, False, False]
    assert [r["verdict"] for r in records() if r["event"] == "Stop"] == ["pushback", "pushback", "limited", "limited", "limited"]
    _prompt(tmp_path)
    assert call_entry("claude", "Stop", stop(tmp_path, WAIT)) is not None


def test_pushbacks_are_counted_across_reasons(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_FACTORY_RUN", "1")
    # 人のいない実行の Stop の guard は conductor の起動（マーカー）のあるセッションだけを見る（#3746）
    call_entry(
        "claude",
        "UserPromptSubmit",
        sample("claude", "UserPromptSubmit", tmp_path, prompt=f"/{identity.load().prefix}development-workflow #1"),
    )
    first = call_entry("claude", "Stop", stop(tmp_path, WAIT))
    # 1 回目は本文の待ちとセグメントの報告の形の 2 つの理由。セグメントの報告の形の説明が長く、文は上限で切られる
    assert first is not None and "セグメントの報告" in first["reason"]
    second = call_entry("claude", "Stop", stop(tmp_path, "- 作業: x\n"))
    # 2 回目は理由が 1 つで、形の説明（見出しの名前はコントラクトの文字列のまま）まで切られずに載る
    assert second is not None and report_text.TEXT_HEAD in second["reason"]
    assert call_entry("claude", "Stop", stop(tmp_path, "形の無い応答")) is None


def test_same_range_read_is_denied_twice_then_advised(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    payload = sample("claude", "PreToolUse", tmp_path, tool_name="Read", tool_input={"file_path": str(target)})
    outs = [call_entry("claude", "PreToolUse", payload) for _ in range(6)]
    assert outs[:2] == [None, None]
    assert [deny_reason(o) is not None for o in outs[2:4]] == [True, True]
    assert all("案内した" in o["hookSpecificOutput"]["additionalContext"] for o in outs[4:])
    _prompt(tmp_path)
    assert call_entry("claude", "PreToolUse", payload) is None


def test_relay_input_does_not_reset_the_counts(tmp_path):
    for _ in range(2):
        call_entry("claude", "Stop", stop(tmp_path, WAIT))
    session = sample("claude", "Stop", tmp_path)["session_id"]
    assert counter_mod.mark_relay(session, state_dir())
    _prompt(tmp_path)  # 中継が送った入力: マーカーを消すだけ
    assert call_entry("claude", "Stop", stop(tmp_path, WAIT)) is None
    _prompt(tmp_path)  # 人の入力: 数え直す
    assert call_entry("claude", "Stop", stop(tmp_path, WAIT)) is not None


def test_relay_mark_file_written_by_the_relay_does_not_reset_the_counts(tmp_path):
    from ndf.domains.relay import markers

    for _ in range(2):
        call_entry("claude", "Stop", stop(tmp_path, WAIT))
    session = sample("claude", "Stop", tmp_path)["session_id"]
    path = markers.mark(state_dir(), session)
    _prompt(tmp_path)  # 中継が送った入力: 中継のマーカーを消すだけ
    assert not path.exists()
    assert call_entry("claude", "Stop", stop(tmp_path, WAIT)) is None


def test_counter_reset_and_limits(tmp_path):
    with Counter.open("abc-1", tmp_path) as c:
        assert c.allow_pushback() and c.allow_pushback() and not c.allow_pushback()
        assert c.allow_deny("k") and c.allow_deny("k") and not c.allow_deny("k") and c.allow_deny("other")
        assert c.bump("r") == 1 and c.bump("r") == 2
        assert c.reset() == "human"
        assert c.data["turn"] == 1 and c.data["pushbacks"] == 0 and c.data["denies"] == {} and c.data["counts"] == {}
    data = json.loads((tmp_path / "hooks" / "sessions" / "abc-1.json").read_text(encoding="utf-8"))
    assert data["turn"] == 1


def test_counter_that_cannot_read_does_not_count(tmp_path):
    path = tmp_path / "hooks" / "sessions" / "s-2.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    with Counter.open("s-2", tmp_path) as c:
        assert not c.usable and not c.allow_pushback() and not c.allow_deny("k") and c.bump("x") == 0
    assert path.read_text(encoding="utf-8") == "{broken"
    with Counter.open("../x", tmp_path) as c:
        assert not c.usable


def test_remembered_value_is_taken_once_in_the_next_open(tmp_path):
    # #3995 の Task 11: 覚えた値は次の Counter.open で取り出せ、取り出した後は None。違う item の値は互いに残る
    with Counter.open("m-1", tmp_path) as c:
        c.remember("k", "A", {"tokens": 1}, 16)
        c.remember("k", "B", {"tokens": 2}, 16)
    with Counter.open("m-1", tmp_path) as c:
        assert c.take("k", "A")["tokens"] == 1
        assert c.take("k", "A") is None
    with Counter.open("m-1", tmp_path) as c:
        assert c.take("k", "A") is None and c.take("k", "B")["tokens"] == 2


def test_remember_drops_only_the_oldest_value_over_keep(tmp_path):
    with Counter.open("m-2", tmp_path) as c:
        for i in range(4):
            c.remember("k", f"id{i}", {"tokens": i}, 3)
        assert c.take("k", "id0") is None
        assert [c.take("k", f"id{i}")["tokens"] for i in (1, 2, 3)] == [1, 2, 3]


def test_remember_after_a_human_input_drops_the_values_of_the_previous_input(tmp_path):
    with Counter.open("m-3", tmp_path) as c:
        c.remember("k", "A", {"tokens": 1}, 16)
        assert c.reset("relay") == "relay"
        c.remember("k", "B", {"tokens": 2}, 16)
        assert c.take("k", "A")["tokens"] == 1
        c.remember("k", "C", {"tokens": 3}, 16)
        assert c.reset("human") == "human"
        c.remember("k", "D", {"tokens": 4}, 16)
        assert c.take("k", "B") is None and c.take("k", "C") is None and c.take("k", "D")["tokens"] == 4


def test_take_from_a_state_file_without_memo_is_none(tmp_path):
    # memo を持たない今の状態のファイルも壊れたとして扱わない（memo は _fresh に足さない）
    path = tmp_path / "hooks" / "sessions" / "m-4.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"turn": 2, "pushbacks": 0, "denies": {}, "counts": {}, "relay_mark": False}), encoding="utf-8")
    with Counter.open("m-4", tmp_path) as c:
        assert c.usable and c.take("k", "A") is None


def test_remember_and_take_without_a_usable_counter(tmp_path):
    c = Counter.unusable()
    c.remember("k", "A", {"tokens": 1}, 16)
    assert "memo" not in c.data and c.take("k", "A") is None


def test_remember_rebuilds_a_memo_entry_that_is_not_a_table(tmp_path):
    with Counter.open("m-5", tmp_path) as c:
        c.data["memo"] = {"k": ["x"]}
        assert c.take("k", "A") is None
        c.remember("k", "A", {"tokens": 1}, 16)
        assert c.take("k", "A")["tokens"] == 1
