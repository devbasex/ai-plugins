"""答え待ちの停止の範囲の選定と業務時間外の選定の順（#2913 の受け入れ条件 1〜3・6・9、I1・I2・I7、決定 5・6）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import parallel_answer as pa  # noqa: E402
import parallel_hold as ph  # noqa: E402
import parallel_plan as pp  # noqa: E402
import sprint_loop_argv as sl_argv  # noqa: E402
import test_parallel_hold_flow as tph  # noqa: E402
import test_parallel_run as tpr  # noqa: E402
import test_sprint_loop as tsl  # noqa: E402
from test_parallel_hold_lanes import HOLD, plan_dir  # noqa: E402
from test_sprint_loop import rec, sl, sl_record, sl_select, start  # noqa: E402

env, mvv = tsl.env, tsl.mvv
run_env = tpr.env
APPROVED = {"answer": "approved", "merged_at": "t", "lap": 0}
# Clock は 2026-10-25T00:01Z から 1 分ずつ進む（日曜）
OUTSIDE = {"timezone": "UTC", "days": ["mon"], "start": "09:00", "end": "10:00"}
INSIDE = {"timezone": "UTC", "days": ["sun", "mon"], "start": "00:00", "end": "23:59"}


@pytest.fixture
def lane(env, monkeypatch):
    d = plan_dir(env.root, "loop")
    monkeypatch.setenv("NDF_RELAY_NAME", "la")
    env.hold = d / ph.HOLD_FILE
    start(env)
    return env


def edit(env, **fields):
    with sl_record.open_record(env, "l1") as (r, before):
        r.update(fields)
        sl_record.write_record(env, r, before)


def declare_hours(env, hours):
    (env.root / ".ndf" / "pace.json").write_text(json.dumps({**tsl.DECL, "business_hours": hours}))


def test_waiting_issues_do_not_block_other_candidates(env):
    """受け入れ条件 1: 人の判断待ちの課題があっても、選べる候補を選んでループの 1 回が始まる。"""
    start(env)
    edit(env, waiting=[{"number": 11, "request": "r-11", "lap": 0}])
    got = sl_select.cmd_select(env, "l1")
    assert got["status"] == "ok" and got["metrics"]["selected"] == [12, 13]
    assert sl.current_lap(rec(env))["hold_only"] is False


def test_a_held_lane_selects_only_approved_designs_and_records_why(lane):
    """受け入れ条件 2: 停止の範囲でも承認した設計を選んで進み、承認していない課題は範囲の理由で外れる。"""
    edit(lane, waiting=[{"number": 12, **APPROVED}])
    lane.hold.write_text(json.dumps(HOLD))
    got = sl_select.cmd_select(lane, "l1")
    assert got["status"] == "ok" and got["metrics"]["status"] == "selected" and got["metrics"]["selected"] == [12]
    why = {r["number"]: r["reason"] for r in got["metrics"]["excluded"]}
    assert why[11] == sl_select.HOLD_REASON
    assert got["metrics"]["answer_hold"] == {"plan": "p1", "scope": "plan", "count": 9, "limit": 8}
    assert got["metrics"]["hours"]["declared"] is False and got["metrics"]["hours"]["inside"] is True
    lap = sl.current_lap(rec(lane))
    assert lap["hold_only"] is True and lap["hours"]["at"]
    assert tsl.sprint(lane, "l1", "standard", "1.0.0-dev.1", None, [])["status"] == "ok"


def test_a_held_lane_without_approved_designs_stops_with_a_mark_and_opens_no_lap(lane):
    """受け入れ条件 3: 承認の要らない候補が無ければ今と同じく answer_hold で止まり、ループの 1 回を始めない。"""
    lane.hold.write_text(json.dumps(HOLD))
    out = sl_select.cmd_select(lane, "l1")
    assert out["exit"] == 3 and out["metrics"]["stop"]["condition"] == "answer_hold"
    assert {r["reason"] for r in out["metrics"]["selection"]} >= {sl_select.HOLD_REASON}
    assert rec(lane)["laps"] == [] and ph.read_marks(lane.root, "p1")["la"]["why"] == "boundary"


def test_the_hold_is_read_after_the_pending_laps_are_handled(lane, monkeypatch):
    """決定 5: 答え待ちの停止はすぐ止まる条件でなく、保留したループの 1 回の残りを流した後に読む。"""
    assert "answer_hold" not in sl_select.IMMEDIATE_STOPS
    lane.hold.write_text(json.dumps(HOLD))
    flowed = {"status": "ok", "summary": "残りを流す"}
    monkeypatch.setattr(sl_select, "pending_step", lambda *_a: flowed)
    assert sl_select.cmd_select(lane, "l1") is flowed


def test_a_resumed_loop_resumes_while_covered_and_the_boundary_decides(lane):
    """決定 6: 停止の範囲に残っていても再開し、承認の要らない候補が無ければ境で止まり直す。"""
    lane.hold.write_text(json.dumps(HOLD))
    assert sl_select.cmd_select(lane, "l1")["exit"] == 3
    assert sl.cmd_start(lane, "l1", [], {})["metrics"]["status"] == "resumed"
    assert sl_select.cmd_select(lane, "l1")["metrics"]["stop"]["condition"] == "answer_hold"


def test_a_hold_only_lap_refuses_to_build_a_design_step(lane):
    """I1: 停止の範囲で始めたループの 1 回に、設計を承認していない課題を含めて組まない。loop exclude も印を引き継ぐ。"""
    edit(lane, waiting=[{"number": 12, **APPROVED}])
    lane.hold.write_text(json.dumps(HOLD))
    sl_select.cmd_select(lane, "l1")
    again = sl_select.cmd_exclude(lane, "l1", 12, "試しに外す")
    assert again["exit"] == 3 and again["metrics"]["stop"]["condition"] == "answer_hold"
    lane.hold.unlink()
    sl.cmd_start(lane, "l1", [], {})
    edit(lane, excluded=[])
    lane.hold.write_text(json.dumps(HOLD))
    sl_select.cmd_select(lane, "l1")
    with sl_record.open_record(lane, "l1") as (r, before):
        sl.current_lap(r)["selection"].append({"number": 11, "decision": sl_record.SELECTED, "reason": "順位"})
        sl_record.write_record(lane, r, before)
    out = tsl.sprint(lane, "l1", "standard", "1.0.0-dev.1", None, [])
    assert out["exit"] == 2 and sl_argv.HOLD_ONLY_REFUSAL in out["summary"] and "#11" in out["summary"]


def test_the_classification_and_the_design_step_use_the_same_rule(env):
    """I2: 選定が承認の要らない作業とした課題は、組んだスプリントの --design に入らない。"""
    r = {"waiting": [{"number": 12, **APPROVED}], "outside_plan": None}
    lap = {"selection": [{"number": n, "decision": sl_record.SELECTED} for n in (11, 12)], "versions": {"dev": ""}}
    lap.update(state_path="s", sprint="l1-1", mode="standard", out="o")
    argv = sl_argv.new_sprint_argv(str(env.root), r, lap, [])
    design = argv[argv.index("--design") + 1 : argv.index("--designed")]
    assert design == ["11"] == [str(n) for n in sl_argv.design_issues(r, [11, 12])]
    assert argv[argv.index("--designed") + 1 :] == ["12"]


@pytest.mark.parametrize("hours, picked, inside", [(INSIDE, [12, 13], True), (OUTSIDE, [13, 11], False), (None, [12, 13], True)])
def test_outside_business_hours_designs_come_before_the_fixes(env, hours, picked, inside):
    """受け入れ条件 5・6: 業務時間外は承認した設計 → 設計を要る課題（順位の順）。時間内と宣言なしは今の順（直しの先頭を先に）。"""
    if hours:
        declare_hours(env, hours)
    start(env)
    edit(env, waiting=[{"number": 13, **APPROVED}, {"number": 12, "answer": "revise"}])
    got = sl_select.cmd_select(env, "l1")
    assert sorted(got["metrics"]["selected"]) == sorted(picked)
    assert got["metrics"]["hours"]["inside"] is inside and got["metrics"]["hours"]["declared"] is bool(hours)
    assert sl.current_lap(rec(env))["hours"] == got["metrics"]["hours"]


def test_outside_hours_the_slots_go_to_approved_then_needs_design_then_the_rest():
    """受け入れ条件 6: 業務時間外の枠の順は 承認した設計 → 設計を要る課題 → 設計の要らない課題（品質の枠を先にしない）。"""
    cands = [{"number": 11}, {"number": 12, "quality_slot": True}, {"number": 13}, {"number": 14}]
    assert sl_select._take_order(cands, [0, 1, 2, 3], {14}, {12, 13}) == ([3, 1, 2, 0], [3])
    assert sl_select._take_order(cands, [0, 1, 2, 3], {14}) == ([3, 1, 0, 2], [3])


def test_a_broken_business_hours_declaration_warns_at_loop_start(env):
    """受け入れ条件 4: loop start は警告を出して始める。"""
    declare_hours(env, {**OUTSIDE, "timezone": None})
    out = start(env)
    assert out["metrics"]["status"] == "new"
    assert [i["rule"] for i in out["items"] if i.get("rule") == "business-hours"] == ["business-hours"]


def test_a_running_lap_is_not_stopped_by_the_hold_or_the_hours(lane):
    """受け入れ条件 9・I7: 動いているループの 1 回は、停止の範囲と業務時間の境をまたいでも loop sprint・loop lap が進む。"""
    assert sl_select.cmd_select(lane, "l1")["metrics"]["status"] == "selected"
    lane.hold.write_text(json.dumps(HOLD))
    declare_hours(lane, OUTSIDE)
    assert tsl.sprint(lane, "l1", "standard", "1.0.0-dev.1", None, [])["status"] == "ok"
    tsl.set_state(lane, sl.current_lap(rec(lane)))
    out = sl.cmd_lap(lane, "l1", False, None, [])
    assert out["metrics"]["status"] == "continue" and rec(lane)["laps"][0]["outcome"] == sl_record.DONE
    # 締めた後の境は停止の範囲で止まらず、次の選定が承認の要らない候補の有無で決める（決定 5）
    assert sl_select.cmd_select(lane, "l1")["metrics"]["stop"]["condition"] == "answer_hold"


def test_an_answer_wakes_a_held_loop_lane_but_not_a_held_sprint_lane(run_env, monkeypatch):
    """決定 6: 停止の範囲でも、答えを受けたループのレーンには再開の印を置かない（耐久ワークフローが起こす）。1 スプリントのレーンは再開の印を置く。"""
    main = run_env["main"]
    p = pp.plan_dir(main, "p1") / "plan.json"
    data = json.loads(p.read_text())
    data["lanes"][0]["input"] = {"kind": "loop", "loop": "l1"}
    p.write_text(json.dumps(data))
    tph.hold_file(main, lanes=["la", "lb"])
    monkeypatch.setattr(pa, "owner_lanes", lambda *_a: ["la", "lb"])
    nxt, held = pa.owner_next(main, "p1", "q1", None, [{"id": "q1", "kind": "gate1", "lane": "la"}])
    assert held == ["lb"] and nxt is None  # ループのレーンの再起動は耐久ワークフローが打つ（#2936）
    assert set(ph.read_marks(main, "p1")) == {"lb"}
