"""起動の問いへの答えと、起動の印の無い古い記録の道（#2936 の PR 2979 のレビュー）。

耐久ステップとイベントを差し替えた `_Run` で、答えの後の分岐だけを確かめる。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_parallel_run as tpr  # noqa: E402,F401  lib を sys.path へ足す

import durable  # noqa: E402
import parallel_answer as pa  # noqa: E402
import parallel_cli as pcli  # noqa: E402
import parallel_launch as plc  # noqa: E402
import parallel_run as prun  # noqa: E402

LANES = [{"name": "la", "input": {"kind": "sprint"}}, {"name": "lb", "input": {"kind": "loop"}}]


@pytest.fixture
def run(monkeypatch):
    r = prun._Run("/m", "p1", LANES)
    r.events, r.launched = [], []
    monkeypatch.setattr(durable, "set_event", lambda k, v: r.events.append((k, v)))

    def launch(_main, argv, lane, cwd, fresh):
        r.launched.append((lane, list(argv)))
        return {"result": "up", "attempts": 1, "at": "2026-11-10T00:00:00Z", "reason": None}

    monkeypatch.setattr(plc, "step_launch", launch)
    for n in r.order:
        r.lanes[n].update(started=True, state="running")
    return r


def test_restarting_a_failed_helper_moves_the_watch_to_the_new_launch(run):
    """元の起動が failed でも、起こし直した一時レーンの止まりをもう一度問えるよう、見張りの目的を新しい起動へ向ける。"""
    run.current = {"n": 1}
    run.launch = {"seq": 3, "purpose": "rel-1-1-present", "argv": ["spawn"], "cwd": None}
    run.launches["rel-1-1-present"] = {"lane": "p1-rel1", "result": "failed"}
    run.helper_asked = 3
    q = {"kind": "spawn", "id": "q1", "lane": "p1-rel1", "purpose": "rel-1-1-present", "n": 1, "launch": {"argv": ["spawn"]}}
    assert run.relaunch(q, ["起こし直す"])
    assert run.launch["purpose"] == "rel-1-1-present-aq1" and run.launches["rel-1-1-present-aq1"]["result"] == "up"
    assert run.helper_asked is None and run.launch["retry"] == 1


def test_a_lane_given_up_after_a_failed_restart_is_not_woken_again(run, monkeypatch):
    """再起動の失敗の問いへ「止める」と答えたレーンは、設計の答えが未適用でも再起動の候補へ戻さず、計画を終えられる。"""
    run.lanes["la"].update(state="stopped")
    run.lanes["lb"].update(state="stopped")
    run.apply_own({"kind": "spawn", "id": "q2", "lane": "la"}, ["止める"])
    assert run.lanes["la"]["given_up"]
    monkeypatch.setattr(prun, "step_design_held", lambda *_a: ["la"])
    monkeypatch.setattr(prun, "step_held_loops", lambda *_a: [])
    monkeypatch.setattr(prun, "step_finish", lambda *_a: None)
    out = prun._finish_or_hold(run, "/m", "p1")
    assert out is not None and out["status"] == "finished" and "la" not in run.wake
    run.wake = ["la"]
    monkeypatch.setattr(run, "stopped", lambda: {"la", "lb"})
    run.launch_wakes()
    assert run.launched == []


def test_a_woken_lane_that_stops_before_it_is_seen_running_is_woken_again_then_asked(run, monkeypatch):
    """設計の答えを当てるために再起動したレーンが、動いて観える前に今の再起動より後で止まったら、もう一度起こし、2 回目の後は問う。"""
    run.lanes["la"].update(state="stopped")
    monkeypatch.setattr(run, "stopped", lambda: {"la"})
    run.wake = ["la"]
    run.launch_wakes()
    assert run.woken == {"la"} and run.launched == [("la", ["restart", "la"])]
    run.release_woken({"la": {"state": "stopped", "at": "2026-11-09T23:00:00Z"}})  # 前の起動の行
    assert run.woken == {"la"}
    run.release_woken({"la": {"state": "stopped", "at": "2026-11-10T00:01:00Z"}})
    assert run.woken == set() and run.questions == []
    assert run.wake_or_ask({"la"}) == ["la"]
    run.wake = ["la"]
    run.launch_wakes()
    assert "wake-la-2" in run.launches and run.woken == {"la"}
    run.release_woken({"la": {"state": "stopped", "at": "2026-11-10T00:02:00Z"}})
    assert run.woken == set() and run.questions == []  # 問うのは答えがまだ当たっていないと確かめてから（_finish_or_hold）
    assert run.wake_or_ask({"la"}) == []
    assert [(q["kind"], q["lane"], q["purpose"]) for q in run.questions] == [("spawn", "la", "wake-la-2")]


def test_a_lane_asked_after_two_restarts_is_not_restarted_until_answered(run, monkeypatch):
    """2 回再起動しても止まるレーンは、問いを立てた後の周回でも再起動の対象へ戻らない（wake-la-3 を打たない）。"""
    run.lanes["la"].update(state="stopped")
    run.lanes["lb"].update(state="stopped")
    run.wake_tries["la"] = 2
    run.launches.update({"wake-la-1": {"lane": "la"}, "wake-la-2": {"lane": "la"}})
    monkeypatch.setattr(prun, "step_design_held", lambda *_a: ["la"])
    for _ in range(2):
        assert prun._finish_or_hold(run, "/m", "p1") is None
        run.launch_wakes()
    assert run.launched == [] and "wake-la-3" not in run.launches and len(run.questions) == 1
    q = run.questions[0]
    assert run.relaunch(q, ["起こし直す"]) and "la" not in run.wake_tries  # 人が起こし直すと答えたら数え直す


def test_restarts_are_counted_until_the_answer_lands_even_if_the_lane_was_seen_running(run, monkeypatch):
    """動いて観えても再起動の回数は数え直さない（動いた後に答えを当てずに止まるレーンを打ち続けない）。答えが当たって持ち主から外れたら数え直す。"""
    monkeypatch.setattr(run, "stopped", lambda: {"la"})
    for at in ("2026-11-10T00:01:00Z", "2026-11-10T00:02:00Z"):
        run.wake = ["la"]
        run.launch_wakes()
        run.release_woken({"la": {"state": "running", "at": at}})
    assert run.woken == set() and run.wake_tries["la"] == 2
    assert run.wake_or_ask({"la"}) == [] and [q["lane"] for q in run.questions] == ["la"]
    monkeypatch.setattr(prun, "step_design_held", lambda *_a: [])
    monkeypatch.setattr(prun, "step_held_loops", lambda *_a: [])
    monkeypatch.setattr(prun, "step_finish", lambda *_a: None)
    prun._finish_or_hold(run, "/m", "p1")
    assert "la" not in run.wake_tries


def test_restart_counts_and_woken_lanes_survive_a_rebuild(run):
    """作り直しで再起動の回数と再起動直後の印を失わない（2 回で問う上限が外れず、古い停止の行で打ち直さない）。"""
    run.woken, run.wake_tries = {"la"}, {"la": 2}
    again = prun._Run("/m", "p1", LANES)
    again._adopt_state(run.state())
    assert again.woken == {"la"} and again.wake_tries == {"la": 2}


def test_restarting_a_release_helper_after_its_question_carries_the_release_number(run, monkeypatch):
    """問いに答えた一時レーンの再起動が立ち上がらなければ、起動の問いへリリースの連番を添え、「止める」でリリースを締められる。"""

    def down(_main, argv, lane, cwd, fresh):
        return {"result": "failed", "attempts": 2, "at": "2026-11-10T00:00:00Z", "reason": "x"}

    monkeypatch.setattr(plc, "step_launch", down)
    run.current = {"n": 2}
    run.wake_owners({"kind": "gate1", "id": "g1", "lane": "p1-rel2"})
    run.wake_owners({"kind": "gate1", "id": "g2", "lane": "p1-rel1"})
    asked = {q["lane"]: q.get("n") for q in run.questions if q["kind"] == "spawn"}
    assert asked == {"p1-rel2": 2, "p1-rel1": None}


def test_stopping_a_held_loop_helper_that_did_not_come_up_stops_the_plan(run):
    """保留したループの残りを流す一時レーンを起こせない問いの「止める」は、計画を止める（問いだけ消えて終わらない状態にしない）。"""
    run.apply_own({"kind": "spawn", "id": "q3", "lane": "p1-rel2", "purpose": "held-lb"}, ["止める"])
    assert run.stop


def test_an_old_record_hands_the_launch_lines_to_the_organizer(run):
    """起動の印の無い古い記録では、知らせに起動の 1 行（spawn）と起こす 1 行（wake）を載せ、wait がそれを返す。"""
    run.launching = False
    run.lanes["la"].update(state="stopped")
    run.wake = ["la"]
    spec = prun.helper_spawn("p1", 1, 1)
    run.pause("release", n=1, k=1, purpose="rel-1-1-present", **spec)
    key, ev = run.events[-1]
    assert key == "pause" and ev["spawn"].startswith(f"python3 {prun.RELAY} spawn --name p1-rel1 ")
    assert ev["wake"] == [f"python3 {prun.RELAY} restart la"] and pcli.legacy_pause(ev)
    run.publish_questions()
    assert run.events[-1][1]["launching"] is False


def test_a_new_record_keeps_the_pause_to_the_helper(run):
    run.pause("release", n=1, k=1, purpose="rel-1-1-present", **prun.helper_spawn("p1", 1, 1))
    ev = run.events[0][1]
    assert "spawn" not in ev and "wake" not in ev and not pcli.legacy_pause(ev)
    run.publish_questions()
    assert "launching" not in run.events[-1][1]


def test_an_old_record_answer_returns_the_chosen_command(monkeypatch):
    """古い記録の問い（commands を持つ）は、選んだ答えの 1 行を answer が返す。新しい記録の問いは何も返さない。"""
    q = {"id": "s1", "kind": "stall", "lane": "la", "commands": {"起こし直す": "python3 relay.py restart la"}}
    assert pa.owner_next(Path("/m"), "p1", "s1", ["起こし直す"], [q]) == ("python3 relay.py restart la", [])
    assert pa.owner_next(Path("/m"), "p1", "s1", ["起こし直す"], [{**q, "commands": None}]) == (None, [])
