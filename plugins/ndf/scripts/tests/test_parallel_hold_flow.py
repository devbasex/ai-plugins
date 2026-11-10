"""耐久ワークフローの答え待ちの停止（#2707 の受け入れ条件 11・15〜26・29・30、I4〜I9・I11・I12）。

突き合わせの分岐は `_Run` を耐久ステップの差し替えで動かし、止める・再開するの流れは本物の DBOS で流す。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_parallel_run as tpr  # noqa: E402
from test_parallel_run import SCRIPTS, calls, event, questions, spawned, start, stop, until  # noqa: E402

import durable  # noqa: E402
import handoff  # noqa: E402
import parallel_answer as pa  # noqa: E402
import parallel_hold as ph  # noqa: E402
import parallel_hold_cli as phc  # noqa: E402
import parallel_lane as pl  # noqa: E402
import parallel_plan as pp  # noqa: E402
import parallel_run as prun  # noqa: E402

env = tpr.env
SEEN = {"limits": {"plan": 2, "lane": 1}, "marks": {}}
LANES = [
    {"name": "la", "input": {"kind": "sprint"}},
    {"name": "lb", "input": {"kind": "loop"}},
    {"name": "lc", "input": {"kind": "sprint"}},
]


@pytest.fixture
def run(monkeypatch):
    """耐久ステップとイベントを差し替えた `_Run`。全レーンが起動して動いている。"""
    r = prun._Run("/m", "p1", LANES)
    r.events, r.writes, r.resumes = [], [], []
    monkeypatch.setattr(durable, "set_event", lambda k, v: r.events.append((k, v)))
    r.write_hold = lambda *a: r.writes.append(a) or {"ok": True, "n": len(r.writes), "line": "1 行"}

    def resume(_main, _name, lanes, drop):
        r.resumes.append((list(lanes), list(drop)))
        return {"restarted": list(lanes), "refused": {}, "gone": list(drop)}

    r.resume_lanes = resume
    for n in r.order:
        r.lanes[n].update(started=True, state="running")
    return r


def own(i):
    return {"id": f"own{i}", "kind": "stall", "lane": "la"}


def ask(i, lane="la"):
    return {"id": f"q{i}", "kind": "ask", "lane": lane}


def marks(*names, at="t1"):
    return {"marks": {n: {"lane": n, "why": "boundary", "at": at} for n in names}}


def state(r, **lanes):
    for n, st in lanes.items():
        r.lanes[n]["state"] = st


def govern(r, **seen):
    r.govern_hold({**SEEN, **seen})


# ---- 突き合わせ ----


def test_an_observation_without_limits_does_nothing(run):
    run.questions = [own(i) for i in range(9)]
    run.superseded = {"own1"}
    run.govern_hold({"now": 1.0, "rows": {}})
    assert run.hold.empty and not run.writes and not run.events and len(run.questions) == 9


def test_below_the_limits_no_durable_step_runs(run):
    run.questions = [own(1)]
    govern(run)
    assert not run.writes and not run.resumes and not run.events


def test_the_hold_is_written_once_and_the_event_is_published(run):
    run.questions = [own(i) for i in range(3)]
    govern(run)
    govern(run)
    assert len(run.writes) == 1 and run.holds == 1
    key, ev = run.events[-1]
    assert key == "answer-hold" and ev["action"] == "answer_hold" and ev["hold"]["plan"] is True and ev["line"] == "1 行"


def test_a_failed_write_keeps_the_scope_and_writes_again(run):
    run.write_hold = lambda *a: {"ok": False, "error": "disk"}
    run.questions = [own(i) for i in range(3)]
    govern(run)
    assert run.hold.empty and not run.events
    run.write_hold = lambda *a: {"ok": True, "n": 1, "line": "x"}
    govern(run)
    assert run.hold.plan


def test_the_plan_hold_restarts_no_lane_and_a_cleared_plan_hold_restarts_the_marked_lane(run):
    state(run, la="stopped")
    run.questions = [own(i) for i in range(3)]
    govern(run, **marks("la"))
    assert not run.resumes
    run.questions = []
    govern(run, **marks("la"))
    assert run.resumes == [(["la"], [])]
    assert "la" in run.revive and run.lanes["la"]["state"] is None and "la" not in run.marks


def test_a_lane_over_its_limit_is_not_restarted_when_the_plan_hold_clears(run):
    state(run, la="stopped", lc="stopped")
    run.questions = [ask(1), ask(2), own(1)]  # 計画 3 > 2、la 2 > 1
    govern(run, **marks("la", "lc"))
    assert run.hold.plan and not run.resumes
    run.questions = [ask(1), ask(2)]
    govern(run, **marks("la", "lc"))
    assert run.hold.lanes == ("la",) and not run.hold.plan
    assert run.resumes == [(["lc"], [])]


def test_a_stopped_lane_without_a_mark_is_not_restarted_and_a_given_up_mark_is_only_removed(run):
    state(run, la="stopped", lc="stopped")
    run.lanes["lc"]["given_up"] = True
    govern(run, **marks("lc"))
    assert run.resumes == [([], ["lc"])]


def test_a_running_lane_keeps_its_mark_and_is_not_restarted(run):
    govern(run, **marks("la"))
    assert not run.resumes and "la" in run.marks
    state(run, la="stopped")
    govern(run, **marks("la"))
    assert run.resumes == [(["la"], [])]  # 印の直後に動いて見えたレーンも、止まれば再開する


def test_a_mark_is_dropped_only_after_the_lane_was_seen_stopped_and_then_ran(run):
    run.questions = [ask(1), ask(2)]  # la を止めておき、止まっていても再開しない
    state(run, la="stopped")
    govern(run, **marks("la"))
    assert not run.resumes and run.seen_stopped == {"la": "t1"}
    state(run, la="running")
    govern(run, **marks("la"))
    assert run.resumes == [([], ["la"])]


def test_a_replaced_mark_does_not_inherit_the_earlier_observation(run):
    run.questions = [ask(1), ask(2)]
    state(run, la="stopped")
    govern(run, **marks("la", at="t1"))
    state(run, la="running")
    govern(run, **marks("la", at="t2"))  # 再開の後の次の停止で置いた印
    assert not run.resumes and run.seen_stopped == {} and "la" in run.marks


def test_nothing_about_marks_changes_when_the_list_failed(run):
    state(run, la="stopped")
    run.ls_ok = False
    govern(run, **marks("la"))
    assert not run.resumes and run.marks == {}


def test_a_refused_restart_keeps_the_mark_and_is_asked_at_the_third_refusal(run, monkeypatch):
    monkeypatch.delenv("NDF_PARALLEL_SPAWN_REFUSALS", raising=False)
    run.resume_lanes = lambda *a: {"restarted": [], "refused": {"la": "limit"}, "gone": []}
    state(run, la="stopped")
    for _ in range(3):
        govern(run, **marks("la"))
    assert run.marks and [q["kind"] for q in run.questions] == ["spawn"]


def test_marks_of_unknown_lanes_do_not_hold_the_plan(run):
    state(run, la="stopped", lb="stopped", lc="stopped")
    govern(run, **marks("old"))
    assert run.marks == {} and prun._run_finished(run)


def test_an_answered_design_retires_older_requests_at_the_next_round(run):
    old = {"id": "old", "kind": "gate1", "lane": "la", "pr": 5, "at": "1"}
    now = {"id": "now", "kind": "gate1", "lane": "la", "pr": 5, "at": "2"}
    run.questions = [old, now]
    run.reply({"id": "now", "result": "approved"})
    later = {"id": "later", "kind": "gate1", "lane": "la", "pr": 5, "at": "3"}
    run.questions.append(later)  # 控えの後に届いた依頼は外さない
    govern(run)
    assert [q["id"] for q in run.questions] == ["later"] and "old" in run.answered
    assert not any(k == "answer-old" for k, _ in run.events)
    run.receive_question(old)
    assert [q["id"] for q in run.questions] == ["later"]  # 再送で一覧へ戻さない


def test_a_request_made_in_the_same_second_is_ordered_by_arrival(run):
    """依頼の `at` は秒までなので、同じ秒の新旧は届いた順で決める。古い方への答えで新しい依頼を外さない。"""
    old = {"id": "old", "kind": "gate1", "lane": "la", "pr": 5, "at": "1"}
    now = {"id": "now", "kind": "gate1", "lane": "la", "pr": 5, "at": "1"}
    run.questions = [old, now]
    run.reply({"id": "old", "result": "approved"})
    govern(run)
    assert [q["id"] for q in run.questions] == ["now"]
    run.questions = [old, now]
    run.answered.clear()
    run.reply({"id": "now", "result": "approved"})
    govern(run)
    assert run.questions == []


def test_restarts_skip_the_held_sprint_lanes_and_run_when_the_hold_clears(run, monkeypatch):
    """答え待ちの停止の範囲にある 1 スプリントのレーンは再起動しない。停止が解けたら耐久ワークフローが再起動する（#2936）。"""
    import parallel_launch

    hits = []
    monkeypatch.setattr(parallel_launch, "step_launch", lambda _m, argv, lane, *_a: hits.append(argv) or {"result": "up", "attempts": 1})
    state(run, la="stopped", lb="stopped")
    run.wake = ["la", "lb"]
    run.questions = [own(i) for i in range(3)]
    govern(run)
    run.launch_wakes()
    assert hits == [["restart", "lb"]] and run.wake == ["la"]  # ループのレーンは停止の範囲でも起こす（#2913 の決定 6）
    hits.clear()
    run.questions = []
    govern(run)
    run.launch_wakes()
    assert hits == [["restart", "la"]] and run.wake == [] and not any(k == "pause" for k, _ in run.events)


def test_keep_stopped_leaves_wake_and_revive(run):
    state(run, la="stopped")
    run.wake, run.revive, run.revive_at = ["la"], ["la"], {"la": 1.0}
    run.take({"kind": "left_stopped", "lane": "la"})
    assert run.wake == [] and run.revive == [] and run.left_stopped == {"la"}
    run.take({"kind": "left_stopped", "lane": "zz"})
    assert run.left_stopped == {"la"}


def test_left_stopped_ends_when_the_lane_runs_or_receives_an_answer(run):
    state(run, la="stopped", lc="stopped")
    run.left_stopped = {"la", "lc"}
    state(run, la="running")
    govern(run)
    assert run.left_stopped == {"lc"}
    run.questions = [ask(1, "lc")]
    run.reply({"id": "q1", "result": "answered", "choice": []})
    assert "lc" not in run.left_stopped


def test_a_left_stopped_lane_does_not_hold_the_end_of_the_plan(run, monkeypatch):
    monkeypatch.setattr(prun, "step_design_held", lambda *_a: ["la"])
    monkeypatch.setattr(prun, "step_held_loops", lambda *_a: [])
    monkeypatch.setattr(prun, "step_finish", lambda *_a: None)
    state(run, la="stopped", lb="stopped", lc="stopped")
    assert prun._finish_or_hold(run, "/m", "p1") is None and run.wake == ["la"]
    run.keep_stopped("la")
    assert prun._finish_or_hold(run, "/m", "p1")["status"] == "finished"


def test_the_plan_hold_blocks_spawning_and_a_marked_lane_blocks_its_dependents(monkeypatch):
    lanes = [{"name": "la", "input": {"kind": "sprint"}}, {"name": "lb", "input": {"kind": "sprint"}, "after": ["la"]}]
    r = prun._Run("/m", "p1", lanes)
    made = []
    monkeypatch.setattr(prun, "step_spawn", lambda m, n, names: made.extend(names) or {x: {"started": "t"} for x in names})
    monkeypatch.setattr(prun, "step_waiting_lanes", lambda *_a: [])
    monkeypatch.setattr(durable, "set_event", lambda *_a: None)
    r.lanes["la"].update(started=True, state="stopped")
    r.marks = {"la": {"at": "t"}}
    prun._spawn_ready(r, "/m")
    assert made == []
    r.marks = {}
    r.hold = ph.Hold(plan=True)
    prun._spawn_ready(r, "/m")
    assert made == []
    r.hold = ph.Hold()
    prun._spawn_ready(r, "/m")
    assert made == ["lb"]


def test_marks_keep_the_plan_from_finishing(run):
    state(run, la="stopped", lb="stopped", lc="stopped")
    run.marks = {"la": {"at": "t"}}
    assert not prun._run_finished(run)


# ---- 流れ ----


def limits(main, plan=2, lane=1):
    (main / ".ndf" / "pace.json").write_text(json.dumps({"parallel": {"answer_limit": {"plan": plan, "lane": lane}}}))


def send(main, i, lane="lz", kind="ask"):
    wid = pl.workflow_id("p1")
    msg = {"kind": kind, "id": f"q{i}", "lane": lane, "question": f"問い {i}", "at": str(i)}
    durable.send_to(pl.KIND, pl.record_identity(main, "p1"), wid, msg, "inbox", f"{wid}:inbox:q{i}")
    return msg["id"]


def hold_event(main):
    return event(main, "p1", "answer-hold")


def test_the_hold_and_the_resume_run_without_the_organizer(env):
    main, log, states = env["main"], env["log"], env["states"]
    limits(main)
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    until(lambda: spawned(log) == ["la"])
    ids = [send(main, i) for i in range(3)]
    until(lambda: ph.read_hold(main, "p1") and ph.read_hold(main, "p1")["plan"])
    ev = until(lambda: hold_event(main))
    assert ev["action"] == "answer_hold" and ev["hold"]["count"] == 3 and ev["n"] == 1
    assert "答え待ちの停止" in (pp.plan_dir(main, "p1") / "plan.md").read_text()
    # レーンが区切りで印を置いて止まる
    ph.place_mark(main, "p1", "la", "boundary")
    states.write_text(json.dumps({"la": "stopped"}))
    send(main, 9)  # 停止の間も受け付ける
    until(lambda: len(questions(main)["items"]) == 4)
    time.sleep(0.6)
    assert spawned(log) == ["la"] and calls(log, "restart") == []
    for rid in [*ids, "q9"]:
        pl.send_answer(main, "p1", rid, "answered", choice=[])
    until(lambda: ph.read_hold(main, "p1") is None)
    until(lambda: calls(log, "restart"))
    until(lambda: ph.read_marks(main, "p1") == {})  # 印は restart が通った後に消す
    assert calls(log, "restart")[0][:2] == ["restart", "la"]
    assert [r["change"] for r in ph.read_log(main, "p1")] == ["on", "off"]
    states.write_text(json.dumps({"la": "running"}))
    time.sleep(0.5)
    states.write_text(json.dumps({"la": "stopped"}))
    until(lambda: "lb" in spawned(log))
    states.write_text(json.dumps({"la": "stopped", "lb": "stopped"}))
    out = durable.output_of(pl.workflow_id("p1"))
    assert out["status"] == "finished" and out["holds"] == 1


def test_a_mark_with_no_requests_left_keeps_the_plan_until_the_restart(env):
    main, log, states = env["main"], env["log"], env["states"]
    states.write_text(json.dumps({"la": "stopped", "lb": "stopped"}))
    ph.place_mark(main, "p1", "lb", "boundary")
    start(main)
    until(lambda: spawned(log) == ["la"])
    until(lambda: "lb" in spawned(log))  # 印の無い la は止まったと数える
    until(lambda: calls(log, "restart"))
    assert durable.status(pl.workflow_id("p1")) in (*pl.RUNNING, "SUCCESS")
    until(lambda: "lb" not in ph.read_marks(main, "p1"))  # 印は restart が通った後に消す
    assert calls(log, "restart")[0][1] == "lb"


def test_stop_ends_the_workflow_with_a_hold_and_leaves_no_files(env):
    main, log, states = env["main"], env["log"], env["states"]
    limits(main)
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    until(lambda: spawned(log) == ["la"])
    for i in range(3):
        send(main, i)
    until(lambda: ph.read_hold(main, "p1"))
    ph.place_mark(main, "p1", "la", "boundary")
    stop(main)
    out = durable.output_of(pl.workflow_id("p1"))
    assert out["status"] == "stopped" and ph.read_hold(main, "p1") is None and ph.read_marks(main, "p1") == {}
    assert ph.read_log(main, "p1")  # 履歴は残す
    assert "## 答え待ちの停止" not in (pp.plan_dir(main, "p1") / "plan.md").read_text()


def test_a_release_in_flow_settles_and_no_new_release_is_built_during_the_plan_hold(env, tmp_path):
    main, log, states = env["main"], env["log"], env["states"]
    limits(main)
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    until(lambda: spawned(log) == ["la"])
    first = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st1")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    for i in range(3):
        send(main, i)
    until(lambda: ph.read_hold(main, "p1"))
    second = pl.send_release_request(main, "p1", "la", [11], [1], tmp_path / "st2")
    (pp.plan_dir(main, "p1") / "releases" / "1" / "1" / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10]}))
    wid = pl.workflow_id("p1")
    resume = lambda msg, key: durable.send_to(pl.KIND, pl.record_identity(main, "p1"), wid, msg, "resume", key)  # noqa: E731
    resume({"n": 1, "phase": "confirm"}, "c1")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed")
    resume({"n": 1, "phase": "settle", "result": "released", "version": "1.2.3"}, "s1")
    assert until(lambda: pl.read_answer(main, "p1", first)[0])["result"] == "released"
    time.sleep(0.6)
    assert not (pp.plan_dir(main, "p1") / "releases" / "2").exists() and pl.read_answer(main, "p1", second)[0] is None
    for i in range(3):
        pl.send_answer(main, "p1", f"q{i}", "answered", choice=[])
    until(lambda: (pp.plan_dir(main, "p1") / "releases" / "2").exists())


CRASH = textwrap.dedent(
    """
    import os, sys, time
    from pathlib import Path
    main, mode = Path(sys.argv[1]), sys.argv[2]
    sys.path[:0] = [sys.argv[3], sys.argv[3] + "/lib"]
    import dbos
    import durable, parallel_hold as ph, parallel_lane as pl, parallel_run as prun
    prun.dev_version = lambda _m: "1.2.3"
    prun.git_head = lambda _m: "sha1"
    if mode == "old":
        ph.limit_values = lambda _m: None  # 閾値を載せない版の観測で記録する
    prun.open_parallel_record(main, "p1")
    wid = pl.workflow_id("p1")
    durable.start(durable.WorkflowRef(wid, 1, "start"), prun.parallel_workflow, str(main), "p1")
    for i in range(3):
        msg = {"kind": "ask", "id": f"q{i}", "lane": "lz", "question": "問い", "at": str(i)}
        durable.send_to(pl.KIND, pl.record_identity(main, "p1"), wid, msg, "inbox", f"{wid}:inbox:q{i}")
    end = time.monotonic() + 30
    while time.monotonic() < end:
        if ph.read_hold(main, "p1") or (mode == "old" and len((durable.event(wid, "questions") or {}).get("items") or []) == 3):
            time.sleep(0.5)
            os._exit(0)
        time.sleep(0.1)
    os._exit(1)
    """
)


def crash(env, tmp_path, mode):
    """別のプロセスで 3 件の問いまで記録し、後始末をせずに落とす。"""
    limits(env["main"])
    env["states"].write_text(json.dumps({"la": "stopped"}))
    (tmp_path / "crash.py").write_text(CRASH)
    argv = [sys.executable, str(tmp_path / "crash.py"), str(env["main"]), mode, str(SCRIPTS)]
    got = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, got.stderr[-2000:]


def test_the_hold_survives_running_the_workflow_again(env, tmp_path):
    """打ち直しの後も同じ停止の範囲で、知らせを重ねず、答えると再開する（受け入れ条件 26）。"""
    main, log = env["main"], env["log"]
    crash(env, tmp_path, "new")
    ph.place_mark(main, "p1", "la", "boundary")
    rows = ph.read_log(main, "p1")
    prun.open_parallel_record(main, "p1")
    time.sleep(1.0)
    assert ph.read_hold(main, "p1")["plan"] is True and "la" in ph.read_marks(main, "p1")
    assert ph.read_log(main, "p1") == rows and calls(log, "restart") == []
    for i in range(3):
        pl.send_answer(main, "p1", f"q{i}", "answered", choice=[])
    until(lambda: calls(log, "restart"))


def test_a_record_without_limits_replays_and_works_from_the_next_round(env, tmp_path):
    """閾値を載せていない観測の記録を再生しても呼び出しの順が崩れず、実時間へ戻ると働く（I9）。"""
    main = env["main"]
    crash(env, tmp_path, "old")
    assert ph.read_hold(main, "p1") is None
    prun.open_parallel_record(main, "p1")
    until(lambda: ph.read_hold(main, "p1"))
    assert stop(main)["status"] == "stopped"


# ---- CLI ----


class Replied(Exception):
    def __init__(self, status, summary, items=None, code=None, next_step=None, **metrics):
        self.status, self.summary, self.code, self.metrics = status, summary, code or 0, metrics


def replied(*a, **k):
    raise Replied(*a, **k)


def park(main, lane="la", done="/d/done.json", clear=False):
    args = type("A", (), {"name": "p1", "lane": lane, "done": done, "clear": clear})
    with pytest.raises(Replied) as e:
        phc.cmd_park(main, args, replied)
    return e.value


def hold_file(main, **fields):
    (pp.plan_dir(main, "p1") / ph.HOLD_FILE).write_text(
        json.dumps({"plan": False, "lanes": [], "count": 1, "limits": {"plan": 8, "lane": 3}, **fields})
    )


def test_park_writes_the_next_step_and_the_mark_then_returns_the_stop_line(env):
    main = env["main"]
    hold_file(main, plan=True)
    got = park(main)
    assert got.code == 0 and "stop" in got.metrics["next"]
    doc = handoff.paths_of(main, pp.load_valid_plan(main, "p1").lane("la").handoff_name())[0]
    text = doc.read_text()
    assert "queue --resume /d/done.json" in text.split("## 次に実行するコマンド")[0]
    assert ph.read_marks(main, "p1")["la"]["why"] == "boundary"


def test_park_keeps_the_request_id_written_in_the_next_step(env):
    main = env["main"]
    hold_file(main, plan=True)
    doc = handoff.paths_of(main, pp.load_valid_plan(main, "p1").lane("la").handoff_name())[0]
    text = doc.read_text()
    head = "## 次にやること"
    assert head in text
    doc.write_text(text.replace(head, f"{head}\n\nparallel.py await --from req-123\n", 1))
    park(main)
    park(main)
    section = doc.read_text().split(head)[1].split("\n## ")[0]
    assert "req-123" in section and section.count("queue --resume /d/done.json") == 1
    assert section.index("queue --resume") < section.index("req-123")


def test_park_after_the_hold_cleared_writes_nothing_and_returns_the_resume_line(env):
    main = env["main"]
    got = park(main)
    assert got.metrics["cleared"] is True and "queue --resume" in got.metrics["next"] and ph.read_marks(main, "p1") == {}


def test_park_of_an_unknown_lane_is_refused(env):
    assert park(env["main"], lane="zz").code == 1


def test_park_clear_removes_the_mark_and_tells_the_workflow(env, monkeypatch):
    main = env["main"]
    sent = []
    monkeypatch.setattr(pl, "send_left_stopped", lambda m, n, lane: sent.append(lane))
    ph.place_mark(main, "p1", "la", "boundary")
    assert park(main, clear=True).metrics["mark_removed"] is True
    assert park(main, clear=True).metrics["mark_removed"] is False  # 印が無くても知らせる
    assert sent == ["la", "la"]


def test_park_clear_reports_a_failure_to_send(env, monkeypatch):
    def boom(*_a):
        raise RuntimeError("no record")

    monkeypatch.setattr(pl, "send_left_stopped", boom)
    assert park(env["main"], clear=True).code == 2


def test_an_answer_for_a_held_owner_places_a_mark_and_returns_no_line(env, monkeypatch):
    main = env["main"]
    hold_file(main, lanes=["la"])
    monkeypatch.setattr(pa, "owner_lanes", lambda *_a: ["la", "lb"])
    nxt, held = pa.owner_next(main, "p1", "q1", None, [{"id": "q1", "kind": "ask", "lane": "la"}])
    assert held == ["la"] and nxt is None  # 停止の範囲の外の持ち主は耐久ワークフローが再起動する（#2936）
    assert ph.read_marks(main, "p1")["la"]["why"] == "answered"


def test_a_release_helper_lane_gets_no_mark_while_the_whole_plan_is_held(env, monkeypatch):
    main = env["main"]
    hold_file(main, plan=True)
    monkeypatch.setattr(pa, "owner_lanes", lambda *_a: ["la", "p1-rel1"])
    nxt, held = pa.owner_next(main, "p1", "q1", None, [{"id": "q1", "kind": "ask", "lane": "p1-rel1"}])
    assert held == ["la"] and nxt is None
    assert set(ph.read_marks(main, "p1")) == {"la"}


def test_an_answered_helper_is_restarted_by_the_workflow_even_while_the_whole_plan_is_held(run, monkeypatch):
    """問いを保留して止まった一時レーンは、答えを受けたその場で耐久ワークフローが再起動する（計画全体の停止の間も）。"""
    import parallel_launch

    hits = []
    monkeypatch.setattr(parallel_launch, "step_launch", lambda _m, argv, lane, *_a: hits.append(argv) or {"result": "up", "attempts": 1})
    run.hold = ph.Hold(plan=True)
    run.questions = [ask(1, lane="p1-rel1")]
    run.reply({"id": "q1", "result": "answered", "choice": ["済んだ"]})
    assert hits == [["restart", "p1-rel1"]]


def test_without_a_hold_the_answer_returns_the_restart_lines(env, monkeypatch):
    main = env["main"]
    monkeypatch.setattr(pa, "owner_lanes", lambda *_a: ["la"])
    assert pa.owner_next(main, "p1", "q1", None, [{"id": "q1", "kind": "ask", "lane": "la"}])[1] == []
    assert ph.read_marks(main, "p1") == {}


def load_cli():
    spec = importlib.util.spec_from_file_location("parallel_cli_hold", SCRIPTS / "parallel.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def wait(cli, capsys, *argv):
    with pytest.raises(SystemExit) as e:
        cli.main(["wait", "p1", *argv])
    return e.value.code or 0, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_wait_returns_every_change_after_the_given_number_and_keeps_it_in_next(env, monkeypatch, capsys):
    main = env["main"]
    cli = load_cli()
    monkeypatch.setattr(cli, "main_of", lambda: main)
    monkeypatch.setattr(pl, "record_exists", lambda *_a: True)
    d = pp.plan_dir(main, "p1")
    rows = [{"n": i, "change": "on", "scope": "plan", "lanes": [], "count": 9, "limit": 8, "reason": "r"} for i in range(1, 11)]
    (d / ph.LOG_FILE).write_text("".join(json.dumps(r) + "\n" for r in rows))
    event_ = durable.Outcome("event", {"key": "answer-hold", "seq": 5, "action": "answer_hold", "n": 10, "line": "最後", "hold": {}})
    monkeypatch.setattr(durable, "wait_from", lambda *_a, **_k: event_)
    code, out = wait(cli, capsys, "--changes-after", "1")
    assert code == 0 and out["metrics"]["action"] == "answer_hold"
    assert [c["n"] for c in out["metrics"]["changes"]] == list(range(2, 11))
    monkeypatch.setattr(durable, "wait_from", lambda *_a, **_k: durable.Outcome("timeout"))
    code, out = wait(cli, capsys, "--after", "5", "--changes-after", "10", "--timeout", "1")
    assert code == 3 and "--after 5 --changes-after 10" in out["metrics"]["next"]
