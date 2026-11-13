from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ndf.testing import slots

PLUGIN_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def project(tmp_path):
    """宣言の runner が `fakerun` のプロジェクト。`fakerun` は始まりと終わりを記録へ足して少し眠る。"""
    root = tmp_path / "project"
    (root / ".ndf").mkdir(parents=True)
    suites = [{"name": "fake", "runner": "fakerun", "command": "fakerun ."}]
    (root / ".ndf" / "project.json").write_text(json.dumps({"test": {"suites": suites}}))
    (root / ".ndf" / "resources.json").write_text(json.dumps({"test_slots": {"max_runs": 1, "memory_floor_mib": 1, "wait_limit_seconds": 120}}))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "fakerun"
    fake.write_text('#!/bin/sh\necho "start $(date +%s.%N)" >> "$LOG"\nsleep 0.3\necho "end $(date +%s.%N)" >> "$LOG"\necho out; echo err >&2\nexit "${FAKE_EXIT:-0}"\n')
    fake.chmod(0o755)
    return root, bin_dir, tmp_path / "log"


def _ndf(args, cwd, bin_dir=None, log=None, **extra):
    env = {**os.environ, "PYTHONPATH": str(PLUGIN_ROOT / "src"), **extra}
    if bin_dir:
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
    if log:
        env["LOG"] = str(log)
    return subprocess.Popen([sys.executable, "-m", "ndf", "test-slot", *args], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_run_keeps_output_and_exit_code(project):
    root, bin_dir, log = project
    for code in ("0", "1"):
        proc = _ndf(["run", "--", "fakerun ."], root, bin_dir, log, FAKE_EXIT=code)
        out, err = proc.communicate()
        assert (proc.returncode, out, err) == (int(code), "out\n", "err\n")
    assert [row["result"] for row in slots.read_events(Path(os.environ[slots.SLOTS_DIR_ENV]))] == ["finished", "finished"]


def test_parallel_runs_wait_for_each_other(project):
    root, bin_dir, log = project
    procs = [_ndf(["run", "--", "fakerun ."], root, bin_dir, log) for _ in range(3)]
    assert [p.wait() for p in procs] == [0, 0, 0]
    marks = sorted((float(t), kind) for kind, t in (line.split() for line in log.read_text().splitlines()))
    running = peak = 0
    for _, kind in marks:
        running += 1 if kind == "start" else -1
        peak = max(peak, running)
    assert peak == 1


def test_light_command_runs_without_a_slot(project):
    root, bin_dir, log = project
    proc = _ndf(["run", "--", "fakerun one_file.py"], root, bin_dir, log)
    assert proc.wait() == 0
    assert slots.read_events(Path(os.environ[slots.SLOTS_DIR_ENV])) == []


def test_wrong_setting_refuses_to_run(project):
    root, bin_dir, log = project
    (root / ".ndf" / "resources.json").write_text(json.dumps({"test_slots": {"max_runs": 0}}))
    proc = _ndf(["run", "--", "fakerun ."], root, bin_dir, log)
    _, err = proc.communicate()
    assert proc.returncode == 2
    assert "max_runs" in json.loads(err)["items"][0]
    assert not log.exists()


def test_report_reads_the_records(tmp_path):
    directory = Path(os.environ[slots.SLOTS_DIR_ENV])
    directory.mkdir(parents=True)
    slots.append_event(directory, {"at": slots._now_iso(time.time()), "waited_seconds": 3, "result": "gave_up"})
    proc = _ndf(["report", "--since", "1d"], tmp_path)
    out, _ = proc.communicate()
    assert proc.returncode == 0
    metrics = json.loads(out)["metrics"]
    assert (metrics["gave_up"], metrics["waited_runs"]) == (1, 1)


def test_report_refuses_a_wrong_period(tmp_path):
    proc = _ndf(["report", "--since", "seven"], tmp_path)
    proc.communicate()
    assert proc.returncode == 2
