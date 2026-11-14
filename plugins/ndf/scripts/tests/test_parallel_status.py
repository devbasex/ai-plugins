"""レーンの様子（`parallel.py status`）が、終わりの記録のあるレーンを止まったレーンと分けて数えること（#3021 の残り）。"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import parallel_plan as pp  # noqa: E402
import parallel_status as pst  # noqa: E402

PLAN = {
    "version": 1,
    "name": "p1",
    "rules": [],
    "lanes": [
        {"name": n, "input": {"kind": "sprint", "issues": [i]}, "plans": 1, "touches": [f"d{i}/"]}
        for n, i in (("la", 1), ("lb", 2), ("lc", 3))
    ],
}


def test_ended_lanes_are_counted_apart_from_stopped(monkeypatch, tmp_path):
    plan, _, _ = pp.check_data(PLAN)
    states = {"la": {"state": "running"}, "lb": {"state": "stopped"}, "lc": {"state": "stopped"}}
    monkeypatch.setattr(pst, "_ls", lambda main: states)
    monkeypatch.setattr(pst, "_ledger_view", lambda main, name: ({}, set()))
    monkeypatch.setattr(pst, "_launch_waits", lambda main, name: {})
    monkeypatch.setattr(pst, "_ended", lambda main, name: {"lc"})
    rows = pst.lane_rows(tmp_path, "p1", plan)
    assert {r["lane"]: r["ended"] for r in rows} == {"la": False, "lb": False, "lc": True}
    assert pst.status_summary(rows) == "レーン 3 本（動いている 1・承認待ち 0・止まった 1・終わった 1・起動待ち 0）"
