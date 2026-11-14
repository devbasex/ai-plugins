"""LLM と外部 CLI の起動が重い起動の枠を取り、空かなければ待って止まること（#3368 の 3368-2 の AD3+ の分）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import slot_pool
from ndf.adapters import agents, claude, launch_slot
from ndf.testing import slots
from ndf.testing import limits as test_limits

PLENTY_MIB = 10**9


@pytest.fixture
def pool(tmp_path, monkeypatch) -> Path:
    """枠の置き場と、テスト 1 本分の容量の設定。待ちは短くする。"""
    monkeypatch.delenv(slots.HELD_ENV, raising=False)
    directory = tmp_path / "slots"
    monkeypatch.setenv(slots.SLOTS_DIR_ENV, str(directory))
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / test_limits.RESOURCES_FILE).write_text(
        json.dumps({test_limits.SECTION: {"max_runs": 1, "memory_floor_mib": 1024}}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launch_slot, "wait_policy", lambda: (0.3, 0.05))
    return directory


def fill(directory: Path) -> slot_pool.Taken:
    directory.mkdir(parents=True, exist_ok=True)
    taken = slot_pool.take(directory, PLENTY_MIB, test_limits.test_weight_mib(), {"pid": 0})
    assert taken is not None
    return taken


def test_holds_llm_cli_weight_and_gives_it_back(pool):
    with launch_slot.held("claude", read_memory=lambda: PLENTY_MIB):
        assert slot_pool.held_mib(pool) == slot_pool.weight_mib(launch_slot.KIND)
    assert slot_pool.held_mib(pool) == 0


def test_gives_back_on_error(pool):
    with pytest.raises(RuntimeError), launch_slot.held("codex", read_memory=lambda: PLENTY_MIB):
        raise RuntimeError("boom")
    assert slot_pool.held_mib(pool) == 0


def test_full_pool_waits_then_stops_with_reason(pool):
    taken = fill(pool)
    try:
        with pytest.raises(launch_slot.NoSlot, match="codex"), launch_slot.held("codex", read_memory=lambda: PLENTY_MIB):
            pytest.fail("枠が空いていないのに起動した")
    finally:
        taken.release()


def test_memory_below_floor_waits_then_stops(pool):
    with pytest.raises(launch_slot.NoSlot, match="512 MiB"), launch_slot.held("claude", read_memory=lambda: 512):
        pytest.fail("メモリが下限を割っているのに起動した")
    assert slot_pool.held_mib(pool) == 0


def test_inside_a_held_slot_takes_nothing(pool, monkeypatch):
    taken = fill(pool)
    monkeypatch.setenv(slots.HELD_ENV, str(pool / "slot-0"))
    try:
        with launch_slot.held("claude", read_memory=lambda: 0):
            assert slot_pool.held_mib(pool) == test_limits.test_weight_mib()
    finally:
        taken.release()


def test_broken_slot_setting_stops(pool, tmp_path):
    (tmp_path / ".ndf" / test_limits.RESOURCES_FILE).write_text("{", encoding="utf-8")

    with pytest.raises(launch_slot.NoSlot, match="用意できない"), launch_slot.held("claude"):
        pytest.fail("設定が読めないのに起動した")


def test_call_claude_and_run_agent_stop_without_slot(pool, marker_launch, monkeypatch):
    monkeypatch.setattr(launch_slot.machine, "available_mib", lambda: PLENTY_MIB)
    marker, argv, spec = marker_launch("kiro")
    taken = fill(pool)
    try:
        reply = claude.call_claude(argv, "q", 30)
        status = agents.run_agent(spec, poll=0.05)
    finally:
        taken.release()

    assert reply.failure is claude.Failure.NO_SLOT
    assert status.state == agents.AgentState.NO_SLOT
    assert not marker.exists()
