"""レーンの側の答え待ちの停止（#2707 の受け入れ条件 12〜14）: ループは周回の境、1 スプリントのレーンはキューのプランの開始で止まる。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import parallel_hold as ph  # noqa: E402
import test_sprint_loop as tsl  # noqa: E402
from test_sprint_loop import rec, sl, sl_record, sl_select, start  # noqa: E402
from test_supervise_queue import PY, SUPERVISE, Queue, run_step  # noqa: E402

env, mvv = tsl.env, tsl.mvv
HOLD = {"plan": True, "lanes": [], "count": 9, "by_lane": {}, "limits": {"plan": 8, "lane": 3}}


def plan_dir(root: Path, kind: str) -> Path:
    d = root / ".ndf" / "parallel" / "p1"
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.json").write_text(json.dumps({"lanes": [{"name": "la", "input": {"kind": kind}}]}))
    return d


@pytest.fixture
def lane(env, monkeypatch):
    d = plan_dir(env.root, "loop")
    monkeypatch.setenv("NDF_RELAY_NAME", "la")
    env.hold = d / ph.HOLD_FILE
    start(env)
    return env


def test_a_loop_lane_stops_at_the_boundary_with_a_mark_and_the_stop_line(lane):
    lane.hold.write_text(json.dumps(HOLD))
    out = sl_select.cmd_select(lane, "l1")
    assert out["exit"] == 3 and out["metrics"]["stop_line"] == "[ndf-loop] stopped l1 answer_hold"
    assert out["metrics"]["stop"]["values"] == {"plan": "p1", "scope": "plan", "count": 9, "limit": 8}
    assert "stop" in out["metrics"]["stop_self"]
    assert rec(lane)["stop"]["condition"] == "answer_hold"
    assert ph.read_marks(lane.root, "p1")["la"]["why"] == "boundary"


def test_the_hold_stops_before_the_pending_laps_are_handled():
    """保留の周回があっても waiting_only へ置き換えず、保留の周回の処理より先に止まる。"""
    rec_ = {"laps": [{"n": 1, "outcome": sl_record.RELEASE_PENDING}]}
    stop = {"condition": "answer_hold", "values": {}}
    assert sl_select.held_stop(rec_, stop) is stop and stop["condition"] in sl_select.IMMEDIATE_STOPS


def test_a_loop_lane_does_not_stop_when_the_hold_cleared_before_the_boundary(lane):
    lane.hold.write_text(json.dumps(HOLD))
    lane.hold.unlink()
    assert sl_select.cmd_select(lane, "l1").get("exit") != 3
    assert ph.read_marks(lane.root, "p1") == {}


def test_a_broken_hold_file_does_not_stop_a_loop_lane(lane):
    lane.hold.write_text("{壊れた")
    assert sl_select.cmd_select(lane, "l1").get("exit") != 3


def test_a_resumed_loop_stops_again_while_covered_and_resumes_when_cleared(lane):
    lane.hold.write_text(json.dumps(HOLD))
    first = sl_select.cmd_select(lane, "l1")["metrics"]["stop"]["at"]
    again = sl.cmd_start(lane, "l1", [], {})
    assert again["exit"] == 3 and again["metrics"]["stop"]["condition"] == "answer_hold" and first
    lane.hold.unlink()
    assert sl.cmd_start(lane, "l1", [], {})["metrics"]["status"] == "resumed"


def test_a_process_that_is_not_the_lane_does_not_read_the_hold(lane, monkeypatch):
    lane.hold.write_text(json.dumps(HOLD))
    monkeypatch.setenv("NDF_RELAY_NAME", "other")
    assert sl_select.cmd_select(lane, "l1").get("exit") != 3


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_SV_STATE_DIR", str(tmp_path / "sv"))
    monkeypatch.setenv("NDF_RELAY_NAME", "la")
    return Queue(tmp_path, os.environ.copy())


def results(res: dict) -> dict:
    return {Path(i["plan"]).stem: i["result"] for i in res["items"]}


def hold(q: Queue, kind="sprint") -> Path:
    path = plan_dir(q.tmp, kind) / ph.HOLD_FILE
    path.write_text(json.dumps(HOLD))
    return path


def test_a_plan_of_a_sprint_lane_is_not_run_and_the_queue_returns_the_park_line(queue):
    hold(queue)
    plan = queue.plan("a", [run_step(f"touch {queue.tmp}/ran")])
    _, res = queue.run(plan)
    item = res["items"][0]
    assert res["status"] == "gate" and item["result"] == "流さなかった" and item["answer_hold"] is True
    assert res["metrics"]["answer_hold"] == {"plan": "p1", "lane": "la"}
    assert "park p1 --lane la --done" in res["next"] and not (queue.tmp / "ran").exists()


def test_the_running_plan_finishes_and_the_next_plan_is_not_run_and_resume_flows_after_the_hold(queue):
    path = plan_dir(queue.tmp, "sprint") / ph.HOLD_FILE
    (queue.tmp / "hold.json").write_text(json.dumps(HOLD))
    first = queue.plan("a", [run_step(f"cp {queue.tmp}/hold.json {path}")], files=["a/"])
    second = queue.plan("b", [run_step(f"touch {queue.tmp}/ran")], files=["b/"])
    _, res = queue.run("--max", "1", first, "--then", second)
    assert results(res) == {"a": "完了", "b": "流さなかった"} and res["status"] == "gate"
    path.unlink()
    argv = [PY, str(SUPERVISE), "queue", "--resume", str(queue.tmp / "done.json"), "--poll", "0.1"]
    got = subprocess.run(argv, capture_output=True, text=True, timeout=180, env=queue.env, cwd=queue.tmp)
    res = json.loads(got.stdout.splitlines()[-1])
    assert results(res) == {"a": "完了", "b": "完了"} and res["status"] == "ok" and (queue.tmp / "ran").exists()


def test_a_loop_lane_plan_and_a_process_outside_the_lanes_run(queue):
    hold(queue, "loop")
    plan = queue.plan("a", [run_step("true")])
    assert queue.run(plan)[1]["status"] == "ok"


def test_a_single_run_is_not_held(queue):
    hold(queue)
    plan = queue.plan("a", [run_step(f"touch {queue.tmp}/ran")])
    got = subprocess.run([PY, str(SUPERVISE), "run", plan], capture_output=True, text=True, timeout=120, env=queue.env, cwd=queue.tmp)
    assert got.returncode == 0, got.stderr[-1000:]
    assert (queue.tmp / "ran").exists()


def held_item() -> dict:
    return {"plan": "b", "result": "流さなかった", "answer_hold": True, "reason": f"{ph.SKIP_REASON}: p1 la"}


@pytest.mark.parametrize(
    "other, status",
    [({"plan": "a", "result": "止まった"}, "stopped"), ({"plan": "a", "result": "関門"}, "gate")],
)
def test_the_park_line_is_kept_with_a_stopped_plan_or_a_gate(other, status, tmp_path):
    from supervise_lib import queue as q

    res = q._queue_result([other, held_item()], 1, tmp_path / "done.json")
    park = res["metrics"]["park"]
    assert res["status"] == status and "park p1 --lane la --done" in park and park in res["next"]
    assert res["next"].startswith("承認ゲート") == (status == "gate")


def test_the_queue_flag_is_on_from_before_the_launch_until_after_the_close(queue, monkeypatch):
    import durable
    from supervise_lib import flow
    from supervise_lib import queue as q

    seen = []
    monkeypatch.setattr(flow, "launch_queue", lambda *a: seen.append(("launch", flow.Engine.queued)) or "p")
    monkeypatch.setattr(q, "_drive_queue", lambda *a: {"status": "ok"})
    monkeypatch.setattr(durable, "close", lambda: seen.append(("close", flow.Engine.queued)))
    plan = queue.plan("a", [run_step("true")])
    assert q.cmd_queue([plan], 1, done=str(queue.tmp / "done.json")) == {"status": "ok"}
    assert seen == [("launch", True), ("close", True)] and flow.Engine.queued is False
