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
