"""lib/run_environment.py: 実行環境の調査（#2505 の決定 9・10、不変条件 I7・I8）。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import procs  # noqa: E402
import run_environment as re_  # noqa: E402


@pytest.mark.parametrize(
    "cores, gib, lanes, want",
    [
        (8, 30, 1, {"max_issues": 6, "max_plans": 6, "max_named": 3}),
        (8, 30, 3, {"max_issues": 2, "max_plans": 6, "max_named": 3}),
        (16, 30, 1, {"max_issues": 6, "max_plans": 6, "max_named": 3}),  # メモリが頭打ち
        (2, 30, 1, {"max_issues": 1, "max_plans": 1, "max_named": 1}),  # CPU が頭打ち
        (1, 1, 4, {"max_issues": 1, "max_plans": 1, "max_named": 1}),  # 下限は 1
        (32, 120, 2, {"max_issues": 12, "max_plans": 24, "max_named": 12}),
    ],
)
def test_candidates_scale_from_the_measured_point(cores, gib, lanes, want):
    assert re_.limit_candidates(cores, gib, lanes) == want


def write_cgroup(tmp: Path, cpu: str, memory: str) -> Path:
    (tmp / "cpu.max").write_text(cpu + "\n")
    (tmp / "memory.max").write_text(memory + "\n")
    return tmp


def test_cgroup_limits_narrow_the_machine_values(tmp_path, monkeypatch):
    monkeypatch.setattr(re_.os, "sched_getaffinity", lambda pid: set(range(8)), raising=False)
    monkeypatch.setattr(procs, "memory_available", lambda: (0, 30 * 2**30))
    d = write_cgroup(tmp_path, "200000 100000", str(4 * 2**30))
    assert re_.cpus(d) == 2.0 and re_.memory_gib(d) == 4.0


def test_an_unlimited_or_missing_cgroup_keeps_the_machine_values(tmp_path, monkeypatch):
    monkeypatch.setattr(re_.os, "sched_getaffinity", lambda pid: set(range(8)), raising=False)
    monkeypatch.setattr(procs, "memory_available", lambda: (0, 30 * 2**30))
    d = write_cgroup(tmp_path, "max 100000", "max")
    assert re_.cpus(d) == 8.0 and re_.memory_gib(d) == 30.0
    missing = tmp_path / "none"
    assert re_.cpus(missing) == 8.0 and re_.memory_gib(missing) == 30.0


def test_place_tells_factory_from_container_and_host(tmp_path, monkeypatch):
    cg = tmp_path / "cgroup"
    cg.write_text("0::/\n")
    none = tmp_path / "no-dockerenv"
    monkeypatch.delenv("NDF_FACTORY_RUN", raising=False)
    assert re_.run_place(cg, none) == "host"
    cg.write_text("0::/kubepods/abc\n")
    assert re_.run_place(cg, none) == "container"
    (tmp_path / "dockerenv").write_text("")
    cg.write_text("0::/\n")
    assert re_.run_place(cg, tmp_path / "dockerenv") == "container"
    monkeypatch.setenv("NDF_FACTORY_RUN", "1")
    assert re_.run_place(cg, none) == "factory"


@pytest.fixture
def machine(monkeypatch):
    monkeypatch.setattr(re_, "cpus", lambda: 8.0)
    monkeypatch.setattr(re_, "memory_gib", lambda: 30.0)
    monkeypatch.setattr(re_, "running_plan_count", lambda: 2)
    monkeypatch.delenv("NDF_PARALLEL_MAX_PLANS", raising=False)
    monkeypatch.delenv("NDF_RELAY_MAX_NAMED", raising=False)


def declare(root: Path, **loop) -> Path:
    (root / ".ndf").mkdir(exist_ok=True)
    p = root / ".ndf" / "pace.json"
    p.write_text(json.dumps({"version": 1, "loop": loop}))
    return p


def test_survey_reports_the_items_and_the_reason(tmp_path, machine):
    declare(tmp_path)
    r = re_.survey(tmp_path)
    assert (r["cpus"], r["memory_gib"], r["lanes"], r["plans"]) == (8.0, 30.0, 1, 2)
    assert r["candidates"] == {"max_issues": 6, "max_plans": 6, "max_named": 3}
    assert r["current"] == {"max_issues": 3, "max_plans": 6, "max_named": 3}
    assert "承認で読める量は別に見る" in r["reason"] and r["place"] and r["unreadable"] == []
    assert re_.differs(r) and len(re_.notes(r)) == 1


def test_the_lanes_of_a_plan_being_started_divide_the_issue_candidate(tmp_path, machine):
    declare(tmp_path)
    assert re_.survey(tmp_path, lanes=3)["candidates"]["max_issues"] == 2


def test_no_line_when_candidates_match_the_current_values(tmp_path, machine):
    declare(tmp_path, max_issues=6)
    r = re_.survey(tmp_path)
    assert not re_.differs(r) and re_.notes(r) == []


def test_unreadable_values_keep_the_current_ones(tmp_path, machine, monkeypatch):
    def broken():
        raise OSError("読めない")

    declare(tmp_path)
    monkeypatch.setattr(re_, "cpus", broken)
    monkeypatch.setattr(re_, "run_place", broken)
    r = re_.survey(tmp_path)
    assert sorted(r["unreadable"]) == ["cpus", "place"] and r["candidates"] == r["current"]


def test_survey_does_not_touch_the_declaration(tmp_path, machine):
    p = declare(tmp_path)
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    re_.survey(tmp_path)
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before


def test_running_lanes_counts_only_running_plans(tmp_path, monkeypatch):
    import parallel_lane as pl
    import parallel_plan as pp

    assert re_.running_lanes(tmp_path) == 1
    for name in "abc":
        d = tmp_path / ".ndf" / "parallel" / name
        d.mkdir(parents=True)
        (d / "plan.json").write_text("{}")
    states = {"a": "PENDING", "b": "SUCCESS", "c": "PENDING"}
    monkeypatch.setattr(pl, "run_status", lambda main, name: states[name])
    monkeypatch.setattr(pp, "load_valid_plan", lambda main, name: type("Plan", (), {"lanes": [0] * {"a": 2, "c": 4}[name]}))
    assert re_.running_lanes(tmp_path) == 6
    states.update(a="SUCCESS", c="SUCCESS")
    assert re_.running_lanes(tmp_path) == 1
