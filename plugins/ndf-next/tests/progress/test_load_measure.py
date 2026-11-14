"""進行表の DB の負荷の測定（load_measure.py）の現状固定。短い時間・少ないプロセスで打ち、出す 1 行の JSON の形を固定する。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("load_measure.py")
KEYS = [
    "at",
    "processes",
    "seconds",
    "interval",
    "check_every",
    "writes",
    "database_is_locked",
    "other_errors",
    "other_samples",
    "wait_p95_seconds",
    "wait_max_seconds",
    "quick_check_count",
    "quick_check_seconds_max",
    "backups",
]


def _measure(tmp_path: Path, *extra: str) -> dict:
    env = {**os.environ, "TMPDIR": str(tmp_path)}
    env.pop("NDF_PROGRESS_DB", None)
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--processes", "2", "--seconds", "1.5", "--interval", "0.05", *extra],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    lines = done.stdout.strip().splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def _assert_report(report: dict) -> None:
    assert list(report) == KEYS
    assert (report["processes"], report["seconds"], report["interval"]) == (2, 1.5, 0.05)
    assert report["other_errors"] == 0 and report["other_samples"] == []
    assert isinstance(report["writes"], int) and isinstance(report["database_is_locked"], int)
    assert isinstance(report["quick_check_count"], int) and isinstance(report["backups"], int)
    if report["writes"]:
        assert 0 <= report["wait_p95_seconds"] <= report["wait_max_seconds"]
    else:
        assert report["wait_p95_seconds"] is None and report["wait_max_seconds"] is None
    if report["quick_check_count"]:
        assert report["quick_check_seconds_max"] >= 0
    else:
        assert report["quick_check_seconds_max"] is None


def test_measure_prints_one_line_report_and_keeps_the_work_under_tmpdir(tmp_path):
    report = _measure(tmp_path)
    _assert_report(report)
    assert report["check_every"] == 0
    assert report["writes"] > 0
    work = [p for p in tmp_path.iterdir() if p.name.startswith("ndf-progress-load-")]
    assert len(work) == 1
    assert sorted(p.name for p in work[0].glob("w*.json")) == ["w0.json", "w1.json"]


def test_measure_with_check_every_reports_the_interval(tmp_path):
    report = _measure(tmp_path, "--check-every", "0.1")
    _assert_report(report)
    assert report["check_every"] == 0.1


@pytest.mark.parametrize("bad", [["--processes", "x"], ["--unknown"]])
def test_measure_rejects_bad_arguments_as_a_usage_error(tmp_path, bad):
    done = subprocess.run([sys.executable, str(SCRIPT), *bad], capture_output=True, text=True, timeout=60)
    assert done.returncode == 2 and done.stdout == ""


def _load_measure():
    spec = importlib.util.spec_from_file_location("load_measure", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collect_report_sums_the_worker_results(tmp_path):
    """子プロセスの結果のファイルから集計する値を固定する（p95 は sorted[int(0.95 * (件数 - 1))]）。"""
    work, root = tmp_path, tmp_path / "state"
    (root / "backup").mkdir(parents=True)
    for name in ("progress-1.sqlite", "progress-2.sqlite", "note.txt"):
        (root / "backup" / name).write_text("")
    rows = [
        {"waits": [0.3, 0.1], "busy": 1, "other": ["a", "b", "c"], "other_count": 4},
        {"waits": [0.2, 0.5, 0.4], "busy": 2, "other": ["d", "e", "f"], "other_count": 3},
    ]
    for i, row in enumerate(rows):
        (work / f"w{i}.json").write_text(json.dumps(row))
    (work / "w0.checks").write_text("0.25\n0.5\n")
    options = argparse.Namespace(processes=2, seconds=1.5, interval=0.05, check_every=0.1)

    report = _load_measure()._collect_report(work, root, options)

    assert list(report) == KEYS
    assert {k: v for k, v in report.items() if k != "at"} == {
        "processes": 2,
        "seconds": 1.5,
        "interval": 0.05,
        "check_every": 0.1,
        "writes": 5,
        "database_is_locked": 3,
        "other_errors": 7,
        "other_samples": ["a", "b", "c", "d", "e"],
        "wait_p95_seconds": 0.4,
        "wait_max_seconds": 0.5,
        "quick_check_count": 2,
        "quick_check_seconds_max": 0.5,
        "backups": 2,
    }
