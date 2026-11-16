"""中継の判断（純粋な関数）: 区間の切り替え・アカウント・リレーハブの上限と版の混ざり。端末と時計を使わない（#2962）。"""

from __future__ import annotations

import dataclasses

import pytest

from ndf.domains.relay import account_decide, hub_decide, switch_decide
from ndf.domains.relay.account_decide import Pick, Usage

BASE = switch_decide.SectionState(
    question=False,
    next_block=None,
    child_alive=True,
    child_exit=None,
    starts_today=0,
    max_starts=3,
    idle_seconds=10.0,
    quiet_seconds=5.0,
    stall_seconds=1800.0,
)


def state(**changes):
    return dataclasses.replace(BASE, **changes)


def test_question_is_forwarded_before_anything_else():
    got = switch_decide.decide(state(question=True, next_block="/goal x", auth_failed=True))
    assert got.kind == "forward"


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
    # 替えて起こし直すのも今日の起動の上限に数える
    assert switch_decide.decide(state(limit_pick=pick, starts_today=3)).kind == "stop"


def test_next_block_waits_from_the_first_mark_not_only_from_the_last_output():
    # /goal の未達の応答で画面が静かにならなくても、印を最初に書いてから静かな時間が過ぎれば切り替える（#2438）
    assert switch_decide.decide(state(next_block="/goal 次", idle_seconds=1.0, next_age_seconds=4.0)).kind == "wait"
    assert switch_decide.decide(state(next_block="/goal 次", idle_seconds=1.0, next_age_seconds=5.0)).kind == "switch"


@pytest.mark.parametrize(
    ("changes", "kind"),
    [
        ({"child_alive": False, "child_exit": 0}, "finish"),
        ({"child_alive": False, "child_exit": 1}, "restart"),
        ({"child_alive": False, "child_exit": 1, "starts_today": 3}, "stop"),
        ({"idle_seconds": 1800.0}, "restart"),
        ({"idle_seconds": 1800.0, "starts_today": 3}, "stop"),
        ({}, "wait"),
    ],
)
def test_child_end_and_stall(changes, kind):
    assert switch_decide.decide(state(**changes)).kind == kind


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


def test_started_needs_the_wrapper_place_a_live_process_and_registration():
    ok = hub_decide.started(hub_decide.Started(True, True, True), "t1")
    assert ok.kind == "go"
    missing = hub_decide.started(hub_decide.Started(False, False, False), "t1")
    assert missing.kind == "refuse" and "ndf relay install" in missing.reason  # 何も出さずに失敗しない（#2583）
    assert hub_decide.started(hub_decide.Started(True, False, False), "t1").kind == "refuse"  # #2563
    assert hub_decide.started(hub_decide.Started(True, True, False), "t1").kind == "refuse"


def test_mixed_versions_are_refused():
    # 旧い版のラッパーと混ぜない（3341-2）
    same = [hub_decide.Running("t1", "11.0.0-dev.4")]
    assert hub_decide.mixed("11.0.0-dev.4", same).kind == "go"
    got = hub_decide.mixed("11.0.0-dev.4", [*same, hub_decide.Running("w1", None)])
    assert got.kind == "refuse" and "w1" in got.reason
