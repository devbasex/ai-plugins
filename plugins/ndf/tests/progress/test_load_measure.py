"""ステートマシンの DB の負荷の測定（load_measure.py）の集計の現状固定。子プロセスの結果のファイルから出す 1 行の JSON の形を固定する。"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

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
