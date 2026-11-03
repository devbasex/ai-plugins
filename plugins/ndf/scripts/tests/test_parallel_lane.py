"""レーンの判定（#2436 の I12）と、レーンの `queue --max` の既定（プランの本数）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import parallel_lane as pl  # noqa: E402
import parallel_plan as pp  # noqa: E402

PLAN = {
    "version": 1,
    "name": "p1",
    "rules": [],
    "lanes": [{"name": "la", "input": {"kind": "sprint", "issues": [1]}, "plans": 2, "touches": ["a/"]}],
}


def _main(tmp_path: Path) -> Path:
    d = pp.plan_dir(tmp_path, "p1")
    d.mkdir(parents=True)
    (d / "plan.json").write_text(json.dumps(PLAN))
    return tmp_path


def test_lane_only_while_plan_is_running(tmp_path):
    main = _main(tmp_path)
    running = lambda _m, _n: "PENDING"  # noqa: E731
    assert pl.lane_of(main, "la", status=running)[1].plans == 2
    assert pl.lane_of(main, "lb", status=running) is None
    assert pl.lane_of(main, "la", status=lambda _m, _n: "SUCCESS") is None
    assert pl.lane_of(main, "la", status=lambda _m, _n: None) is None


def test_queue_max_default(tmp_path, monkeypatch):
    import supervise

    main = _main(tmp_path)
    monkeypatch.setattr(pl, "lane_plans", lambda m, lane=None: 2 if Path(m) == main else None)
    assert supervise.default_max(str(main)) == 2
    assert supervise.default_max(str(tmp_path / "other")) == 3


def test_non_lane_sprint_keeps_prod_plan(monkeypatch):
    from supervise_lib import sprint

    monkeypatch.setattr(sprint, "parallel_lane", lambda repo: None)
    assert list(sprint.prod_plans(None, "/r", lambda: {"x": 1})) == ["release-prod"]
    monkeypatch.setattr(sprint, "parallel_lane", lambda repo: ("p1", "la"))
    import parallel_release

    monkeypatch.setattr(
        parallel_release,
        "plan_release_request",
        lambda a, repo, plan, lane, prs=None: {"フェーズ": "配布の依頼", "plan": plan, "lane": lane},
    )
    got = sprint.prod_plans(None, "/r", lambda: {"x": 1})
    assert got == {"release-request": {"フェーズ": "配布の依頼", "plan": "p1", "lane": "la"}}


def test_release_request_plan_has_one_gate_step():
    import argparse

    import supervise_lib  # noqa: F401  lib/ と supervise_lib を読めるようにする
    import parallel_release

    plan = parallel_release.plan_release_request(argparse.Namespace(issue=[2436], mode="standard"), "/r", "p1", "la", ["{queue_pr:check}"])
    assert plan["フェーズ"] == "配布の依頼" and [s["id"] for s in plan["steps"]] == ["parallel-request"]
    assert "request p1 --lane la --prs {queue_pr:check} --issues 2436" in plan["steps"][0]["cmd"]


def test_present_gate_puts_approve_after_mvv(tmp_path):
    import supervise_lib  # noqa: F401
    import parallel_release

    steps = parallel_release.present_gate_steps(str(tmp_path / "a.json"), "/r", "issues/x.md", "1 2", "standard", "/f", "/a")
    ids = [s["id"] for s in steps]
    assert ids[:3] == ["mvv", "note", "approve"] and steps[1]["next"] == "approve" and steps[2]["next"] == "end"
