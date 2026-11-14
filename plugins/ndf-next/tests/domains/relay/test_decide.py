"""区間の切り替えとリレーハブの起動の判断（#2962・#2438・#2563・#2583・#2753）。時計と端末を立てずに確かめる。"""

from __future__ import annotations

import pytest

from ndf.domains.relay import hub_decide, switch_decide
from ndf.domains.relay.switch_decide import SectionState


def _state(**kw) -> SectionState:
    base = {"next_block": None, "question": False, "child_alive": True, "child_exit": None, "starts_today": 0, "max_starts": 3}
    return SectionState(**{**base, **kw})


@pytest.mark.parametrize(
    ("kw", "kind"),
    [
        ({"question": True, "next_block": "x"}, "forward"),
        ({"next_block": "/goal 次"}, "switch"),
        ({"next_block": "/goal 次", "child_alive": False, "child_exit": 1}, "switch"),
        ({}, "wait"),
        ({"idle_seconds": 100, "stall_seconds": 60}, "restart"),
        ({"idle_seconds": 100, "stall_seconds": 60, "starts_today": 3}, "stop"),
        ({"child_alive": False, "child_exit": 0}, "finish"),
        ({"child_alive": False, "child_exit": 137}, "restart"),
        ({"child_alive": False, "child_exit": 137, "starts_today": 3}, "stop"),
    ],
)
def test_section_step(kw, kind):
    assert switch_decide.decide(_state(**kw)).kind == kind


def test_switch_carries_the_block_as_the_next_prompt():
    step = switch_decide.decide(_state(next_block="/goal 次の区間"))
    assert step.prompt == "/goal 次の区間"


def test_same_state_gives_the_same_step():
    state = _state(idle_seconds=10, stall_seconds=60)
    assert switch_decide.decide(state) == switch_decide.decide(state)


def _lanes(**kw) -> hub_decide.Lanes:
    base = {"running": 3, "max_named": 3, "target_running": True, "starts_today": 0, "max_starts": 5}
    return hub_decide.Lanes(**{**base, **kw})


def test_restart_judges_the_limit_without_the_target_before_stopping():
    # 対象を数えずに判定する（上限ちょうどでも対象の起こし直しは通る。#2753）
    assert hub_decide.restart(_lanes()).kind == "go"
    assert hub_decide.restart(_lanes(running=4)).kind == "refuse"
    assert hub_decide.restart(_lanes(target_running=False)).kind == "refuse"
    refused = hub_decide.restart(_lanes(starts_today=5))
    assert refused.kind == "refuse" and "止めない" in refused.reason


def test_spawn_respects_the_limit():
    assert hub_decide.spawn(_lanes(running=2)).kind == "go"
    assert hub_decide.spawn(_lanes()).kind == "refuse"


@pytest.mark.parametrize(
    ("facts", "kind", "word"),
    [
        (hub_decide.Started(True, True, True), "go", "起動した"),
        (hub_decide.Started(False, False, False), "refuse", "ndf relay install"),
        (hub_decide.Started(True, False, False), "refuse", "立ち上がらなかった"),
        (hub_decide.Started(True, True, False), "refuse", "一覧に載らない"),
    ],
)
def test_started_is_reported_only_when_the_wrapper_is_up(facts, kind, word):
    got = hub_decide.started(facts, "t1")
    assert got.kind == kind and word in got.reason
