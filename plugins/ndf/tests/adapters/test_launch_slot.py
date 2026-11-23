"""LLM と外部 CLI の起動が重い起動のスロットを取り、返すこと（#3368 の 3368-2 の AD3+ の分）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import slot_pool
from ndf.adapters import launch_slot
from ndf.testing import slots
from ndf.testing import limits as test_limits

PLENTY_MIB = 10**9


@pytest.fixture
def pool(tmp_path, monkeypatch) -> Path:
    """スロットの保存先と、テスト 1 本分の容量の設定。待ちは短くする。"""
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


class FakeClock:
    """眠った分だけ進む偽の時計（実時間を経たせない）。`slept` に眠った秒を順に残す。"""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


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


def test_test_slot_under_a_cli_counts_the_cli_weight_as_its_own(pool, tmp_path):
    lim = test_limits.load_limits(tmp_path)
    options = dict(kind="whole", limits=lim, directory=pool, poll=0.01, read_memory=lambda: PLENTY_MIB)
    with launch_slot.held("claude", read_memory=lambda: PLENTY_MIB) as slot_env:
        assert slot_pool.PARENT_ENV in slot_env
        with slots.gate("pytest .", tmp_path, options=slots.GateOptions(environ=slot_env, **options)) as held:
            assert held.index is not None
        stranger = slots.GateOptions(environ={}, **{**options, "limits": test_limits.Limits(1, 1, 0)})
        with pytest.raises(slots.Stopped), slots.gate("pytest .", tmp_path, options=stranger):
            pytest.fail("CLI の外のテストが CLI のスロットを数えずに取った")


def test_gate_rechecks_the_cache_after_taking_the_slot(pool, monkeypatch):
    """スロットを待つ間にバックアップが拒否へ変われば、スロットを返して起動しない。"""
    answers = iter([None, "学習に使う設定へ変わった"])
    monkeypatch.setattr(launch_slot.training_cache, "refusal", lambda agent, env=None: next(answers))
    monkeypatch.setattr(launch_slot.machine, "available_mib", lambda: PLENTY_MIB)
    with pytest.raises(launch_slot.NotLaunched, match="学習に使う"), launch_slot.gate("codex"):
        pytest.fail("拒否へ変わった後に起動した")
    assert slot_pool.held_mib(pool) == 0


# ---------------------------------------------------------------- #4000 の決定 8: 待つ上限を呼び手が決める


def test_issue_4000_waits_the_configured_limit_without_a_limit(pool, monkeypatch):
    """#4000: 上限を省けば今までどおり設定の上限（`launch.wait.llm-cli`）まで、設定の間隔で待つ。"""
    taken = fill(pool)
    clock = FakeClock()
    monkeypatch.setattr(launch_slot, "time", clock)
    lim = test_limits.load_limits(Path.cwd())
    try:
        with pytest.raises(launch_slot.NoSlot, match="空かなかった"):
            launch_slot._wait_for("claude", pool, lim, lambda: PLENTY_MIB)
    finally:
        taken.release()

    assert clock.slept and set(clock.slept) == {0.05} and clock.now >= 0.3


def test_issue_4000_a_zero_limit_gives_up_without_sleeping(pool, monkeypatch):
    """#4000: 上限 0 なら 1 度だけ取りに行き、取れなければ眠らずに `NoSlot`。"""
    taken = fill(pool)
    clock = FakeClock()
    monkeypatch.setattr(launch_slot, "time", clock)
    lim = test_limits.load_limits(Path.cwd())
    try:
        with pytest.raises(launch_slot.NoSlot, match="空かなかった"):
            launch_slot._wait_for("claude", pool, lim, lambda: PLENTY_MIB, limit=0)
    finally:
        taken.release()

    assert clock.slept == []


def test_issue_4000_a_zero_limit_still_takes_a_free_slot(pool, monkeypatch):
    """#4000: 上限 0 でも空きがあればその場で取る。"""
    clock = FakeClock()
    monkeypatch.setattr(launch_slot, "time", clock)
    lim = test_limits.load_limits(Path.cwd())

    taken = launch_slot._wait_for("claude", pool, lim, lambda: PLENTY_MIB, limit=0)

    assert slot_pool.held_mib(pool) == slot_pool.weight_mib(launch_slot.KIND) and clock.slept == []
    taken.release()


def test_issue_4000_held_passes_the_wait_down(pool, monkeypatch):
    """#4000: `held` の `wait` が待つ上限として届く（空きが無ければ眠らずに `NoSlot`）。"""
    taken = fill(pool)
    clock = FakeClock()
    monkeypatch.setattr(launch_slot, "time", clock)
    try:
        with pytest.raises(launch_slot.NoSlot), launch_slot.held("claude", read_memory=lambda: PLENTY_MIB, wait=0):
            pytest.fail("空きが無いのにスロットを持った")
    finally:
        taken.release()

    assert clock.slept == []
