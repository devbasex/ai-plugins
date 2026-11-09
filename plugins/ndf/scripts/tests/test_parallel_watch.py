"""並列の計画の見回りの判定（#2649）: 今の起動・再起動の試み・開発ループの記録と照らして問い、前提が消えた問いを取り下げる。

前半は `_Run` を耐久ワークフローの外で組み、観測の値を直接渡して判定を確かめる。中ほどはリレーハブの記録から
再起動の試みを読む形、後半は版の印の無い観測で記録した計画を新しいコードで再開する形を確かめる。
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_parallel_run as tpr  # noqa: E402
from test_parallel_run import SCRIPTS, event, questions, until  # noqa: E402

import durable  # noqa: E402
import parallel_answer as pa  # noqa: E402
import parallel_lane as pl  # noqa: E402
import parallel_reply  # noqa: E402
import parallel_run as prun  # noqa: E402
import parallel_watch as pw  # noqa: E402
from relay_lib import hub as hb  # noqa: E402
from relay_lib import hub_client as hc  # noqa: E402

env = tpr.env
LIMIT = 3600.0
T0 = 1_800_000_000.0
HELPER = "p1-rel1"


def iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()


@pytest.fixture
def run(monkeypatch):
    """版の印のある観測を受けた `_Run`。イベントは捨て、ループの待ちの記録は無いものとする。"""
    monkeypatch.setattr(durable, "set_event", lambda *_a, **_k: None)
    monkeypatch.setattr(parallel_reply, "step_waiting_lanes", lambda *_a: [])
    lanes = [
        {"name": "la", "input": {"kind": "loop", "loop": "la"}},
        {"name": "lb", "input": {"kind": "loop", "loop": "lb"}, "after": ["la"]},
        {"name": "ls", "input": {"kind": "sprint"}},
    ]
    r = prun._Run("/m", "p1", lanes)
    r.judging = True
    for n in ("la", "ls"):
        r.lanes[n].update(started=True, state="running")
    return r


def stalls(r) -> list[dict]:
    return [q for q in r.questions if q.get("kind") == "stall"]


def inboxes(monkeypatch, **topics):
    """`durable.recv` を話題ごとの列で差し替える。読んだ数を返す。"""
    box = {k: list(v) for k, v in topics.items()}
    read = {"n": 0}

    def recv(topic, _timeout):
        read["n"] += 1
        return box.get(topic, []).pop(0) if box.get(topic) else None

    monkeypatch.setattr(durable, "recv", recv)
    return read


# ---- 一時レーンの途中停止（受け入れ条件 1〜6） ----


def releasing(r, launched: float | None = T0):
    r.current = {"n": 1, "prs": [10], "dirs": ["/d"]}
    r.launch = {"seq": 3, "action": "release", "spawn": "SPAWN", **({"at": launched} if launched is not None else {})}


def helpers(r) -> list[dict]:
    return [q for q in r.questions if q.get("kind") == "helper"]


def test_a_helper_row_from_before_the_launch_is_not_asked(run, monkeypatch):
    """行の時刻が今の起動の指示以前なら問わず、インボックスも読まない（受け入れ条件 1・I1）。"""
    read = inboxes(monkeypatch)
    releasing(run)
    for at in (T0 - 60, T0):
        pw.ask_helper(run, {HELPER: {"state": "stopped", "at": iso(at)}})
    assert helpers(run) == [] and read["n"] == 0


def test_a_helper_row_without_a_time_is_not_asked(run, monkeypatch):
    """時刻を持たない stopped の行では、起動の時刻が無くても問わない（受け入れ条件 2）。"""
    inboxes(monkeypatch)
    for launched in (T0, None):
        releasing(run, launched)
        pw.ask_helper(run, {HELPER: {"state": "stopped"}})
    assert helpers(run) == []


def test_a_gate2_request_behind_other_messages_stops_the_question(run, monkeypatch):
    """依頼が他のメッセージ 2 件の後ろに並んでいても、読み切ってから判定し直すため問わない（受け入れ条件 3・I2）。"""
    others = [{"kind": "ask", "id": f"o{i}", "lane": "la", "question": f"別の問い {i}", "options": ["はい"]} for i in range(2)]
    gate2 = {"kind": "gate2", "id": "g1", "lane": HELPER, "n": 1, "question": "リリース承認ゲート", "options": ["承認"]}
    inboxes(monkeypatch, inbox=[*others, gate2])
    releasing(run)
    pw.ask_helper(run, {HELPER: {"state": "stopped", "at": iso(T0 + 5)}})
    assert helpers(run) == [] and [q["id"] for q in run.questions] == ["o0", "o1", "g1"]


def test_a_settle_waiting_in_resume_stops_the_question(run, monkeypatch):
    """settle が `resume` にあれば、読み切りでリリースが締まり問わない（受け入れ条件 4）。"""
    settle = {"n": 1, "phase": "settle", "result": "released", "version": "1.0.0"}
    inboxes(monkeypatch, resume=[settle])
    releasing(run)
    run.releases = [run.current]
    pw.ask_helper(run, {HELPER: {"state": "stopped", "at": iso(T0 + 5)}})
    assert helpers(run) == [] and run.current is None and run.releases[0]["state"] == "released"


def test_a_helper_that_sent_nothing_is_asked_once_in_the_same_round(run, monkeypatch):
    """何も送らずに止まった一時レーンは、止まった行を読んだ見回りで 1 件だけ問う（受け入れ条件 5・6・I3）。"""
    inboxes(monkeypatch)
    releasing(run)
    for _ in range(3):
        pw.ask_helper(run, {HELPER: {"state": "stopped", "at": iso(T0 + 5)}})
    assert len(helpers(run)) == 1 and helpers(run)[0]["commands"] == {"続きから起こし直す": "SPAWN"}


def test_the_launch_time_is_recorded_only_after_a_judging_observation(run, monkeypatch):
    """起動の指示の時刻は版の印のある観測の後だけ耐久ステップで取る（決定 2・I8）。"""
    monkeypatch.setattr(prun, "step_clock", lambda: T0)
    run.pause("release", n=1, k=1, spawn="S")
    assert run.launch["at"] == T0
    run.judging = False
    run.pause("release", n=1, k=1, spawn="S")
    assert "at" not in run.launch


# ---- 停滞の問い（受け入れ条件 7〜12） ----


def test_a_stall_names_the_last_move_and_the_restart_attempts(run):
    """本文に最後に動いた時刻・その後の再起動の試みの回数・最後の結果が載る。断られた試みは経過の起点にしない（受け入れ条件 7・I4）。"""
    tries = [{"at": iso(T0 - 3900), "result": "started"}, {"at": iso(T0 - 3800), "result": "limit"}]
    pw.ask_stalls(run, {"la": {"state": "running", "at": iso(T0 - 4000), "restarts": tries}}, T0, LIMIT)
    (q,) = stalls(run)
    assert iso(T0 - 4000) in q["question"] and "試み 2 回" in q["question"] and "断られた（limit）" in q["question"]
    assert "65 分" in q["question"] and q["asked_at"] == T0


def test_a_stall_without_attempts_says_zero(run):
    pw.ask_stalls(run, {"la": {"state": "running", "at": iso(T0 - 4000), "restarts": []}}, T0, LIMIT)
    assert "試み 0 回" in stalls(run)[0]["question"]


def test_a_recent_successful_restart_holds_the_stall(run):
    """起動できた再起動から区切り未満なら、一覧の時刻が古くても問わない（受け入れ条件 9）。"""
    tries = [{"at": iso(T0 - 100), "result": "started"}]
    pw.ask_stalls(run, {"la": {"state": "running", "at": iso(T0 - 4000), "restarts": tries}}, T0, LIMIT)
    assert stalls(run) == []


def test_a_stall_is_withdrawn_when_the_lane_moves(run):
    """一覧の時刻が問いを出した時刻より進むと外れ、答えても何も起きない（受け入れ条件 10・11・I5）。"""
    pw.ask_stalls(run, {"la": {"state": "running", "at": iso(T0 - 4000)}}, T0, LIMIT)
    rid = stalls(run)[0]["id"]
    pw.withdraw_settled(run, {"la": {"state": "running", "at": iso(T0 - 1)}}, {})
    assert stalls(run) != []
    pw.withdraw_settled(run, {"la": {"state": "running", "at": iso(T0 + 1)}}, {})
    assert stalls(run) == [] and rid in run.answered
    before = json.dumps(run.lanes, sort_keys=True)
    run.reply({"id": rid, "result": "answered", "choice": ["起こし直す"]})
    assert run.held == {} and run.revive == [] and run.wake == [] and json.dumps(run.lanes, sort_keys=True) == before


def test_an_answer_to_a_withdrawn_question_warns_and_returns_no_line():
    """取り下げた問いへの `parallel.py answer` は警告を返し、次に打つ 1 行を返さない（受け入れ条件 11）。"""
    assert "取り下げ済み" in pa.not_listed("la-s1", [])["warning"]
    assert pa.owner_next(Path("/m"), "p1", "la-s1", ["起こし直す"], items=[]) == (None, [])


# ---- 再起動を断られたレーン（受け入れ条件 7・I10・I11） ----


def refused(r, at="2026-11-09T00:00:00.000Z", why="limit"):
    r.lanes["la"].update(started=iso(T0 - 9000), state="stopped")
    return {"la": {"state": "stopped", "at": iso(T0 - 100), "restarts": [{"at": at, "result": why}]}}


def test_a_refused_restart_is_asked_once_per_attempt(run):
    """再起動を断られて止まったレーンは、試みにつき 1 件だけ、起こし直す 1 行だけを持つ問いになる。"""
    rows = refused(run)
    pw.ask_stalls(run, rows, T0, LIMIT)
    (q,) = stalls(run)
    assert q["options"] == ["起こし直す", "止める"] and list(q["commands"]) == ["起こし直す"]
    assert "試み 1 回" in q["question"] and "断られた（limit）" in q["question"] and q["refused_at"] == rows["la"]["restarts"][0]["at"]
    run.reply({"id": q["id"], "result": "answered", "choice": ["止める"]})
    pw.ask_stalls(run, rows, T0 + LIMIT * 2, LIMIT)
    assert stalls(run) == []
    pw.ask_stalls(run, refused(run, at="2026-11-09T01:00:00.000Z"), T0, LIMIT)
    assert len(stalls(run)) == 1
    started = {**rows, "la": {**rows["la"], "restarts": [{"at": "x", "result": "started"}]}}
    run.questions = []
    pw.ask_stalls(run, started, T0, LIMIT)
    assert stalls(run) == []


def test_the_stop_answer_to_a_refused_restart_returns_no_line(run, monkeypatch):
    pw.ask_stalls(run, refused(run), T0, LIMIT)
    rid = stalls(run)[0]["id"]
    assert pa.owner_next(Path("/m"), "p1", rid, ["止める"], items=run.questions) == (None, [])
    assert pa.owner_next(Path("/m"), "p1", rid, ["起こし直す"], items=run.questions)[0].endswith("restart la")


def test_restart_answer_holds_dependents_and_stop_answer_releases_them(run, monkeypatch):
    """「起こし直す」は動き出すまで依存先の起動と計画の終わりを保留し、「止める」は保留を解く（I11）。"""
    spawned = []
    monkeypatch.setattr(prun, "step_spawn", lambda _m, _n, names: spawned.extend(names) or {n: {} for n in names})
    monkeypatch.setattr(prun, "step_waiting_lanes", lambda *_a: [])
    pw.ask_stalls(run, refused(run), T0, LIMIT)
    run.reply({"id": stalls(run)[0]["id"], "result": "answered", "choice": ["起こし直す"]})
    assert run.revive == ["la"] and run.wake == ["la"] and run.halted == []
    prun._spawn_ready(run, "/m")
    assert spawned == []
    pw.ask_stalls(run, refused(run, at="2026-11-09T02:00:00.000Z"), T0, LIMIT)
    run.reply({"id": stalls(run)[0]["id"], "result": "answered", "choice": ["止める"]})
    assert run.revive == [] and run.wake == [] and run.halted == ["la"]
    prun._spawn_ready(run, "/m")
    assert spawned == ["lb"]


def test_a_halted_lane_with_a_waiting_record_does_not_hold_the_plan(run, monkeypatch):
    """待ちの記録が残るレーンでも「止める」の後は依存先が起動し、計画が終わる。動き出すと保留の判定へ戻る（I11）。"""
    spawned, finished = [], []
    monkeypatch.setattr(prun, "step_spawn", lambda _m, _n, names: spawned.extend(names) or {n: {} for n in names})
    monkeypatch.setattr(prun, "step_waiting_lanes", lambda *_a: ["la"])
    monkeypatch.setattr(prun, "step_design_held", lambda *_a: ["la"])
    monkeypatch.setattr(prun, "step_held_loops", lambda *_a: [])
    monkeypatch.setattr(prun, "step_finish", lambda *_a: finished.append(True))
    run.lanes["la"]["state"], run.halted = "stopped", ["la"]
    prun._spawn_ready(run, "/m")
    assert spawned == ["lb"]
    assert prun._finish_or_hold(run, "/m", "p1")["status"] == "finished" and run.wake == []
    pw.release_halted(run, {"la": {"state": "running"}})
    assert run.halted == [] and prun._finish_or_hold(run, "/m", "p1") is None


# ---- 開発ループが止まったレーン（受け入れ条件 13〜16・I6・I7） ----


def stopped_loop(at: float = T0 - 10, condition: str = "same_failure", lane: str = "la") -> dict:
    return {lane: {"loop": lane, "condition": condition, "at": iso(at)}}


def look(r, loops: dict, now: float, state: str = "running", at: float | None = None):
    rows = {"la": {"state": state, "at": iso(now - 5 if at is None else at), "restarts": []}}
    pw.watch(r, {"loops": loops}, rows, now, LIMIT)


def test_a_stopped_loop_on_a_running_lane_is_asked_at_once(run):
    """区切りを待たずに 1 件問い、本文に止まった条件が載る（受け入れ条件 13）。"""
    look(run, stopped_loop(), T0)
    look(run, stopped_loop(), T0 + 60)
    (q,) = stalls(run)
    assert "same_failure" in q["question"] and q["loop_stop"] == {"loop": "la", "condition": "same_failure"}


def test_a_loop_that_stopped_before_the_current_start_is_not_asked(run):
    """記録の止まった時刻が今の起動以前なら問わず、後なら問う（I6）。"""
    run.lanes["la"]["started"] = iso(T0 - 10)
    look(run, stopped_loop(T0 - 10), T0)
    assert stalls(run) == []
    look(run, stopped_loop(T0 - 9), T0)
    assert len(stalls(run)) == 1


def test_waiting_only_sprint_and_running_records_are_not_asked(run):
    """`waiting_only`・スプリントのレーン・止まっていない記録には問わない（受け入れ条件 15）。"""
    look(run, stopped_loop(condition="waiting_only"), T0)
    look(run, {}, T0)
    rows = {"ls": {"state": "running", "at": iso(T0 - 5)}}
    pw.watch(run, {"loops": stopped_loop(lane="ls")}, rows, T0, LIMIT)
    assert stalls(run) == []


def test_a_loop_stop_is_withdrawn_by_the_record_or_the_state_but_not_by_the_time(run):
    """記録が戻るか行が running でなくなると外れ、一覧の時刻が進むだけでは外れない（受け入れ条件 16）。"""
    look(run, stopped_loop(), T0)
    look(run, stopped_loop(), T0 + 30, at=T0 + 20)
    assert len(stalls(run)) == 1
    look(run, {}, T0 + 60)
    assert stalls(run) == []
    look(run, stopped_loop(T0 + 70), T0 + 80)
    assert len(stalls(run)) == 1
    look(run, stopped_loop(T0 + 70), T0 + 90, state="stopped")
    assert stalls(run) == []


def test_waiting_after_a_loop_stop_holds_both_questions_until_the_next_limit(run):
    """t=0 に問い t=4200 に「待つ」と答えると、t=7799 まで同じレーンにどちらの問いも立たず、t=7800 に問える（受け入れ条件 14・I7）。"""
    look(run, stopped_loop(), T0)
    look(run, stopped_loop(), T0 + 4100, at=T0 - LIMIT * 3)
    assert len(stalls(run)) == 1  # 未回答の間は重ならない
    run.seen_at = T0 + 4200
    run.reply({"id": stalls(run)[0]["id"], "result": "answered", "choice": ["待つ"]})
    look(run, stopped_loop(), T0 + 7799, at=T0 - LIMIT * 3)
    assert stalls(run) == []
    look(run, stopped_loop(), T0 + 7800, at=T0 - LIMIT * 3)
    (q,) = stalls(run)
    assert q.get("loop_stop")


def test_a_loop_that_resumes_and_stops_again_is_asked_before_the_limit(run):
    """答えの後に記録が running へ戻り、期限の前に再び止まると、次の見回りで問う（受け入れ条件 13・14）。"""
    look(run, stopped_loop(), T0)
    run.seen_at = T0 + 10
    run.reply({"id": stalls(run)[0]["id"], "result": "answered", "choice": ["待つ"]})
    look(run, {}, T0 + 20)
    look(run, stopped_loop(T0 + 25), T0 + 30)
    assert len(stalls(run)) == 1


def test_a_refused_restart_after_a_loop_stop_answer_is_asked_within_the_hold(run):
    """開発ループの止まりへ「起こし直す」と答えた後、再起動が断られると期限の内でも断りの問いが立つ（I7・I10）。"""
    look(run, stopped_loop(), T0)
    run.seen_at = T0 + 10
    run.reply({"id": stalls(run)[0]["id"], "result": "answered", "choice": ["起こし直す"]})
    rows = refused(run)
    pw.watch(run, {"loops": stopped_loop()}, rows, T0 + 60, LIMIT)
    (q,) = stalls(run)
    assert q.get("refused_at") and prun._run_finished(run) is False


def test_without_the_mark_the_new_judgments_make_no_calls(run, monkeypatch):
    """版の印の無い観測では、取り下げ・開発ループの止まり・読み切りを通らない（I8）。"""
    read = inboxes(monkeypatch)
    run.judging = False
    pw.ask_stalls(run, {"la": {"state": "running", "at": iso(T0 - 4000)}}, T0, LIMIT)
    rid = stalls(run)[0]["id"]
    look(run, stopped_loop(), T0 + 60, at=T0 + 50)
    releasing(run, None)
    pw.ask_helper(run, {HELPER: {"state": "stopped"}})
    assert [q["id"] for q in stalls(run)] == [rid] and len(helpers(run)) == 1 and read["n"] == 0


# ---- リレーハブの記録の再起動の試み（I9） ----


@pytest.fixture
def relay_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("NDF_RELAY_TMUX", "ndf-no-tmux")
    monkeypatch.setenv("NDF_RELAY_LAUNCH_CHECK", "0")
    monkeypatch.delenv("NDF_RELAY_MAX_NAMED", raising=False)
    monkeypatch.setattr(hb.tmux, "binary", lambda: "/bin/true")
    monkeypatch.setattr(hb.tmux, "session_gone", lambda _n: False)
    (tmp_path / "state" / "ndf" / "relay").mkdir(parents=True)
    hb.ensure_dirs()


def test_each_restart_attempt_appears_once(relay_state, monkeypatch, capsys):
    """起動できた回・リレーハブが断った回・リレーハブへ届かなかった回がそれぞれ 1 件ずつ並ぶ。起動は数えず、古い行の
    `restarted` は数え、読めない行は飛ばす。"""
    failed = {"why": None}
    monkeypatch.setattr(hb.tmux, "new_session", lambda *_a: failed["why"])
    hc.put_request("spawn", "a", "/", [], "block:/x")
    hb.Hub(None).serve_once()
    hc.put_request("restart", "a", "/", [], "restart")
    hb.Hub(None).serve_once()
    failed["why"] = "tmux が落ちた"
    hc.put_request("restart", "a", "/", [], "restart")
    hb.Hub(None).serve_once()
    monkeypatch.setattr(hb, "hub_running", lambda: False)
    monkeypatch.setattr(hc, "stop_named", lambda _n: True)
    assert hc.cmd_restart(["a", "--json"]) != 0
    capsys.readouterr()
    with open(hb.hub_path(hb.HUB_LOG), "a", encoding="utf-8") as f:
        f.write("壊れた行\n")
        f.write(json.dumps({"at": "2026-01-01T00:00:00.000Z", "event": "restarted", "name": "a", "reason": None}) + "\n")
        f.write(json.dumps({"at": "2026-01-01T00:00:01.000Z", "event": "refused", "name": "a", "reason": "limit"}) + "\n")
    got = [t["result"] for t in hc.restart_attempts()["a"]]
    assert got == ["started", hb.TMUX_FAILED, "no-hub", "started"]


def test_ls_lists_only_attempts_after_the_row_time():
    tries = [{"at": "2026-11-09T00:00:00.000Z", "result": "started"}, {"at": "2026-11-09T01:00:00.000Z", "result": "limit"}]
    assert hc._restarts_after(tries, "2026-11-09T00:30:00.000Z") == tries[1:]
    assert hc._restarts_after(tries, None) == tries


# ---- 版の印の無い記録の再開（受け入れ条件 19・I8） ----

OLD = textwrap.dedent(
    """
    import os, sys, time
    from pathlib import Path
    main = Path(sys.argv[1])
    sys.path[:0] = [sys.argv[2], sys.argv[2] + "/lib"]
    import dbos
    import durable, parallel_lane as pl, parallel_release as prl, parallel_run as prun
    build = prl.build_plans
    prl.build_plans = lambda m, n, s, k, v, builder_=None: build(m, n, s, k, v, lambda *_a: ({"steps": []}, {"steps": []}))
    prun.dev_version = lambda _m: "1.2.3"
    prun.git_head = lambda _m: "sha1"
    new = prun.step_observe.__wrapped__

    @durable.step(name="parallel.observe")
    def old_observe(main, name):  # 版の印・止まった開発ループ・再起動の試みを持たない版の観測
        got = new(main, name)
        for k in ("can_judge", "loops"):
            got.pop(k, None)
        got["rows"] = {n: {k: v for k, v in r.items() if k != "restarts"} for n, r in got["rows"].items()}
        return got

    prun.step_observe = old_observe

    def absent(*_a, **_k):  # 古い版に無い呼び出し。版の印を見ずに呼ぶと記録が古い版と違ってしまう
        raise SystemExit("古い版に無い呼び出し")

    import parallel_watch as pw
    prun.step_clock = prun._Run.drain = pw.withdraw_settled = pw.ask_loop_stops = absent
    prun.open_parallel_record(main, "p1")
    wid = pl.workflow_id("p1")
    durable.start(durable.WorkflowRef(wid, 1, "start"), prun.parallel_workflow, str(main), "p1")
    pl.send_release_request(main, "p1", "la", [10], [1], main / "st")
    end = time.monotonic() + 30
    while time.monotonic() < end:
        items = (durable.event(wid, "questions") or {}).get("items") or []
        if any(q.get("kind") == "helper" for q in items):
            time.sleep(0.5)
            os._exit(0)
        time.sleep(0.1)
    os._exit(1)
    """
)


def test_a_record_without_the_mark_replays_and_judges_from_the_next_round(env, tmp_path, monkeypatch):
    """版の印の無い観測で記録した計画（起動の指示・一時レーンの途中停止の問いを含む）を新しいコードで再開しても落ちず、
    次の見回りから取り下げが働く（受け入れ条件 19）。"""
    main, states = env["main"], env["states"]
    monkeypatch.setenv("NDF_PARALLEL_STALL_MINUTES", "1")
    states.write_text(json.dumps({"la": "running", HELPER: {"state": "stopped", "role": "helper", "at": iso(time.time() + 3)}}))
    (tmp_path / "old.py").write_text(OLD)
    got = subprocess.run([sys.executable, str(tmp_path / "old.py"), str(main), str(SCRIPTS)], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, got.stderr[-2000:]
    rows = {"la": {"state": "running", "at": iso(time.time() - 600)}, HELPER: {"state": "stopped", "role": "helper"}}
    states.write_text(json.dumps(rows))
    prun.open_parallel_record(main, "p1")
    stall = until(lambda: [q for q in questions(main)["items"] if q["kind"] == "stall"])[0]
    assert [q["kind"] for q in questions(main)["items"]].count("helper") == 1
    rows["la"]["at"] = iso(time.time() + 5)
    states.write_text(json.dumps(rows))
    until(lambda: all(q["id"] != stall["id"] for q in questions(main)["items"]))
    assert durable.status(pl.workflow_id("p1")) in pl.RUNNING and event(main, "p1", "pause")
