"""止まりの知らせの中身（#4036 の受け入れ条件 4）。判定の材料は値で渡し、時刻はテストが決める。"""

from __future__ import annotations

from ndf.progress.lane import liveness
from ndf.progress.lane.types import LaneRow
from ndf.progress.plan import run, run_decide
from ndf.progress.plan.run_decide import LANE_STALLED, RunState, Stall

ALIVE = {"name": "w-a", "running": True, "state": "running", "reason": None}
GONE = {"name": "w-b", "running": False, "state": "stopped", "reason": None}
IDLE_WAIT = {"tool": "progress", "status": "ok", "summary": "次に流すのは工程 実装"}
ROWS = {"a": [LaneRow("r1", {}, "running", sprint_id="s1")], "b": [LaneRow("r1", {}, "running", sprint_id="s2")]}
MOVED = {"a": "2026-11-23T00:00:00+00:00", "b": "2026-11-23T00:01:00+00:00"}


def _found(activity):
    return liveness.Probes(
        wrapper_name=lambda name: f"w-{name}",
        wrappers=lambda: {"w-a": ALIVE, "w-b": GONE},
        sprint=lambda _sid: IDLE_WAIT,
        activity=lambda name: activity if name == "a" else None,
    )


def test_an_idle_conductor_stall_carries_its_moved_time_and_no_background():
    quiet = liveness.Activity(turn_done=True, moved=1000.0, background=0)
    got = run._judge(ROWS, run_decide.RUNNING, _found(quiet), MOVED)
    by_lane = {s[0]: s for s in got.stalls}
    assert by_lane["a"][1].endswith(liveness.IDLE_CONDUCTOR) and by_lane["a"][3:] == (1000.0, "r1", 0)
    # 活動を見ずに決まる止まり（ended）は今までどおりの記録の時刻と None
    assert by_lane["b"][3:] == (MOVED["b"], "r1", None)


def test_a_stall_without_a_recorded_time_has_none():
    got = run._judge({"b": ROWS["b"]}, run_decide.RUNNING, _found(None), {})
    assert got.stalls[0][3] is None and got.stalls[0][5] is None


def test_a_unix_moved_time_becomes_iso_in_the_lane_stalled_request():
    report = run_decide._stall_report(RunState(), Stall("a", "stall:a/r1:idle_conductor", "理由", 0.0, "r1", 0))
    facts = dict(report.facts)
    assert report.kind == LANE_STALLED
    assert facts["moved"] == "1970-01-01T00:00:00+00:00" and facts["background"] == 0
    assert "1970-01-01T00:00:00+00:00" in report.detail


def test_an_iso_moved_time_and_an_unknown_background_pass_through():
    facts = dict(run_decide._stall_report(RunState(), Stall("b", "k", "理由", MOVED["b"], "r1")).facts)
    assert facts["moved"] == MOVED["b"] and facts["background"] is None


def test_a_five_item_stall_from_an_older_snapshot_still_reads():
    old = Stall(*["b", "k", "理由", MOVED["b"], "r1"])
    assert old.background is None
    report = run_decide._stall_report(RunState(), old)
    assert dict(report.facts)["background"] is None
