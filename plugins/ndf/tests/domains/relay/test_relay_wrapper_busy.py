"""ラッパーがバックグラウンドの作業の記録を読み、止める直前に読み直し、待ちを状態に書く（#4049）。

子の hook の代わりに起動の記録を書き、見回りを 1 回ずつ打つ（時計は差し替える）。hook のエントリポイントから通すのは `test_relay_busy_flow.py`。
"""

from __future__ import annotations

import pytest
from ndf.domains.relay import accounts, registry, signals, switch_decide
from ndf.domains.relay.account_decide import Pick
from relay_children import FakeChild, register, rows_of

FULL = 800_000
GUARD = Pick("switch", "work2", "閾値を越えた。work2 へ替える")


def over(p) -> None:
    """文脈の量が運用上限に達した静かな子にする。"""
    p.tokens, p.now = FULL, p.now + 10.0


def at_guard(p, safe: bool = True) -> None:
    """見回りの替え（守り 1）の時点にする。"""
    p.w._guard_pick = lambda sec, now: GUARD
    p.w._safe_point = lambda sec, idle: safe


@pytest.fixture
def guarded(tmp_path, patrol):
    """登録済みアカウント work1 の子で、見回りの替えの時点にした見回り。"""

    def make(**changes):
        register(tmp_path, "work1", "work2")
        accounts.use(tmp_path / "state", "work1")
        p = patrol(**changes)
        at_guard(p)
        p.now = 10.0
        return p

    return make


@pytest.fixture
def reads(monkeypatch):
    """記録を読んだ回数。"""
    got = []
    real = signals.read_busy
    monkeypatch.setattr(signals, "read_busy", lambda *a: got.append(a) or real(*a))
    return got


def test_4_no_record_is_left_when_the_child_starts(patrol):
    # 受け入れ条件 4: 子を起動する時点で、ほかの起動の記録も今の起動の記録も無い。子は会話の ID を受け取る
    p = patrol()
    p.busy(1, "start")
    seen, nxt = [], FakeChild([], None)
    nxt.started = lambda directory, session_id, launch: seen.append(list(directory.glob("busy-*")))
    p.spawn.children[0].close()
    p.spawn.children[0] = nxt
    p.sec.child.terminate()
    assert p.look().kind == "restart" and seen == [[]]
    assert p.spawn.calls[1][1]["NDF_RELAY_SESSION"] == p.sec.session_id
    over(p)
    assert p.look().reason == switch_decide.BACKGROUND_UNKNOWN  # 前の起動の 1 件を読まない


def test_1_6_9_works_are_waited_for_and_the_handover_is_asked_once_after_they_end(patrol):
    # 受け入れ条件 1・6・9: 作業が残る間は待って状態に件数と時刻を書き、0 件になった見回りで 1 度だけ引継ぎを求める
    p = patrol()
    p.busy(1, "start")
    over(p)
    assert p.look().kind == "wait_background"
    state, reason = p.state()
    at = signals.read_busy(p.w.dir, p.sec.launch, p.sec.session_id)[2]
    assert state == "waiting" and "1 件以上" in reason and at in reason
    p.says()
    assert p.look().kind == "active" and p.state() == ("running", switch_decide.ACTIVE)
    p.busy(2)
    assert "作業 2 件" in p.later(10.0).reason and p.state()[0] == "waiting"
    p.busy(0)
    assert p.look().kind == "handover" and p.spawn.calls[1][0][-1] == switch_decide.HANDOVER_PROMPT
    p.busy(0)
    assert p.later(10.0).kind == "active"


@pytest.mark.parametrize("text", [None, "{"], ids=["none", "broken"])
def test_5_an_unknown_record_holds_the_handover_but_not_the_stall(patrol, text):
    p = patrol()
    if text is not None:
        (p.w.dir / f"busy-{p.sec.launch}.json").write_text(text, encoding="utf-8")
    over(p)
    assert p.look().reason == switch_decide.BACKGROUND_UNKNOWN and p.state() == ("waiting", switch_decide.BACKGROUND_UNKNOWN)
    assert p.later(1000.0).kind == "restart" and len(p.spawn.calls) == 2


@pytest.mark.parametrize("count, monitors", [(1, 0), (0, 1)], ids=["work", "monitor"])
def test_2_a_stalled_child_with_works_or_watches_is_never_restarted(patrol, count, monitors):
    # 受け入れ条件 2・11: 運用上限の手前の子は止まりの秒まで動いている子で、届いた後は待ち続ける（監視だけでも起動し直さない）
    p = patrol()
    p.tokens = FULL - 1
    p.busy(count, "stop", monitors)
    assert [p.later(100.0).kind for _ in range(20)] == ["active"] * 9 + ["wait_background"] * 11
    assert len(p.spawn.calls) == 1 and len(rows_of(p.w, "wait_background")) == 1


def test_10_the_record_is_read_only_at_the_patrols_that_need_it(patrol, reads):
    p = patrol()
    p.busy(1)
    p.tokens = FULL - 1
    p.later(10.0)  # 運用上限の手前で静か
    at_guard(p, safe=False)
    p.look()  # 守り 1 の止めてよい時点の外
    p.tokens = FULL
    p.says()
    p.look()  # 運用上限で静かでない
    assert reads == []
    p.later(10.0)
    assert len(reads) == 1
    p.w._limit_pick = lambda sec, screen: Pick("wait_reset", "work1", "上限")
    p.now += 1000.0
    assert {p.look().kind for _ in range(10)} == {"wait_reset"} and len(reads) == 1


@pytest.mark.parametrize("cause", ["handover", "account", "restart", "restart-unknown", "restart-watch", "handover-lost"])
def test_3_a_start_after_the_decision_keeps_the_child(patrol, guarded, cause):
    # 受け入れ条件 3・2: 手を決めた後に作業（止まりでは監視も）が始まれば子を止めず、終わりの時刻も残さず、次の見回りで待つ
    p = guarded() if cause == "account" else patrol()
    if cause != "restart-unknown":
        p.busy(0)
    if cause.startswith("handover"):
        over(p)
    p.now += 1000.0 if cause.startswith("restart") else 0.0
    changes = {
        "restart-watch": lambda: p.busy(0, "stop", 1),
        "handover-lost": (p.w.dir / f"busy-{p.sec.launch}.json").unlink,  # 記録を確かめられなくなった
    }
    assert p.look(changes.get(cause, lambda: p.busy(1, "start"))).kind == cause.partition("-")[0]
    assert p.sec.child.alive and p.sec.ended_at is None and len(p.spawn.calls) == 1
    assert p.look().kind == "wait_background"


def test_3_a_start_is_not_recorded_while_the_child_is_being_stopped_or_after(patrol, monkeypatch):
    p = patrol()
    p.busy(0)
    old, got, real = (p.sec.launch, p.sec.session_id), [], p.w._stop_child

    def start():
        got.append(signals.record_start(p.w.dir, *old, 0.0))

    monkeypatch.setattr(p.w, "_stop_child", lambda sec: start() or real(sec))
    over(p)
    assert p.look(start).kind == "handover" and got == [True]  # 決めた後の開始は記録でき、止めない
    p.busy(0)
    assert p.look().kind == "handover" and len(p.spawn.calls) == 2
    start()
    assert got == [True, False, False]


def test_the_record_is_not_read_again_when_a_question_holds_the_child(patrol, reads):
    p = patrol()
    (p.w.dir / signals.QUESTION_FILE).write_text("{}", encoding="utf-8")
    assert p.w._stop_if_clear(p.sec, allow=lambda found: True) == "held" and reads == []


@pytest.mark.parametrize("count", [0, 1, None])
def test_12_the_guard_switches_only_a_child_without_works_and_tells_to_rearm_the_watches(guarded, count):
    # 受け入れ条件 12: 監視だけの子は替えて張り直しを伝え、作業が残るか確かめられなければ替えない
    p = guarded()
    if count is not None:
        p.busy(count, "stop", 1)
    step = p.look()
    if count == 0:
        argv, env = p.spawn.calls[1]
        assert argv[1] == "--resume" and env["NDF_CLAUDE_ACCOUNT"] == "work2"
        assert argv[-1] == f"{switch_decide.GUARD_RESUME_PROMPT}。{switch_decide.MONITOR_RESUME}"
    else:
        assert step.kind == "wait_background" and len(p.spawn.calls) == 1


def test_12_the_limit_switches_without_reading_the_works(guarded, reads):
    # 受け入れ条件 12（守り 3）: 上限に達した後の替えは、作業が残っていても待たず、最初の入力も変えない
    p = guarded()
    p.busy(1)
    p.w._limit_pick = lambda sec, screen: Pick("switch", "work2", "上限") if len(p.spawn.calls) == 1 else None
    assert p.look().kind == "account" and reads == []
    assert p.spawn.calls[1][0][-1] == switch_decide.RESUME_PROMPT


def test_the_next_section_marker_switches_without_reading_the_works(patrol, reads):
    p = patrol()
    p.busy(1)
    assert signals.write_next(p.w.dir, p.sec.session_id, "/goal 次", False, 0.1)
    assert p.later(10.0).kind == "switch" and reads == [] and len(p.spawn.calls) == 2


def test_a_key_child_that_must_end_is_stopped_without_reading_the_works(patrol, reads):
    p = patrol()
    p.busy(1)
    p.sec.must_end = p.sec.end_now = True
    ended, stopped = p.w._key_step()
    assert (ended, stopped) == (None, True) and not p.sec.child.alive and reads == []


def test_a_broken_config_stops_the_child_without_reading_the_works(patrol, reads):
    p = patrol()
    p.busy(1)
    assert p.w._halt_broken(signals.SignalError("壊れた")).cause == "config_broken"
    assert not p.sec.child.alive and reads == []


def test_12_the_wait_before_the_guard_counts_into_the_length_of_the_section(guarded):
    # 決定 8: 600 秒待ってから替えたセグメントは待った時間を含む長さで数え（短くない）、再起動ループの保持も待つ間に解ける
    p = guarded(spin=switch_decide.Spin(3, 120.0))
    p.w.streak, p.w.spin_held = switch_decide.Streak(2, frozenset({"work1", "work2"})), True
    p.busy(1)
    first = p.sec
    assert p.look().kind == "wait_background" and first.ended_at is None
    p.busy(0)
    assert p.later(600.0).kind == "account"
    assert first.ended_at - first.started_at == 610.0
    assert p.w.streak.count == 0 and p.w.spin_held is False


def test_9_12_the_wait_before_the_guard_does_not_remain_once_the_child_moves(guarded, monkeypatch):
    # 受け入れ条件 9・12: 作業で待った後に止めてよい時点を外れたら running と待つ時点の理由に変わる。同じ理由では書き直さない
    p = guarded()
    p.busy(1)
    assert p.look().kind == "wait_background" and p.state()[0] == "waiting"
    written = []
    real = registry.write_state
    monkeypatch.setattr(registry, "write_state", lambda d, row: written.append(row["state"]) or real(d, row))
    p.look()
    at_guard(p, safe=False)
    p.look()
    p.look()
    assert p.state() == ("running", switch_decide.GUARD_POINT) and written == ["running"]
