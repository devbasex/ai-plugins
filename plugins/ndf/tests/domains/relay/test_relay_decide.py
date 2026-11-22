"""中継の判断（純粋な関数）: セグメントの切り替え・アカウント・リレーハブの上限とバージョンの混ざり。端末と時計を使わない（#2962）。"""

from __future__ import annotations

import dataclasses
import json

import pytest

from ndf.domains.relay import account_decide, account_registry, hub_decide, switch_decide
from ndf.domains.relay.account_decide import Pick, Usage

BASE = switch_decide.SectionState(
    question=False,
    next_block=None,
    child_alive=True,
    child_exit=None,
    idle_seconds=10.0,
    quiet_seconds=5.0,
    stall_seconds=1800.0,
)


def state(**changes):
    return dataclasses.replace(BASE, **changes)


def test_question_is_forwarded_before_anything_else():
    got = switch_decide.decide(state(question=True, next_block="/goal x", auth_failed=True))
    assert got.kind == "forward"


@pytest.mark.parametrize(
    "changes",
    [
        {"next_block": "/goal 次", "idle_seconds": 60.0, "next_age_seconds": 60.0},
        {"context_tokens": 800_000, "context_limit": 800_000},
        {"idle_seconds": 1800.0},
        {"child_alive": False, "child_exit": 1},
        {"limit_pick": Pick("switch", "main", "上限")},
    ],
)
def test_i1_forwarded_question_waits_instead_of_switching_handing_over_or_restarting(changes):
    # I1・受け入れ条件 1・2・3: 転送した回答待ちの質問がある間は、マーカー・文脈の上限・止まりの判断より先に wait を返す
    got = switch_decide.decide(state(question=True, question_forwarded=True, question_id="toolu_1", **changes))
    assert (got.kind, got.reason) == ("wait", switch_decide.QUESTION_PENDING)


def test_i2_unforwarded_question_carries_its_id_in_the_forward_reason():
    # I2・受け入れ条件 5: 理由に質問の ID を入れ、直前と同じ理由を省く _note でも別の質問の forward の行を残す
    first = switch_decide.decide(state(question=True, question_id="toolu_a"))
    second = switch_decide.decide(state(question=True, question_id="toolu_b"))
    assert (first.kind, second.kind) == ("forward", "forward")
    assert "toolu_a" in first.reason and "toolu_b" in second.reason


def test_section_state_reads_the_json_without_the_question_fields():
    # 受け入れ条件 9: ndf relay decide の今の入力の JSON（question_forwarded・question_id が無い）も読め、今の文の forward
    data = json.loads('{"question": true, "next_block": null, "child_alive": true, "child_exit": null}')
    got = switch_decide.decide(switch_decide.SectionState(**data))
    assert (got.kind, got.reason) == ("forward", switch_decide.FORWARD_UNKNOWN)


def test_next_block_switches_after_the_quiet_time_even_when_the_goal_is_unmet():
    # /goal が未達で Stop が続き、子が生きていても、静かな時間が過ぎれば切り替える（#2438）
    assert switch_decide.decide(state(next_block="/goal 次", idle_seconds=1.0)).kind == "wait"
    got = switch_decide.decide(state(next_block="/goal 次", idle_seconds=6.0))
    assert (got.kind, got.prompt) == ("switch", "/goal 次")
    assert switch_decide.decide(state(next_block="/goal 次", child_alive=False, child_exit=0, idle_seconds=0.0)).kind == "switch"


def test_context_over_the_limit_asks_for_a_handover_once():
    # 文脈の量が上限を超えたら、hook の外でも判定の機会を作る（#2595）
    over = state(context_tokens=800_000, context_limit=800_000)
    got = switch_decide.decide(over)
    assert got.kind == "handover" and got.prompt == switch_decide.HANDOVER_PROMPT
    assert switch_decide.decide(dataclasses.replace(over, handover_asked=True)).kind == "wait"
    assert switch_decide.decide(dataclasses.replace(over, idle_seconds=1.0)).kind == "wait"
    assert switch_decide.decide(state(context_tokens=799_999, context_limit=800_000)).kind == "wait"


def test_auth_failure_is_reported_as_stalled_not_running():
    # 認証の失敗で /goal が外れたレーンを running のまま扱わない（#3243）
    got = switch_decide.decide(state(auth_failed=True))
    assert got.kind == "stalled"


def test_limit_pick_switches_account_with_resume_prompt():
    pick = Pick("switch", "main", "team が上限に当たった")
    got = switch_decide.decide(state(limit_pick=pick, next_block="/goal x"))
    assert (got.kind, got.account, got.prompt) == ("account", "main", switch_decide.RESUME_PROMPT)
    assert switch_decide.decide(state(limit_pick=Pick("wait_reset", "team", "替える先が無い"))).kind == "wait_reset"


def test_next_block_waits_from_the_first_mark_not_only_from_the_last_output():
    # /goal の未達の応答で画面が静かにならなくても、マーカーを最初に書いてから静かな時間が過ぎれば切り替える（#2438）
    assert switch_decide.decide(state(next_block="/goal 次", idle_seconds=1.0, next_age_seconds=4.0)).kind == "wait"
    assert switch_decide.decide(state(next_block="/goal 次", idle_seconds=1.0, next_age_seconds=5.0)).kind == "switch"


@pytest.mark.parametrize(
    ("changes", "kind"),
    [
        ({"child_alive": False, "child_exit": 0}, "finish"),
        ({"child_alive": False, "child_exit": 1}, "restart"),
        ({"idle_seconds": 1800.0}, "restart"),
        ({}, "wait"),
    ],
)
def test_child_end_and_stall(changes, kind):
    assert switch_decide.decide(state(**changes)).kind == kind


# --- 再起動ループの検出（決定 13・I15） ---

SPIN = switch_decide.Spin(3, 120.0)
LAUNCHES = [
    {"child_alive": False, "child_exit": 1},
    {"idle_seconds": 1800.0},
    {"next_block": "/goal 次", "idle_seconds": 60.0, "next_age_seconds": 60.0},
    {"context_tokens": 800_000, "context_limit": 800_000},
    {"limit_pick": Pick("switch", "main", "team が上限に当たった")},
]


def test_i15_section_state_has_no_start_count_and_decide_never_stops():
    # I15: 判断は起動の回数を入力に持たず、どの起動の手も回数で stop にならない
    fields = {f.name for f in dataclasses.fields(switch_decide.SectionState)}
    assert not fields & {"starts_today", "max_starts"}
    kinds = [switch_decide.decide(state(**changes)).kind for changes in LAUNCHES]
    assert kinds == ["restart", "restart", "switch", "handover", "account"]


@pytest.mark.parametrize("changes", LAUNCHES[:4])
def test_i15_spin_turns_restart_switch_and_handover_into_spin(changes):
    # I15: 検出に当たる（spin）なら、区切り・起動し直し・引継ぎは次のセグメントを起動せず spin を返す
    got = switch_decide.decide(state(spin=True, spin_seen=frozenset({"team"}), **changes))
    assert got.kind == "spin" and got.reason.endswith(switch_decide.SPIN_REASON)


def test_i15_spin_counts_only_an_account_switch_to_a_target_already_launched():
    # 決定 13: 続きの中で初めての起動先への替えは数えず、既に起動した起動先へ戻る替えは spin
    pick = Pick("switch", "main", "team が上限に当たった")
    fresh = switch_decide.decide(state(limit_pick=pick, spin=True, spin_seen=frozenset({"team"})))
    assert (fresh.kind, fresh.account) == ("account", "main")
    back = switch_decide.decide(state(limit_pick=pick, spin=True, spin_seen=frozenset({"team", "main"})))
    assert back.kind == "spin" and back.reason == f"team が上限に当たった。{switch_decide.SPIN_REASON}"


def test_i15_spin_held_turns_even_a_first_time_account_switch_into_spin():
    # 決定 13: 検出に当たった後の保持の間は、初めての起動先への替えも起動しない
    pick = Pick("switch", "main", "team が上限に当たった")
    got = switch_decide.decide(state(limit_pick=pick, spin_held=True, spin_seen=frozenset({"team"})))
    assert got.kind == "spin"
    assert switch_decide.decide(state(spin_held=True)).kind == "wait"  # 起動の手でなければ当てない


@pytest.mark.parametrize("code", [0, 1])
def test_i15_child_ending_while_held_is_spin_before_finish(code):
    # 決定 13: 保持の間に子が終われば、終了コード 0 の finish より先に spin
    got = switch_decide.decide(state(child_alive=False, child_exit=code, spin_held=True))
    assert got.kind == "spin" and got.reason.endswith(switch_decide.SPIN_REASON)
    assert switch_decide.decide(state(child_alive=False, child_exit=0)).kind == "finish"


def test_i15_child_ending_while_held_is_spin_before_the_limit_wait():
    # 決定 13: 保持の間に上限の表示を残した子が終われば、替え先が無くても wait_reset より先に spin
    pick = Pick("wait_reset", "team", "替える先が無い")
    got = switch_decide.decide(state(child_alive=False, child_exit=1, spin_held=True, limit_pick=pick))
    assert got.kind == "spin" and got.reason.startswith("子が終了コード 1 で終わった")
    assert switch_decide.decide(state(child_alive=False, child_exit=1, limit_pick=pick)).kind == "wait_reset"


def _walk(streak, segments, limit=SPIN):
    """（秒, 手, 今の起動先, 次の起動先）を順に advance へ通した続き。"""
    for seconds, kind, current, target in segments:
        streak = switch_decide.advance(streak, seconds, kind, current, target, limit)
    return streak


def test_advance_counts_three_short_restarts_including_the_first_section():
    # 決定 13・I15（検出）: 119 秒のセグメントが 3 つ続くと当たる（1 つ目のセグメントも数える）。120 秒ちょうどは短くない
    start = switch_decide.Streak(0, frozenset({"work1"}))
    got = _walk(start, [(119.0, "restart", "work1", "work1")] * 3)
    assert got == switch_decide.Streak(3, frozenset({"work1"})) and switch_decide.hit(got, SPIN)
    assert not switch_decide.hit(_walk(start, [(119.0, "restart", "work1", "work1")] * 2), SPIN)
    assert _walk(start, [(120.0, "restart", "work1", "work1")]) == switch_decide.Streak(0, frozenset({"work1"}))


def test_advance_starts_over_after_a_long_section():
    # 決定 13・I15（検出）: 2 つの短いセグメントの後に 120 秒以上のセグメントがあれば数え直す
    start = switch_decide.Streak(0, frozenset({"work1"}))
    got = _walk(start, [(119.0, "restart", "work1", "work1"), (121.0, "restart", "work1", "work1"), (119.0, "restart", "work1", "work1")])
    assert got.count == 1 and not switch_decide.hit(got, SPIN)


def test_advance_with_zero_seconds_never_hits():
    # 決定 13: NDF_RELAY_SPIN=0（秒が 0）なら検出しない
    off = switch_decide.Spin(3, 0.0)
    got = _walk(switch_decide.Streak(0, frozenset({"work1"})), [(1.0, "restart", "work1", "work1")] * 5, off)
    assert got.count == 0 and not switch_decide.hit(switch_decide.Streak(9, frozenset()), off)


def test_advance_does_not_count_switches_to_first_time_targets():
    # 決定 13（替えの扱い）: work1→work2→work3→metered と 120 秒未満の替えが 3 つ続いても数は 0 のまま
    start = switch_decide.Streak(0, frozenset({"work1"}))
    got = _walk(start, [(60.0, "account", "work1", "work2"), (60.0, "account", "work2", "work3"), (60.0, "account", "work3", "metered")])
    assert got == switch_decide.Streak(0, frozenset({"work1", "work2", "work3", "metered"})) and not switch_decide.hit(got, SPIN)


def test_advance_counts_switches_back_to_a_target_already_launched():
    # 決定 13（替えの扱い）: work1→work2→work1→work2→work1 の短い替えは戻る替えごとに 1 足し、3 つ目の戻りで当たる
    start = switch_decide.Streak(0, frozenset({"work1"}))
    hops = [("work1", "work2"), ("work2", "work1"), ("work1", "work2"), ("work2", "work1")]
    counts = []
    streak = start
    for current, target in hops:
        streak = switch_decide.advance(streak, 60.0, "account", current, target, SPIN)
        counts.append(streak.count)
    assert counts == [0, 1, 2, 3] and switch_decide.hit(streak, SPIN)


def test_advance_keeps_the_targets_of_the_streak():
    # 決定 13（替えの扱い）: 長いセグメントの後は起動先が今と次の 2 つになり、戻る短い替えを数える
    got = switch_decide.advance(switch_decide.Streak(0, frozenset({"work1"})), 300.0, "account", "work1", "work2", SPIN)
    assert got == switch_decide.Streak(0, frozenset({"work1", "work2"}))
    got = switch_decide.advance(got, 60.0, "account", "work2", "work1", SPIN)
    assert got == switch_decide.Streak(1, frozenset({"work1", "work2"}))
    # 短い区切りで work1 から work2 へ移った後、121 秒の work2 から work3 へ移ると旧い起動先を捨てる
    moved = switch_decide.Streak(1, frozenset({"work1", "work2"}))
    assert switch_decide.advance(moved, 121.0, "account", "work2", "work3", SPIN) == switch_decide.Streak(0, frozenset({"work2", "work3"}))
    # 数を足す分岐でも次の起動先を足す
    assert switch_decide.advance(switch_decide.Streak(0, frozenset({"work1"})), 60.0, "switch", "work1", "work2", SPIN) == (
        switch_decide.Streak(1, frozenset({"work1", "work2"}))
    )
    restarted = switch_decide.advance(switch_decide.Streak(1, frozenset({"work1"})), 60.0, "restart", "work1", "work1", SPIN)
    assert restarted == switch_decide.Streak(2, frozenset({"work1"}))
    switched = switch_decide.advance(restarted, 60.0, "switch", "work1", "work2", SPIN)
    assert switched == switch_decide.Streak(3, frozenset({"work1", "work2"})) and switch_decide.hit(switched, SPIN)


def test_target_key_of_the_default_login_does_not_collide_with_a_registered_name():
    # 決定 13: 既定のログイン（None）の起動先の値は、登録の名前の形と重ならない
    assert switch_decide.target_key(None) == switch_decide.DEFAULT_TARGET
    assert not account_registry.valid_name(switch_decide.target_key(None))
    assert [switch_decide.target_key(n) for n in ("work1", "metered", "key:main")] == ["work1", "metered", "key:main"]


ROWS = [Usage("team", 94.0, False), Usage("main", 20.0, False), Usage("gmail", 50.0, False)]


def test_usage_guard_switches_at_the_cut_point_before_the_limit():
    # 閾値以上なら、閾値より空いたアカウントへ替える（#3336）
    got = account_decide.before_limit("team", ROWS, 90.0)
    assert (got.kind, got.account) == ("switch", "main")
    assert account_decide.before_limit("main", ROWS, 90.0).kind == "keep"
    assert account_decide.before_limit("team", [Usage("team", 94.0, False), Usage("main", 95.0, False)], 90.0).kind == "keep"
    assert account_decide.before_limit("unknown", ROWS, 90.0).kind == "keep"


def test_after_limit_switches_even_with_background_waits_and_resumes_after_reset():
    # 背景の待ちが残っていても替える（#3337）。替える先が無ければリセットを待ち、解けたら同じアカウント
    limited = [Usage("team", 100.0, True), Usage("main", 20.0, False)]
    assert account_decide.after_limit("team", limited) == Pick("switch", "main", account_decide.after_limit("team", limited).reason)
    only = [Usage("team", 100.0, True), Usage("main", 100.0, True)]
    assert account_decide.after_limit("team", only).kind == "wait_reset"
    reset = [Usage("team", 3.0, False)]
    assert account_decide.after_limit("team", reset) == Pick("switch", "team", account_decide.after_limit("team", reset).reason)


def test_restart_judges_limits_before_stopping_and_excludes_the_target():
    # 上限 3 で 3 本が動いているとき、そのうち 1 本の restart は通る（#2753）
    assert hub_decide.restart(hub_decide.Lanes(3, 3, True, 0, 5)).kind == "go"
    # 動いていない 1 本の restart は上限に数える
    assert hub_decide.restart(hub_decide.Lanes(3, 3, False, 0, 5)).kind == "refuse"
    assert hub_decide.restart(hub_decide.Lanes(1, 3, True, 5, 5)).kind == "refuse"
    assert hub_decide.spawn(hub_decide.Lanes(3, 3, False, 0, 5)).kind == "refuse"


def test_started_needs_the_wrapper_location_a_live_process_and_registration():
    ok = hub_decide.started(hub_decide.Started(True, True, True), "t1")
    assert ok.kind == "go"
    missing = hub_decide.started(hub_decide.Started(False, False, False), "t1")
    assert missing.kind == "refuse" and "ndf relay install" in missing.reason  # 何も出さずに失敗しない（#2583）
    assert hub_decide.started(hub_decide.Started(True, False, False), "t1").kind == "refuse"  # #2563
    assert hub_decide.started(hub_decide.Started(True, True, False), "t1").kind == "refuse"


def test_mixed_versions_are_refused():
    # 旧いバージョンのラッパーと混ぜない（3341-2）
    same = [hub_decide.Running("t1", "11.0.0-dev.4")]
    assert hub_decide.mixed("11.0.0-dev.4", same).kind == "go"
    got = hub_decide.mixed("11.0.0-dev.4", [*same, hub_decide.Running("w1", None)])
    assert got.kind == "refuse" and "w1" in got.reason


def test_i6_mixed_refuses_only_old_ndf_and_another_major():
    # I6・受け入れ条件 5: 同じメジャーでバージョンの違うラッパーは断らない
    ok = hub_decide.mixed("11.1.0", [hub_decide.Running("t1", "11.0.0-dev.4"), hub_decide.Running("t2", "11.0.7")])
    assert ok.kind == "go"
    old = hub_decide.mixed("11.0.8", [hub_decide.Running("t1", None)])
    assert old.kind == "refuse" and "t1" in old.reason
    major = hub_decide.mixed("11.0.8", [hub_decide.Running("t1", "11.0.7"), hub_decide.Running("w1", "10.17.51")])
    assert major.kind == "refuse" and "w1" in major.reason and "t1" not in major.reason
