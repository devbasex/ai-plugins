from __future__ import annotations

from pathlib import Path

import pytest

from ndf import identity, slot_pool
from ndf.testing import records, slots, waits
from ndf.testing.limits import Limits

PLENTY = 10**9
POLL = 0.01


def _limits(max_runs: int, wait: int = 60, floor: int = 1) -> Limits:
    return Limits(max_runs=max_runs, memory_floor_mib=floor, wait_limit_seconds=wait)


def _gate(directory: Path, limits: Limits, read_memory=lambda: PLENTY, environ=None):
    return slots.gate(
        "pytest .",
        directory,
        options=slots.GateOptions(
            kind="whole", limits=limits, environ=environ or {}, directory=directory, poll=POLL, read_memory=read_memory
        ),
    )


@pytest.mark.parametrize("environ", [{slots.HELD_ENV: "x"}, {"GITHUB_ACTIONS": "true"}])
def test_nested_and_ci_runs_take_no_slot(tmp_path, environ):
    with _gate(tmp_path, _limits(1)) as outer:
        with _gate(tmp_path, _limits(1, wait=1), environ={**environ, **outer.env}) as inner:
            assert inner.index is None
    assert outer.env == {slots.HELD_ENV: str(tmp_path / "slot-0")}


def test_light_command_takes_no_slot(tmp_path):
    with _gate(tmp_path, _limits(1)):
        with slots.gate(
            "pytest a.py",
            tmp_path,
            options=slots.GateOptions(
                suites=[{"runner": "pytest", "command": "pytest ."}], limits=_limits(1, wait=1), environ={}, directory=tmp_path
            ),
        ) as held:
            assert held.index is None


def test_unmeasurable_memory_starts_at_once(tmp_path):
    with _gate(tmp_path, _limits(1, floor=PLENTY), read_memory=lambda: None) as held:
        assert held.index == 0
    assert records.read_events(tmp_path)[-1]["measured"] is False


def test_unusable_directory_still_runs(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    with slots.gate(
        "pytest .", tmp_path, options=slots.GateOptions(kind="whole", limits=_limits(1), environ={}, directory=blocker / "slots")
    ) as held:
        assert held.index is None and held.note


def test_unwritable_slot_files_release_the_slot_and_run_without_it(tmp_path, monkeypatch):
    real_write = slot_pool.write_json

    def refuse(path, data):
        raise PermissionError(13, "書けない", str(path))

    monkeypatch.setattr(slot_pool, "write_json", refuse)
    with _gate(tmp_path, _limits(1)) as held:
        assert held.index is None and held.env == {}
        assert held.note.startswith("スロットの保存先へ書けないのでスロットを取らずに走らせた: ")
    monkeypatch.setattr(slot_pool, "write_json", real_write)
    with waits.returns_before(10):
        with _gate(tmp_path, _limits(1, wait=10)) as held:
            assert held.index == 0  # 取りかけたスロットは返してある


def test_preexec_is_none_when_running_without_a_slot():
    assert slots.Held().preexec is None


@pytest.mark.parametrize("in_child", [False, True])
def test_interrupt_after_retiring_carries_the_retired_locations(tmp_path, monkeypatch, in_child):
    # 旧い状態の保存先を退避した後の中断（待ちの間・子の実行中）は、退避の元と先を持つ Interrupted で抜ける
    retired = [{"retired_from": "/s/p", "retired_to": "/s/p.v10-20261114"}]
    monkeypatch.setattr(slots, "slots_location", lambda env: (tmp_path / "slots", retired))
    options = slots.GateOptions(
        kind="whole", limits=_limits(1), environ={}, poll=POLL, read_memory=lambda: PLENTY, interrupted=lambda: not in_child
    )

    with pytest.raises(slots.Interrupted) as caught:
        with slots.gate("pytest .", tmp_path, options=options):
            raise KeyboardInterrupt

    assert caught.value.retired == retired


def test_gate_keeps_the_locations_retired_before_preparing_failed(tmp_path, monkeypatch):
    # 退避の途中で失敗してスロットを取らずに走らせるときも、先に退避した元と先を Held に残す
    retired = (identity.Retired(Path("/s/p"), Path("/s/p.v10-20261114")),)

    def fail(env):
        raise identity.IdentityError("read-only", retired)

    monkeypatch.setattr(slots, "slots_location", fail)
    options = slots.GateOptions(kind="whole", limits=_limits(1), environ={}, poll=POLL, read_memory=lambda: PLENTY)

    with slots.gate("pytest .", tmp_path, options=options) as held:
        pass

    assert held.index is None
    assert held.retired == [{"retired_from": "/s/p", "retired_to": "/s/p.v10-20261114"}]
