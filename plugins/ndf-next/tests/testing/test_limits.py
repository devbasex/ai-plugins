from __future__ import annotations

import json

import pytest

from ndf.testing import limits

GIB = 1024


@pytest.mark.parametrize(
    ("cpus", "total", "runs"),
    [(8, 30 * GIB, 2), (4, 30 * GIB, 1), (8, 15 * GIB, 1), (1, 1 * GIB, 1), (64, 256 * GIB, 16), (8, None, 2)],
)
def test_default_runs_follow_cpu_and_memory(cpus, total, runs):
    assert limits.default_limits(cpus, total).max_runs == runs


def test_default_floor_is_ten_percent_with_a_minimum():
    assert limits.default_limits(8, 30 * GIB).memory_floor_mib == 3 * GIB
    assert limits.default_limits(8, 8 * GIB).memory_floor_mib == 2 * GIB


def _write(tmp_path, data):
    (tmp_path / ".ndf").mkdir(exist_ok=True)
    (tmp_path / ".ndf" / "resources.json").write_text(json.dumps(data))


def test_settings_override_defaults_from_a_parent_directory(tmp_path):
    _write(tmp_path, {"test_slots": {"max_runs": 3, "wait_limit_seconds": 5}})
    sub = tmp_path / "worktree" / "deep"
    sub.mkdir(parents=True)
    got = limits.load_limits(sub, cpus=8, total_mib=30 * GIB)
    assert got == limits.Limits(3, 3 * GIB, 5)


@pytest.mark.parametrize("value", [0, -1, "2", 1.5, True])
def test_wrong_value_is_refused_with_the_place_to_fix(tmp_path, value):
    _write(tmp_path, {"test_slots": {"memory_floor_mib": value}})
    with pytest.raises(limits.SlotConfigError) as err:
        limits.load_limits(tmp_path, cpus=8, total_mib=30 * GIB)
    assert "memory_floor_mib" in err.value.items[0]


def test_broken_json_is_refused(tmp_path):
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "resources.json").write_text("{")
    with pytest.raises(limits.SlotConfigError):
        limits.load_limits(tmp_path, cpus=8, total_mib=30 * GIB)


def test_load_suites_skips_lint(tmp_path):
    (tmp_path / ".ndf").mkdir()
    suites = [{"runner": "pytest", "command": "pytest ."}, {"runner": "ruff", "kind": "lint", "command": "ruff ."}]
    (tmp_path / ".ndf" / "project.json").write_text(json.dumps({"test": {"suites": suites}}))
    assert [s["runner"] for s in limits.load_suites(tmp_path)] == ["pytest"]
