"""1 スプリントのレーンの終わり（#3058 の A・B と I1〜I9・移行性）: 終わりの記録で数える・猶予の後に止める・止められなかった
レーンの問い・終わりの記録の無い停止の問い・終わった後の差し戻し・版の要るまとめの待ちと一時レーン・副命令 end と verified。

耐久ワークフローの中の値は `_Run` を耐久ワークフローの外で組んで確かめ（知らせは捨てる）、止める耐久ステップは
`test_parallel_run` の偽の relay.py（`env`）で流す。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_parallel_run as tpr  # noqa: E402

import durable  # noqa: E402
import parallel_end as pend  # noqa: E402
import parallel_ledger as plg  # noqa: E402
import parallel_run as prun  # noqa: E402

env = tpr.env
RID = "la-r000000000001"
LANES = [{"name": "la", "input": {"kind": "sprint"}}, {"name": "lb", "input": {"kind": "sprint"}, "after": ["la"]}]


def stamp(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


@pytest.fixture
def run(monkeypatch):
    """印 `parallel-lane-end`・`parallel-approved-sha` の後の記録の `_Run`（耐久ワークフローの外。知らせは捨てる）。"""
    monkeypatch.setattr(durable, "set_event", lambda *_a, **_k: None)
    monkeypatch.setattr(prun, "step_admit", lambda m, n, names: prun.step_spawn(m, n, names))
    monkeypatch.setattr(prun, "step_progress", lambda *_a: None)
    monkeypatch.setattr(prun, "step_waiting_lanes", lambda *_a: [])
    r = prun._Run("/m", "p1", LANES)
    r.ending = r.approved_only = True
    return r


@pytest.fixture
def spawned(monkeypatch):
    got: list[str] = []
    monkeypatch.setattr(prun, "step_spawn", lambda _m, _n, names: got.extend(names) or {n: {"started": True} for n in names})
    return got


def halts(monkeypatch, results):
    """止める耐久ステップを差し替え、打った回数を数える。`results` は打つたびに返す結果。"""
    calls: list[str] = []

    def fake(_main, _name, lane):
        calls.append(lane)
        return {"lane": lane, **results[min(len(calls), len(results)) - 1]}

    monkeypatch.setattr(pend, "step_halt", fake)
    return calls


def end(r, lane="la", at=None, request=None, by=None):
    r.take({"kind": "lane_end", "id": lane, "lane": lane, "by": by or lane, "at": at or stamp(time.time()), "request": request})


def kinds(r, kind):
    return [q for q in r.questions if q.get("kind") == kind]


# ---- 終わりの記録で数える（A4・A5・I1・I2） ----


def test_a_sprint_lane_stopped_without_an_end_record_does_not_start_its_dependents(run, spawned):
    run.lanes["la"].update(started=True, state="stopped")
    prun._spawn_ready(run, "/m")
    assert spawned == []  # 終わりの記録の無い停止は終わったと数えない（A5）
    end(run)
    prun._spawn_ready(run, "/m")
    assert spawned == ["lb"]  # 終わりの記録があり止まって観えたレーンの後に起動する（A4）


def test_only_the_first_end_record_of_a_sprint_lane_counts(run):
    run.lanes["la"]["started"] = True
    end(run, at="2026-11-12T00:00:00Z")
    end(run, at="2026-11-12T01:00:00Z")
    assert run.lanes["la"]["ended"]["at"] == "2026-11-12T00:00:00Z"


def test_an_end_record_of_a_loop_lane_changes_nothing(monkeypatch):
    monkeypatch.setattr(durable, "set_event", lambda *_a, **_k: None)
    r = prun._Run("/m", "p1", [{"name": "lc", "input": {"kind": "loop"}}])
    r.ending = True
    end(r, lane="lc")
    assert "ended" not in r.lanes["lc"]
    r.lanes["lc"].update(started=True, state="stopped")
    assert r.counted(r.stopped()) == {"lc"}  # 開発ループのレーンは今のとおり止まって観えれば数える（A8）


def test_an_old_record_counts_any_stopped_lane_and_ignores_end_rows(monkeypatch):
    monkeypatch.setattr(durable, "set_event", lambda *_a, **_k: None)
    r = prun._Run("/m", "p1", LANES)
    r.lanes["la"].update(started=True, state="stopped")
    end(r)
    assert r.counted(r.stopped()) == {"la"} and "ended" not in r.lanes["la"]


@pytest.mark.parametrize("ending", [True, False], ids=["new", "old"])
def test_only_a_record_after_the_mark_tells_the_lanes_it_ends_them(monkeypatch, ending):
    """目録の側（`sprint_lane_close.plan_ends`）はイベント `lane-end` で、版の要るまとめを流せる計画かを読む。"""
    got = []
    monkeypatch.setattr(durable, "set_event", lambda k, v: got.append((k, v)))
    r = prun._Run("/m", "p1", LANES)
    r.ending = ending
    r.announce_ending()
    assert got == ([("lane-end", True)] if ending else [])


def test_the_plan_does_not_finish_while_a_sprint_lane_has_no_end_record(run):
    for n in ("la", "lb"):
        run.lanes[n].update(started=True, state="stopped")
    assert not prun._run_finished(run)
    end(run)
    end(run, lane="lb")
    assert prun._run_finished(run)


# ---- 止める（A2・A3・A6・I3・I4） ----


def test_an_ended_lane_is_halted_once_after_the_delay(run, monkeypatch):
    calls = halts(monkeypatch, [{"result": "stopped", "code": 0, "state": "stopped"}])
    t = time.time()
    run.lanes["la"].update(started=True, state="running")
    end(run, at=stamp(t))
    run.seen_at = t + pend.HALT_DELAY - 5
    run.observe_ends({"la": {"state": "running"}})
    assert calls == []  # 猶予の間は止めない
    run.seen_at = t + pend.HALT_DELAY + 1
    run.observe_ends({"la": {"state": "running"}})
    run.observe_ends({"la": {"state": "stopped"}})
    assert calls == ["la"] and run.lanes["la"]["state"] == "stopped"  # 1 回だけ打つ（I3）
    assert run.counted(run.stopped()) == {"la"}


def test_a_lane_that_cannot_be_halted_is_asked_until_it_is_seen_stopped(run, monkeypatch):
    calls = halts(monkeypatch, [{"result": "failed", "code": 1, "state": "running"}])
    run.lanes["la"].update(started=True, state="running")
    end(run, at=stamp(time.time() - 3600))
    run.seen_at = time.time()
    run.observe_ends({})
    (q,) = kinds(run, "halt")
    assert q["lane"] == "la" and q["commands"]["止めた"].endswith("relay.py stop la")
    run.reply({"id": q["id"], "choice": ["止めた"]})
    assert kinds(run, "halt") == [] and run.wake == []  # 答えても終わったレーンを起こさない
    run.observe_ends({})  # まだ動いて観える: 新しい id で立て直す（I4）
    (q2,) = kinds(run, "halt")
    assert q2["id"] != q["id"] and calls == ["la"]
    run.lanes["la"]["state"] = "stopped"
    run.observe_ends({})
    assert kinds(run, "halt") == [] and run.counted(run.stopped()) == {"la"}


@pytest.mark.parametrize(
    ("code", "states", "want"), [(0, {"la": "stopped"}, "stopped"), (3, {}, "stopped"), (0, {"la": "running"}, "failed")]
)
def test_step_halt_reads_the_stop_code_and_the_list(env, monkeypatch, code, states, want):
    """止める 1 行が 0 か 3（既に止まっている。A6）で、一覧で止まっているか一覧に無ければ stopped。台帳に 1 行残す（A2）。"""
    main = env["main"]
    env["states"].write_text(json.dumps(states))
    real = pend.relay
    monkeypatch.setattr(pend, "relay", lambda m, *a: (code, {}) if a[0] == "stop" else real(m, *a))
    got = pend.step_halt(str(main), "p1", "la")
    assert got["result"] == want and got["code"] == code
    rows = [r for r in plg.read_ledger_rows(main, "p1").rows if r["kind"] == "lane_halt"]
    assert [r["key"] for r in rows] == ["lane_halt:la"] and rows[0]["msg"]["result"] == want


def test_a_halt_row_restores_the_result_without_halting_again(run, monkeypatch):
    calls = halts(monkeypatch, [{"result": "stopped", "code": 0, "state": "stopped"}])
    end(run, at=stamp(time.time() - 3600))
    run.take({"kind": "lane_halt", "id": "la", "lane": "la", "result": "stopped", "code": 0, "state": "stopped", "at": "x"})
    run.seen_at = time.time()
    run.observe_ends({})
    assert calls == [] and run.lanes["la"]["halt"]["result"] == "stopped"


def test_the_end_state_survives_the_checkpoint(run):
    end(run, request=RID)
    run.queue_verify(RID, "la", "/x/v.json", "1.2.3", 1)
    got = run.state()
    r2 = prun._Run("/m", "p1", LANES)
    r2.ending = True
    r2._adopt_state(got)
    assert r2.lanes["la"]["ended"]["request"] == RID and r2.verifies == run.verifies


# ---- 版の要るまとめ（B2・B3・I6・I7） ----


def request(run, rid=RID, lane="la", plan="/x/lane-verify.json"):
    run.take({"kind": "request", "id": rid, "lane": lane, "prs": [10], "issues": [1], "sprint_state": "/s", "at": "x", "verify_plan": plan})


def test_released_answer_queues_the_verify_and_a_helper_runs_it_alone(run, monkeypatch):
    launched = []
    monkeypatch.setattr(run, "launch_once", lambda purpose, lane, argv, cwd=None, **_k: launched.append((purpose, lane, argv[-1])) or {})
    request(run)
    run.answer(RID, "released", "1.2.3", 1)
    assert [v["id"] for v in run.verifies] == [RID] and not prun._run_finished(run)  # 待ちが残る間は終わらない（I6）
    run.launch_verify()
    assert launched == []  # レーンの終わりの記録（版の要らないまとめの完了）の前は起動しない
    end(run)
    run.current = {"n": 2}
    run.launch_verify()
    assert launched == []  # リリースを流す一時レーンと同時に起動しない（I7）
    run.current = None
    run.launch_verify()
    run.launch_verify()
    assert [(p, h) for p, h, _ in launched] == [(f"verify-{RID}", "p1-rel1v")]
    assert f"--parallel-helper p1 verify {RID}" in launched[0][2]
    opened = []
    monkeypatch.setattr(run, "open_release", lambda: opened.append(1))
    request(run, rid="lb-r000000000002", lane="lb")
    prun._tick_release(run, "/m", "p1", 0)
    assert opened == []  # 流している間は次のリリースを組まない（決定 5）
    run.take({"kind": "verified", "id": RID, "result": "完了", "at": "x"})
    assert run.verifies == [] and run.verifying is None and run.lanes["la"]["verified"] == "完了"


@pytest.mark.parametrize("leave", ["left_stopped", "given_up"])
def test_the_wait_of_a_lane_left_stopped_without_an_end_record_does_not_hold_the_end(run, leave):
    request(run)
    run.answer(RID, "released", "1.2.3", 1)
    run.pending = []
    end(run, lane="lb")
    for n in ("la", "lb"):
        run.lanes[n].update(started=True, state="stopped")
    assert not prun._run_finished(run)  # 終わりの記録も止めたままの印も無いレーンは数えない
    run.take({"kind": "left_stopped", "lane": "la"}) if leave == "left_stopped" else run.lanes["la"].update(given_up=True)
    assert [v["id"] for v in run.verifies] == [RID] and prun._run_finished(run)  # 流れない待ちは計画の終わりを止めない
    end(run)
    assert not prun._run_finished(run)  # 終わりの記録が来れば外していない待ちが流れるまで終えない


def test_a_request_without_a_verify_plan_and_a_failed_answer_queue_nothing(run):
    run.take({"kind": "request", "id": RID, "lane": "la", "prs": [10], "issues": [1], "sprint_state": "/s", "at": "x"})
    run.answer(RID, "released", "1.2.3", 1)
    request(run, rid="lb-r000000000002", lane="lb")
    run.answer("lb-r000000000002", "failed", None, 1, "組めない")
    assert run.verifies == []


def test_a_rebuilt_request_finds_the_verify_plan_of_its_origin(run):
    request(run)
    run.answer(f"{RID}-again2", "released", "1.2.3", 2)
    assert [v["plan"] for v in run.verifies] == ["/x/lane-verify.json"]


def test_a_verify_notice_without_a_request_queues_the_verify(run):
    run.take({"kind": "verify", "id": RID, "lane": "la", "plan": "/x/v.json", "version": "1.2.3", "at": "x"})
    assert run.verifies[0]["plan"] == "/x/v.json"


def test_stopping_a_stalled_verify_helper_drops_the_wait(run, monkeypatch):
    monkeypatch.setattr(run, "launch_once", lambda *_a, **_k: {})
    request(run)
    end(run)
    run.answer(RID, "released", "1.2.3", 1)
    run.launch_verify()
    q = {"kind": "helper", "id": "p1-rel1v-verify-1", "lane": "p1-rel1v", "verify": RID, "purpose": f"verify-{RID}", "options": []}
    run.questions.append(q)
    run.reply({"id": q["id"], "choice": ["止める"]})
    assert run.verifies == [] and run.verifying is None


def test_stopping_a_verify_helper_that_never_came_up_drops_the_wait(run, monkeypatch):
    import parallel_launch as plaunch

    monkeypatch.setattr(plaunch, "step_launch", lambda *_a, **_k: {"result": "failed", "attempts": 2, "at": "x", "reason": "down"})
    run.launching = True
    request(run)
    end(run)
    run.answer(RID, "released", "1.2.3", 1)
    run.launch_verify()
    (q,) = kinds(run, "spawn")
    assert q["verify"] == RID  # 起動の問いが版の要るまとめの依頼を持つ
    run.reply({"id": q["id"], "choice": ["起こし直す"]})
    (again,) = [x for x in kinds(run, "spawn") if x["id"] != q["id"]]
    assert again["verify"] == RID and run.verifies[0]["purpose"] == again["purpose"]  # 起こし直しの問いへも引き継ぐ
    run.reply({"id": again["id"], "choice": ["止める"]})
    assert run.verifies == [] and run.verifying is None


@pytest.mark.parametrize(
    ("at", "want"),
    [
        ("2026-11-12T00:00:01Z", True),
        ("2026-11-12T00:00:00Z", True),
        ("2026-11-11T23:59:59.500Z", True),  # 0.5 秒の幅の内（境界を含む）
        ("2026-11-11T23:59:59.499Z", False),
        ("2026-11-11T23:00:00Z", False),
        (None, False),
        ("", False),
    ],
)
def test_char_moved_since_reads_the_row_time_with_half_a_second_margin(at, want):
    """現状固定: 起動の確かめ（`parallel_launch._moved_since`）は行の `at` が打った時刻 - 0.5 秒以降なら動いたと読む。"""
    import clock
    import parallel_launch as plaunch

    since = clock.parse("2026-11-12T00:00:00Z", naive="utc").timestamp()
    assert plaunch._moved_since({"at": at}, since) is want
    assert plaunch._moved_since({}, since) is False


def test_char_came_up_by_state_and_freshness():
    """現状固定: `came_up` は fresh なら時刻だけで、そうでなければ動いて観える状態か、打った後に止まった行で立ち上がったと読む。"""
    import clock
    import parallel_launch as plaunch

    since = clock.parse("2026-11-12T00:00:00Z", naive="utc").timestamp()
    old, new = "2026-11-11T23:00:00Z", "2026-11-12T00:00:00Z"
    assert plaunch.came_up({"state": "running"}, since, False) is True
    assert plaunch.came_up({"state": "awaiting_approval"}, since, False) is True
    assert plaunch.came_up({"state": "stopped", "at": new}, since, False) is True
    assert plaunch.came_up({"state": "stopped", "at": old}, since, False) is False
    assert plaunch.came_up({}, since, False) is False
    assert plaunch.came_up({"state": "running"}, since, True) is False  # fresh は状態を見ない
    assert plaunch.came_up({"state": "running", "at": new}, since, True) is True
    assert plaunch.came_up({"state": "stopped", "at": old}, since, True) is False


@pytest.mark.parametrize(
    ("row_at", "asks"),
    [
        ("2026-11-12T00:00:00Z", True),
        ("2026-11-11T23:59:59.500Z", True),  # 起動の記録の時刻 - 0.5 秒（境界を含む）
        ("2026-11-11T23:59:59.499Z", False),
        ("2026-11-11T23:00:00Z", False),
        (None, False),
    ],
)
def test_char_watch_verify_reads_the_stop_after_the_launch_with_half_a_second_margin(run, monkeypatch, row_at, asks):
    """現状固定: 版の要るまとめの一時レーンが立ち上がりの記録（`at`）- 0.5 秒以降に止まって観えたら、2 回続けて観たときに問う。"""
    asked = []
    monkeypatch.setattr(run, "ask", lambda kind, rid, lane, text, options, **kw: asked.append((kind, rid, lane, options, kw)))
    purpose = f"verify-{RID}"
    run.verifies = [{"id": RID, "lane": "la", "helper": "p1-rel1v", "purpose": purpose, "launch": {"argv": ["x"]}}]
    run.verifying = RID
    run.launches[purpose] = {"result": "up", "at": "2026-11-12T00:00:00Z"}
    rows = {"p1-rel1v": {"state": "stopped", "at": row_at}}
    run._watch_verify(rows)
    assert asked == [] and run.verifies[0].get("seen") == (1 if asks else None)
    run._watch_verify(rows)
    if not asks:
        assert asked == [] and "seen" not in run.verifies[0]
        return
    (got,) = asked
    assert got[:3] == ("helper", "p1-rel1v-verify-1", "p1-rel1v") and got[3] == [pend.RESUME, pend.STOP]
    assert got[4] == {"purpose": purpose, "launch": {"argv": ["x"]}, "verify": RID}
    assert run.verifies[0]["seen"] == 0 and run.verifies[0]["asked"] == 1


def test_char_watch_verify_ignores_a_launch_that_is_not_up_or_a_running_helper(run, monkeypatch):
    """現状固定: 起動の記録が up でない・一時レーンが止まって観えない・起動の記録の時刻が読めないときは数えない。"""
    asked = []
    monkeypatch.setattr(run, "ask", lambda *a, **kw: asked.append(a))
    purpose = f"verify-{RID}"
    run.verifies = [{"id": RID, "lane": "la", "helper": "p1-rel1v", "purpose": purpose, "launch": {}}]
    run.verifying = RID
    stopped = {"p1-rel1v": {"state": "stopped", "at": "2026-11-12T00:00:00Z"}}
    for rec, rows in (
        ({"result": "failed", "at": "2026-11-12T00:00:00Z"}, stopped),
        ({"result": "up", "at": "2026-11-12T00:00:00Z"}, {"p1-rel1v": {"state": "running", "at": "2026-11-12T00:00:00Z"}}),
        ({"result": "up"}, stopped),
    ):
        run.launches[purpose] = rec
        run._watch_verify(rows)
        run._watch_verify(rows)
    assert asked == [] and "seen" not in run.verifies[0]


def test_stop_waits_for_the_verify_helper_and_force_stops_it(run, monkeypatch):
    stopped: list[list[str]] = []
    monkeypatch.setattr(prun, "step_stop_lanes", lambda _m, names: stopped.append(list(names)) or [])
    monkeypatch.setattr(prun, "step_finish", lambda *_a: None)
    monkeypatch.setattr(run, "launch_once", lambda *_a, **_k: {})
    request(run)
    end(run)
    run.answer(RID, "released", "1.2.3", 1)
    run.launch_verify()
    run.stop = True
    assert prun._TICK[0][1](run, "/m", "p1", 0) is None and stopped == []  # verified が届くまで待つ
    run.force = True
    assert prun._TICK[0][1](run, "/m", "p1", 0)["status"] == "stopped"
    assert "p1-rel1v" in stopped[-1]  # --force は版の要るまとめの一時レーンも止める


# ---- 差し戻しと失敗（B4・I9） ----


def test_a_rejection_after_the_end_record_is_asked_without_waking_the_lane(run):
    run.lanes["la"].update(started=True, state="stopped")
    end(run, request=RID)
    run.answer(RID, "rejected", None, 1, "利用者が差し戻した")
    (q,) = kinds(run, "after_end")
    assert q["lane"] == "la" and q["result"] == "rejected" and "lane p1 add" in q["commands"]["レーンを足して直す"]
    run.reply({"id": q["id"], "choice": ["見送る"]})
    assert run.wake == [] and run.revive == []


def test_a_rejection_settled_before_the_end_record_is_taken_is_asked(run):
    run.answer(RID, "rejected", None, 1, "差し戻し")
    end(run, request=RID)
    assert [q["lane"] for q in kinds(run, "after_end")] == ["la"]


def test_an_old_request_rejected_after_a_new_one_ended_the_lane_is_not_asked(run):
    end(run, request="la-r000000000009")
    run.answer(RID, "rejected", None, 1, "古い依頼")
    assert kinds(run, "after_end") == []


def test_a_rejection_is_not_woken_into_an_ended_lane(run):
    run.lanes["la"].update(started=True, state="stopped")
    run.lanes["lb"].update(started=True, state="running")
    end(run, request=RID)
    run.wake_answered([{"id": RID, "lane": "la"}])
    assert run.wake == []


# ---- 終わりの記録の無い停止（移行性・決定 10） ----


def test_a_long_stop_without_an_end_record_is_asked_and_can_be_counted(run, monkeypatch):
    halts(monkeypatch, [{"result": "failed", "code": 1, "state": "running"}])
    run.lanes["la"].update(started=True, state="stopped")
    run.seen_at = time.time()
    old = stamp(run.seen_at - 3 * 3600)
    run.observe_ends({"la": {"state": "stopped", "at": old}})
    (q,) = kinds(run, "unended")
    run.observe_ends({"la": {"state": "stopped", "at": old}})
    assert len(kinds(run, "unended")) == 1  # 区切りごとに 1 回
    run.reply({"id": q["id"], "choice": ["終わったと数える"]})
    assert run.lanes["la"]["ended"]["by"] == "organizer"
    run.observe_ends({"la": {"state": "stopped", "at": old}})
    assert run.lanes["la"]["halt"]["result"] == "stopped" and run.counted(run.stopped()) == {"la"}  # 止める 1 行は打たない


def test_reviving_an_unended_lane_puts_it_into_the_wake(run):
    run.lanes["la"].update(started=True, state="stopped")
    run.seen_at = time.time()
    run.observe_ends({"la": {"state": "stopped", "at": stamp(run.seen_at - 3 * 3600)}})
    (q,) = kinds(run, "unended")
    run.reply({"id": q["id"], "choice": ["起こし直す"]})
    assert run.wake == ["la"] and "ended" not in run.lanes["la"]


def test_a_lane_waiting_for_its_request_is_not_asked(run):
    run.lanes["la"].update(started=True, state="stopped")
    request(run)
    run.seen_at = time.time()
    run.observe_ends({"la": {"state": "stopped", "at": stamp(run.seen_at - 3 * 3600)}})
    assert kinds(run, "unended") == []


# ---- 承認した提示で確定する（C1・I5） ----


def test_a_new_record_confirms_the_first_presentation_even_if_the_head_moved(run, monkeypatch):
    monkeypatch.setattr(prun, "step_presented", lambda _d: {"sha": "sha1", "prs": [10]})
    monkeypatch.setattr(prun, "step_head", lambda _m: (_ for _ in ()).throw(AssertionError("先頭を読まない")))
    monkeypatch.setattr(prun, "step_build", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("提示 2 を組まない")))
    monkeypatch.setattr(run, "launch_helper", lambda: None)
    rel = {"n": 1, "state": "presented", "version": "1.2.3", "dirs": ["/r/1/1"], "requests": [RID]}
    run.releases, run.current = [rel], rel
    run.confirm(rel, None)
    assert rel["state"] == "confirmed" and rel["k"] == 1 and rel["sha"] == "sha1" and len(rel["dirs"]) == 1


# ---- 副命令 end と verified（A1・B4・F7） ----


def _out(capsys, fn, a):
    with pytest.raises(SystemExit) as ex:
        fn(a)
    return ex.value.code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.fixture
def cli(tmp_path, monkeypatch):
    import parallel_external as pe
    import parallel_lane as pl

    sent: list[tuple] = []
    monkeypatch.setattr(pe, "organizer_dir", lambda: None)
    monkeypatch.setattr(pend, "_home_running", lambda *_a: tmp_path)
    monkeypatch.setattr(pl, "deliver_message", lambda main, name, msg, topic, key: sent.append((name, msg, key)))
    return {"main": tmp_path, "sent": sent, "pl": pl, "pe": pe}


def test_end_writes_the_end_record_with_the_request(cli, capsys, monkeypatch):
    rec = cli["main"] / "request.txt"
    rec.write_text(json.dumps({"id": RID, "plan": "p1", "lane": "la"}))
    monkeypatch.setattr(cli["pl"], "read_answer", lambda *_a, **_k: (None, None))
    code, res = _out(capsys, pend.cmd_end, argparse.Namespace(name="p1", lane="la", request=str(rec)))
    assert code == 0 and res["summary"] == "[ndf-parallel] lane-end p1 la"
    ((name, msg, key),) = cli["sent"]
    assert (name, msg["kind"], msg["lane"], msg["request"], key) == ("p1", "lane_end", "la", RID, "lane-end:la")
    assert plg.message_key(msg, "inbox") == ("lane_end", "lane_end:la")


def test_end_returns_a_gate_without_writing_when_the_answer_is_rejected(cli, capsys, monkeypatch):
    rec = cli["main"] / "request.txt"
    rec.write_text(json.dumps({"id": RID}))
    monkeypatch.setattr(cli["pl"], "read_answer", lambda *_a, **_k: ({"result": "rejected", "reason": "差し戻し", "release": 1}, None))
    code, res = _out(capsys, pend.cmd_end, argparse.Namespace(name="p1", lane="la", request=str(rec)))
    assert code == 10 and res["status"] == "gate" and cli["sent"] == []


def test_end_of_an_external_lane_writes_nothing(cli, capsys, monkeypatch):
    monkeypatch.setattr(cli["pe"], "organizer_dir", lambda: Path("/x"))
    code, res = _out(capsys, pend.cmd_end, argparse.Namespace(name="--external", lane="la", request=None))
    assert code == 0 and res["metrics"]["written"] is False and cli["sent"] == []


def test_verified_returns_the_task_and_then_sends_the_result(cli, capsys):
    main = cli["main"]
    msg = {"kind": "request", "id": RID, "lane": "la", "verify_plan": "/w/sprint-s1/lane-verify.json"}
    plg.append_ledger_row(main, "p1", "request", f"request:{RID}", msg, by="la")
    code, res = _out(capsys, pend.cmd_verified, argparse.Namespace(name="p1", id=RID, done=None))
    assert code == 0 and "/w/sprint-s1/lane-verify.json --done" in res["metrics"]["pipeline"]
    assert res["metrics"]["verified"].endswith(f"verified p1 {RID} --done {main}/.ndf/parallel/p1/verify/{RID}/done.json")
    done = main / "done.json"
    done.write_text(json.dumps({"items": [{"result": "完了"}, {"result": "完了"}]}))
    code, res = _out(capsys, pend.cmd_verified, argparse.Namespace(name="p1", id=RID, done=str(done)))
    assert code == 0 and cli["sent"][-1][1] == {**cli["sent"][-1][1], "kind": "verified", "id": RID, "result": "完了"}


def test_verified_of_a_rebuilt_request_uses_the_plan_of_its_origin(cli, capsys):
    msg = {"kind": "request", "id": RID, "lane": "la", "verify_plan": "/w/sprint-s1/lane-verify.json"}
    plg.append_ledger_row(cli["main"], "p1", "request", f"request:{RID}", msg, by="la")
    code, res = _out(capsys, pend.cmd_verified, argparse.Namespace(name="p1", id=f"{RID}-again2", done=None))
    assert code == 0 and "/w/sprint-s1/lane-verify.json --done" in res["metrics"]["pipeline"]


def test_verified_without_a_plan_stops(cli, capsys):
    code, _ = _out(capsys, pend.cmd_verified, argparse.Namespace(name="p1", id=RID, done=None))
    assert code == 1
