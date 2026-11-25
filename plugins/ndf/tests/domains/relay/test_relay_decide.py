"""中継の判断（純粋な関数）: セグメントの切り替え・アカウント・リレーハブの上限とバージョンの混ざり。端末と時計を使わない（#2962）。"""

from __future__ import annotations

import dataclasses
import json

import pytest

from ndf.contracts import limits
from ndf.domains.relay import account_decide, account_registry, cli, hub_decide, switch_decide
from ndf.domains.relay.account_decide import Pick, Pool, Usage
from ndf.domains.relay.switch_decide import Background

AT = "2026-11-22T03:19:34+00:00"
IDLE, WATCHING = Background(0, "stop", AT, 0), Background(0, "stop", AT, 1)  # 作業が無い・監視だけが残る
WORKING, STARTED = Background(2, "stop", AT, 1), Background(1, "start", AT, 0)  # 作業が残る・作業を始めた
# 動いていてバックグラウンドの作業が無いと確かめられた子
BASE = switch_decide.SectionState(
    question=False,
    next_block=None,
    child_alive=True,
    child_exit=None,
    idle_seconds=10.0,
    quiet_seconds=5.0,
    stall_seconds=1800.0,
    background=IDLE,
)


def state(**changes):
    return dataclasses.replace(BASE, **changes)


def test_question_is_forwarded_before_anything_else():
    got = switch_decide.decide(state(question=True, next_block="/goal x", auth_pick=Pick("switch", "work2", "認証の失敗")))
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
    assert switch_decide.decide(dataclasses.replace(over, handover_asked=True)).kind == "active"
    assert switch_decide.decide(dataclasses.replace(over, idle_seconds=1.0)).kind == "active"
    assert switch_decide.decide(state(context_tokens=799_999, context_limit=800_000)).kind == "active"


@pytest.mark.parametrize(
    ("idle", "stall", "kind"),
    [(1799.9, 1800.0, "active"), (1800.0, 1800.0, "restart"), (9999.0, 0.0, "active")],
    ids=["just-before", "reached", "off"],
)
def test_the_stall_restarts_only_once_the_stall_seconds_are_reached(idle, stall, kind):
    # #4049 の受け入れ条件 15（変えない振る舞い）: 止まりの秒の手前では起動し直さず、届いたら起動し直す。0 秒なら止まりを見ない
    assert switch_decide.decide(state(idle_seconds=idle, stall_seconds=stall)).kind == kind


def test_the_limit_switch_resumes_with_the_limit_prompt_even_when_the_stall_and_the_context_are_reached():
    # #4049 の受け入れ条件 15（変えない振る舞い）: 上限に達した後の替え（守り 3）は引継ぎ・止まりより先で、最初の入力は今の文のまま
    over = state(limit_pick=Pick("switch", "main", "上限"), idle_seconds=1800.0, context_tokens=800_000, context_limit=800_000)
    got = switch_decide.decide(over)
    assert (got.kind, got.prompt, got.account) == ("account", switch_decide.RESUME_PROMPT, "main")


def test_handover_prompt_asks_to_keep_the_wait_line_and_check_the_document():
    # #3995 の 3995:7: 求める文は wait の行を残すことと ndf records handoff check を含み、背景の作業の終わりについて書かない
    prompt = switch_decide.HANDOVER_PROMPT
    assert "ndf lanes wait" in prompt and "ndf records --cwd <メインディレクトリの絶対パス> handoff check" in prompt
    assert "背景" not in prompt


def _settings_limit(tmp_path, monkeypatch, env):
    """ラッパーの設定（wrapper_settings）が判断へ渡す運用上限。`env` は NDF_CONTEXT_LIMIT（None なら無し）。"""
    monkeypatch.chdir(tmp_path)
    if env is None:
        monkeypatch.delenv("NDF_CONTEXT_LIMIT", raising=False)
    else:
        monkeypatch.setenv("NDF_CONTEXT_LIMIT", env)
    return cli.wrapper_settings("w1", []).context_limit


@pytest.mark.parametrize(
    ("env", "tokens", "kind"),
    [(None, 600_000, "handover"), (None, 599_999, "active"), ("500000", 500_000, "handover"), ("500000", 499_999, "active")],
)
def test_wrapper_settings_hand_the_operating_limit_to_the_handover(tmp_path, monkeypatch, env, tokens, kind):
    # #3995 の 3995:7・3995:9: ラッパーは運用上限（600,000。NDF_CONTEXT_LIMIT で替える）で静かな子に引継ぎを頼む
    limit = _settings_limit(tmp_path, monkeypatch, env)
    assert switch_decide.decide(state(context_tokens=tokens, context_limit=limit)).kind == kind


def test_wrapper_settings_read_the_limit_from_the_limits_table(tmp_path, monkeypatch):
    # #3995 の 3995:1: 上限の設定の 2 つの値を差し替えると、ラッパーの判断は差し替えた運用上限を使う
    real = limits.value
    swapped = {"context.decide-from": 100_000, "context.limit": 200_000}
    monkeypatch.setattr(limits, "value", lambda name, key: swapped[name] if name in swapped else real(name, key))
    limit = _settings_limit(tmp_path, monkeypatch, None)
    assert switch_decide.decide(state(context_tokens=200_000, context_limit=limit)).kind == "handover"
    assert switch_decide.decide(state(context_tokens=199_999, context_limit=limit)).kind == "active"


def test_3864_7_an_auth_failure_without_a_target_is_stalled_while_the_child_is_alive():
    # 受け入れ条件 #3864 の 7: 替え先が無ければ子を残す（stalled）。子が終わっていれば今の終わりの判断へ
    pick = Pick("stalled", "work1", "work1 の認証に失敗し、替える先が無い")
    assert switch_decide.decide(state(auth_pick=pick)).kind == "stalled"
    assert switch_decide.decide(state(auth_pick=pick, child_alive=False, child_exit=0)).kind == "finish"


def test_3864_6_an_auth_failure_resumes_on_the_target_with_the_auth_prompt():
    # 受け入れ条件 #3864 の 6: 認証の失敗の switch は account の手で、同じ会話を AUTH_RESUME_PROMPT で続ける
    got = switch_decide.decide(state(auth_pick=Pick("switch", "work2", "work1 の認証に失敗した"), next_block="/goal x"))
    assert got == switch_decide.Step("account", "work1 の認証に失敗した", switch_decide.AUTH_RESUME_PROMPT, "work2")
    assert switch_decide.AUTH_RESUME_PROMPT == "認証の失敗でアカウントを替えた。中断したところから続ける"


def test_3864_6_the_limit_comes_before_an_auth_failure():
    # 上限の行と認証の失敗が同時なら上限が先（上限の switch も wait_reset も）
    auth = Pick("switch", "work2", "認証")
    got = switch_decide.decide(state(limit_pick=Pick("switch", "main", "上限"), auth_pick=auth))
    assert (got.kind, got.prompt, got.account) == ("account", switch_decide.RESUME_PROMPT, "main")
    assert switch_decide.decide(state(limit_pick=Pick("wait_reset", "work1", "待つ"), auth_pick=auth)).kind == "wait_reset"


def test_3864_6_an_auth_switch_to_a_seen_target_in_a_spin_is_spin():
    # 再起動ループの検出: 続きの中で既に起動した替え先なら spin、初めての替え先なら account
    auth = Pick("switch", "work2", "認証")
    assert switch_decide.decide(state(auth_pick=auth, spin=True, spin_seen=frozenset({"work2"}))).kind == "spin"
    assert switch_decide.decide(state(auth_pick=auth, spin=True, spin_seen=frozenset({"work1"}))).kind == "account"


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
        ({}, "active"),
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
    assert switch_decide.decide(state(spin_held=True)).kind == "active"  # 起動の手でなければ当てない


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


GUARD = Pick("switch", "work2", "work1 の 5 時間 86% が閾値 A 85% 以上。work2（余裕の組） へ替える")


def test_d11_guard_switch_at_a_safe_point_resumes_with_the_guard_prompt():
    # 決定 11・I12: 見回りの判断が switch で止めてよい時点なら、替え先で同じ会話を続ける手（account）と GUARD_RESUME_PROMPT。
    # 替えで終わる監視の張り直しを続けて伝える（#4049 の受け入れ条件 12）
    got = switch_decide.decide(state(guard_pick=GUARD, safe=True))
    prompt = f"{switch_decide.GUARD_RESUME_PROMPT}。{switch_decide.MONITOR_RESUME}"
    assert got == switch_decide.Step("account", GUARD.reason, prompt, "work2", busy="zero")


def test_i13_guard_switch_waits_until_the_safe_point():
    # I13: 止めてよい時点の外では替えのために子を止めず、動いている子として扱う
    got = switch_decide.decide(state(guard_pick=GUARD, safe=False))
    assert got == switch_decide.Step("active", switch_decide.GUARD_POINT)


@pytest.mark.parametrize(
    "changes, kind",
    [
        ({"idle_seconds": 1800.0}, "restart"),  # tool_use のままハングした子は出力の止まりで再起動する
        ({"context_tokens": 800_000, "context_limit": 800_000}, "handover"),  # 文脈の上限の引継ぎも待たない
    ],
)
def test_r1_8_waiting_for_the_safe_point_does_not_hold_the_stall_restart_or_the_handover(changes, kind):
    # cross-review の r1-8: 止めてよい時点を待つ間も、出力の止まりの再起動と文脈の上限の引継ぎは働く。作業が残ればどちらも待つ（#4049）
    assert switch_decide.decide(state(guard_pick=GUARD, safe=False, **changes)).kind == kind
    assert switch_decide.decide(state(guard_pick=GUARD, safe=False, background=WORKING, **changes)).kind == "wait_background"


@pytest.mark.parametrize(
    "changes, kind",
    [
        ({"next_block": "/goal 次", "idle_seconds": 60.0}, "switch"),  # 次のセグメントのマーカーはカットポイントの判断が先
        ({"question": True, "question_id": "toolu_1"}, "forward"),  # 質問は転送が先
        ({"guard_pick": Pick("keep", "work1", "替えない")}, "active"),  # keep なら見回りの手は無い（子が動いている）
        ({"limit_pick": Pick("wait_reset", "work1", "上限")}, "wait_reset"),  # 上限の判断が先
        ({"spin": True, "spin_seen": frozenset({"work1", "work2"})}, "spin"),  # 続きの中で起動した起動先への替えは spin
    ],
)
def test_i13_guard_comes_after_the_marker_question_and_limit_and_is_cut_by_the_spin(changes, kind):
    got = switch_decide.decide(state(**{"guard_pick": GUARD, "safe": True, **changes}))
    assert got.kind == kind
    if kind == "active":
        assert got.reason == switch_decide.ACTIVE


G = account_decide.Guards(85.0, 90.0)  # 閾値 A・C（`contracts/limits/` の既定）
R = 1_800_000_000.0  # 7 日の窓のリセットの時刻の基準
GONE = Usage("gone", None, None, None, None, True)  # 使用率は分からないが上限
choose = account_decide.choose


def use(name, five=None, seven=None, resets=None, limited=False):
    percent = max((v for v in (five, seven) if v is not None), default=0.0)
    return Usage(name, percent, five, seven, resets, limited)


def test_4404_inside_reads_the_five_hour_and_weekly_windows_against_a_and_c():
    assert account_decide.inside(None, G)  # 使用率の分からないものは内側
    assert account_decide.inside(use("a", 84, 89), G)
    assert not account_decide.inside(use("a", 85, 10), G)
    assert not account_decide.inside(use("a", 10, 90), G)
    assert not account_decide.inside(use("a", 10, 10, limited=True), G)


def test_4404_ranked_puts_inside_then_explicit_then_headroom_and_drops_limited():
    rows = [use("busy", 10, 79), use("calm", 5, 32), use("over", 86, 10), use("full", 10, 100, limited=True), GONE]
    names = ["busy", "calm", "over", "full", "gone", "unknown"]
    assert account_decide.ranked(None, rows, names, G) == ["calm", "busy", "unknown", "over"]
    assert account_decide.ranked(None, rows, names, G, explicit=("", "busy")) == ["busy", "calm", "unknown", "over"]
    assert account_decide.ranked(None, rows, names, G, explicit=("over",)) == ["calm", "busy", "unknown", "over"]  # 外側の明示は内側より後
    assert account_decide.ranked("calm", rows, names, G) == ["busy", "unknown", "over"]
    assert account_decide.ranked(None, rows, [], G) == []  # 候補の外の行を返さない


def test_4527_start_picks_the_longest_and_skips_a_limited_pin():
    rows = [use("main", None, 79), use("gmail", None, 32), use("team", None, 100, limited=True)]
    got = choose("start", None, rows, Pool(["gmail", "main", "team"]), G)
    assert (got.kind, got.account, got.order) == ("switch", "gmail", ("gmail", "main"))
    assert choose("start", None, rows, Pool(["gmail", "main", "team"], explicit=("main",)), G).order == ("main", "gmail")
    assert choose("start", None, rows, Pool(["gmail", "main", "team"], explicit=("team",)), G).account == "gmail"
    none = choose("start", None, rows, Pool(["team"]), G)
    assert (none.kind, none.account, none.order) == ("keep", None, ())  # 既定のログイン


def test_4404_running_keeps_inside_and_moves_only_when_it_cannot_continue():
    rows = [use("main", None, 79), use("gmail", None, 32)]
    keep = choose("running", "main", rows, Pool(["main", "gmail"]), G)
    assert (keep.kind, keep.account, keep.order) == ("keep", "main", ("main", "gmail"))  # 区切りで替えない
    over = [use("main", None, 91), use("gmail", None, 32)]
    assert choose("running", "main", over, Pool(["main", "gmail"]), G).account == "gmail"
    both = [use("main", None, 91), use("gmail", 86, 32)]
    assert choose("running", "main", both, Pool(["main", "gmail"]), G).kind == "keep"  # 内側の候補が無ければ使い切る
    assert choose("running", None, rows, Pool(["gmail"]), G).kind == "keep"
    assert choose("running", "metered", over, Pool(["main", "gmail"]), G).account == "gmail"
    assert choose("running", "metered", [use("main", None, 91)], Pool(["main"]), G).account == "main"  # 従量より上限でない登録へ戻る
    assert choose("running", "key:k1", [], Pool([]), G).kind == "keep"


def test_4404_limit_keeps_an_account_the_api_does_not_report_limited():
    freed = [use("work1", None, 40), use("work2", None, 10)]
    got = choose("limit", "work1", freed, Pool(["work1", "work2"]), G)
    assert (got.kind, got.account, got.order) == ("switch", "work1", ("work1",))
    assert choose("limit", "work1", [], Pool(["work1"]), G).account == "work1"  # 記録の無いものは解けたとして扱う


def test_4404_limit_moves_to_the_longest_then_fallbacks_then_waits():
    limited = use("work1", None, 100, limited=True)
    rows = [limited, use("work2", 86, 10), use("work3", None, 50)]
    got = choose("limit", "work1", rows, Pool(["work1", "work2", "work3"], ["metered"]), G)
    assert got.order == ("work3", "work2", "metered")
    assert choose("limit", "work1", [limited], Pool(["work1"], ["metered", "key:k1"]), G).order == ("metered", "key:k1")
    assert choose("limit", "metered", [], Pool([], ["metered", "key:k1"]), G).account == "key:k1"
    assert choose("limit", "work1", [limited], Pool(["work1"]), G).kind == "wait_reset"
    assert choose("limit", None, [use("work2", None, 10)], Pool(["work2"]), G).account == "work2"  # 既定のログインは画面の行で上限


def test_after_auth_picks_the_order_then_fallbacks_and_stalls_without_any():
    rows = [use("work1", 10, 10), use("work2", 10, 10)]
    assert choose("auth", "work1", rows, Pool(["work1", "work2"]), G).account == "work2"
    assert choose("auth", None, rows, Pool(["work2"]), G).account == "work2"  # 既定のログインでも替える
    assert choose("auth", "metered", [], Pool(["work1"], ["metered"]), G).account == "work1"
    assert choose("auth", "key:k1", [], Pool([], ["key:k1", "key:k2"]), G).account == "key:k2"
    assert choose("auth", "work1", [], Pool(["work1"]), G).kind == "stalled"
    assert choose("auth", "metered", [], Pool([], ["metered"]), G).kind == "stalled"


def test_after_auth_names_the_default_login_in_every_reason():
    picks = [
        choose("auth", None, [use("work2", 10, 10)], Pool(["work2"]), G),
        choose("auth", None, [], Pool([], ["metered"]), G),
        choose("auth", None, [], Pool([]), G),
    ]
    assert [(p.kind, p.account) for p in picks] == [("switch", "work2"), ("switch", "metered"), ("stalled", None)]
    assert all(p.reason.startswith("既定のログイン の認証に失敗") and "None" not in p.reason for p in picks)


def test_choose_refuses_an_unknown_scene():
    with pytest.raises(ValueError):
        choose("cut", None, [], Pool([]), G)


def test_restart_judges_limits_before_stopping_and_excludes_the_target():
    # 上限 3 で 3 本が動いているとき、そのうち 1 本の restart は通る（#2753）
    assert hub_decide.restart(hub_decide.Lanes(3, 3, True)).kind == "go"
    # 動いていない 1 本の restart は上限に数える
    assert hub_decide.restart(hub_decide.Lanes(3, 3, False)).kind == "refuse"
    assert hub_decide.spawn(hub_decide.Lanes(3, 3, False)).kind == "refuse"


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


SWITCH_K2 = Pick("switch", "work2", "metered の認証に失敗した。work2 で再開する")


@pytest.mark.parametrize(
    "changes, kind",
    [
        # 止められる時点の前は、上限・認証の失敗・次のセグメントのマーカー・質問があっても wait
        (dict(end_ready=False, limit_pick=SWITCH_K2, auth_pick=SWITCH_K2, next_block="/goal x", question=True), "wait"),
        (dict(end_ready=False, limit_pick=Pick("wait_reset", None, "上限")), "wait"),
        # 止めた見回りで替え先があれば account（AUTH_RESUME_PROMPT）、再起動ループに当たれば spin、無ければ revoked
        (dict(end_ready=True, drift_pick=SWITCH_K2), "account"),
        (dict(end_ready=True, drift_pick=SWITCH_K2, spin=True, spin_seen=frozenset({"work2"})), "spin"),
        (dict(end_ready=True, drift_pick=None, auth_pick=Pick("stalled", "metered", "替え先が無い")), "revoked"),
        (dict(end_ready=True, drift_pick=None, child_alive=False, child_exit=1), "revoked"),
    ],
    ids=["wait-before-end", "no-wait-reset", "account", "spin", "revoked-not-stalled", "revoked-not-restart"],
)
def test_3864_9_d12_revoked_comes_first_and_never_stalls(changes, kind):
    # 受け入れ条件 #3864 の 9・決定 12: must_end の子は _revoked が先で、stalled・restart・wait_reset を返さない
    got = switch_decide.decide(state(must_end=True, **changes))
    assert got.kind == kind
    if kind == "account":
        assert got.account == "work2" and got.prompt == switch_decide.AUTH_RESUME_PROMPT


def test_d12_revoked_is_none_without_must_end():
    assert switch_decide._revoked(state(end_ready=True, drift_pick=SWITCH_K2)) is None


# --- バックグラウンドの作業が残る子を止めずに待つ（#4049） ---

OVER = {"context_tokens": 800_000, "context_limit": 800_000}
STALL = {"idle_seconds": 1800.0}
AT_GUARD = {"guard_pick": GUARD, "safe": True}


@pytest.mark.parametrize(
    "changes, found, kind, reason",
    [
        (OVER, IDLE, "handover", "800000"),
        (OVER, WATCHING, "handover", "800000"),
        (OVER, WORKING, "wait_background", f"作業 2 件が終わるのを待つ（ほかに監視 1 件。{AT} の記録）"),
        (OVER, STARTED, "wait_background", f"作業 1 件以上が終わるのを待つ（{AT} に作業の開始を記録）"),
        (OVER, None, "wait_background", switch_decide.BACKGROUND_UNKNOWN),
        (STALL, IDLE, "restart", "1800 秒止まっている"),
        (STALL, WATCHING, "wait_background", f"監視 1 件が終わるのを待つ（{AT} の記録）"),
        (STALL, WORKING, "wait_background", "作業 2 件"),
        (STALL, None, "restart", "1800 秒止まっている"),
        ({**OVER, **STALL}, IDLE, "handover", "800000"),
        ({**OVER, **STALL}, WATCHING, "handover", "800000"),
        ({**OVER, **STALL}, STARTED, "wait_background", "1 件以上"),
        ({**OVER, **STALL}, None, "restart", "1800 秒止まっている"),
        ({**OVER, "handover_asked": True}, WORKING, "active", switch_decide.ACTIVE),
        ({**OVER, "context_tokens": 799_999}, WORKING, "active", switch_decide.ACTIVE),
        ({**OVER, "idle_seconds": 4.9}, WORKING, "active", switch_decide.ACTIVE),
        (AT_GUARD, WATCHING, "account", GUARD.reason),
        (AT_GUARD, WORKING, "wait_background", "作業 2 件"),
        (AT_GUARD, STARTED, "wait_background", "1 件以上"),
        (AT_GUARD, None, "wait_background", switch_decide.BACKGROUND_UNKNOWN),
        ({**AT_GUARD, **STALL}, IDLE, "account", GUARD.reason),
        ({**AT_GUARD, **STALL}, WORKING, "wait_background", "作業 2 件"),
        ({**AT_GUARD, **STALL}, None, "restart", "1800 秒止まっている"),
        ({**AT_GUARD, "limit_pick": Pick("switch", "main", "上限")}, WORKING, "account", "上限"),
        ({"spin": True, **STALL}, IDLE, "spin", "1800 秒止まっている"),
        ({"spin": True, **STALL}, WORKING, "wait_background", "作業 2 件"),
        ({"child_alive": False, "child_exit": 1, **STALL}, WORKING, "restart", "終了コード 1"),
    ],
)
def test_4049_1_2_12_a_child_with_works_left_is_waited_for(changes, found, kind, reason):
    # 受け入れ条件 1・2・10・11・12: 作業が残れば引継ぎ・起動し直し・見回りの替えを待ち、監視だけなら起動し直しだけを待つ
    got = switch_decide.decide(state(background=found, **changes))
    assert got.kind == kind and reason in got.reason


@pytest.mark.parametrize(
    "changes, found, busy",
    [
        (OVER, IDLE, "zero"),
        (STALL, IDLE, "none_running"),
        (STALL, None, "none_running"),
        (AT_GUARD, IDLE, "zero"),
        ({**AT_GUARD, "limit_pick": Pick("switch", "main", "上限")}, IDLE, None),
        ({"auth_pick": Pick("switch", "work2", "認証")}, IDLE, None),
        ({"next_block": "/goal 次"}, IDLE, None),
        ({"child_alive": False, "child_exit": 1}, IDLE, None),
    ],
)
def test_4049_3_the_steps_that_depend_on_the_count_ask_for_a_recheck_before_stopping(changes, found, busy):
    assert switch_decide.decide(state(background=found, **changes)).busy == busy


def test_4049_12_going_back_from_metered_also_tells_to_rearm_the_watches():
    back = switch_decide.decide(state(metered=True, **AT_GUARD))
    assert back.prompt == f"{switch_decide.GUARD_BACK_PROMPT}。{switch_decide.MONITOR_RESUME}"


ALL_AT_ONCE = {**OVER, **STALL, **AT_GUARD}


@pytest.mark.parametrize(
    "changes, needed",
    [
        (OVER, True),
        (STALL, True),
        ({"spin": True, **STALL}, True),
        (AT_GUARD, True),
        ({**OVER, "context_tokens": 799_999}, False),
        ({**OVER, "idle_seconds": 4.9}, False),
        ({**OVER, "handover_asked": True}, False),
        ({"idle_seconds": 1799.9}, False),
        ({"idle_seconds": 9999.0, "stall_seconds": 0.0}, False),
        ({**AT_GUARD, "safe": False}, False),
        ({**ALL_AT_ONCE, "limit_pick": Pick("switch", "main", "上限")}, False),
        ({**ALL_AT_ONCE, "limit_pick": Pick("wait_reset", "work1", "上限")}, False),
        ({**ALL_AT_ONCE, "auth_pick": Pick("switch", "work2", "認証")}, False),
        ({**ALL_AT_ONCE, "question": True}, False),
        ({**ALL_AT_ONCE, "next_block": "/goal 次"}, False),
        ({**ALL_AT_ONCE, "must_end": True}, False),
        ({"child_alive": False, "child_exit": 1}, False),
    ],
)
def test_4049_10_the_record_is_needed_only_where_the_chosen_step_depends_on_it(changes, needed):
    assert switch_decide.needs_background(state(**changes)) is needed
