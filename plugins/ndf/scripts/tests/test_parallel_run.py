"""並列の進行の耐久ワークフロー（#2436 の受け入れ条件 4・6・8・11・12、I7・I9・I10・I11・I14・I15）。

本物の DBOS（SQLite。置き場は根の conftest.py が `NDF_DBOS_DIR` へ向ける）で流し、統括役の CLI は
`NDF_PARALLEL_RELAY` の差し替え（呼び出しを記録し、`ls` の状態をファイルから返す）で、git とリリースプランの作りは
monkeypatch で差し替える。
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import dbos  # noqa: E402,F401
import durable  # noqa: E402
import parallel_answer as pa  # noqa: E402
import parallel_lane as pl  # noqa: E402
import parallel_plan as pp  # noqa: E402
import parallel_release as prl  # noqa: E402
import parallel_run as prun  # noqa: E402

PLAN = {
    "version": 1,
    "name": "p1",
    "rules": [],
    "lanes": [
        {"name": "la", "input": {"kind": "sprint", "issues": [1]}, "plans": 1, "touches": ["a/"]},
        {"name": "lb", "input": {"kind": "sprint", "issues": [2]}, "plans": 1, "touches": ["b/"], "after": ["la"]},
    ],
}

FAKE = textwrap.dedent(
    """
    import json, os, sys
    log, states = os.environ["FAKE_LOG"], os.environ["FAKE_STATES"]
    with open(log, "a") as f:
        f.write(json.dumps(sys.argv[1:]) + "\\n")
    sub = sys.argv[1]
    if sub == "ls" and os.path.exists(os.environ.get("FAKE_LS_FAIL", "")):
        print("ls: hub に届かない", file=sys.stderr)
        sys.exit(3)
    if sub == "ls":
        st = json.load(open(states)) if os.path.exists(states) else {}
        rows = [{"name": n, **(s if isinstance(s, dict) else {"state": s})} for n, s in st.items()]
        print(json.dumps({"items": rows, "metrics": {"hub": True, "tmux": True}}))
    elif sub == "spawn" and sys.argv[3] in os.environ.get("FAKE_REFUSE", "").split():
        print(json.dumps({"items": [{"result": "limit"}], "metrics": {}}))
        sys.exit(1)
    else:
        print(json.dumps({"items": [{"result": "started"}], "metrics": {}}))
    """
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    main = tmp_path / "repo"
    (main / ".ndf").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    plan = pp.check_data(PLAN)[0]
    pp.write_all(main, plan, PLAN)
    fake = tmp_path / "fake_relay.py"
    fake.write_text(FAKE)
    log, states = tmp_path / "relay.log", tmp_path / "states.json"
    monkeypatch.setenv("NDF_PARALLEL_RELAY", f"{sys.executable} {fake}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_STATES", str(states))
    monkeypatch.setenv("FAKE_LS_FAIL", str(tmp_path / "ls.fail"))
    monkeypatch.setenv("NDF_PARALLEL_POLL", "0.2")
    heads = {"v": "sha1"}
    monkeypatch.setattr(prun, "git_head", lambda _m: heads["v"])
    monkeypatch.setattr(prun, "dev_version", lambda _m: "1.2.3")

    def builder(_main, _name, d, _v):
        return {"steps": []}, {"steps": []}

    monkeypatch.setattr(prl, "build_plans", lambda m, n, s, k, v, builder_=None: _build(m, n, s, k, v, builder))
    yield {"main": main, "log": log, "states": states, "heads": heads}
    _stop_running(main)
    durable.close()


def _stop_running(main):
    """テストが残した耐久ワークフローを止め、スレッドが抜けるのを待つ。

    close() は DBOS を止めてもワークフローのスレッドを止めない。残すと次のテストの同じ ID（parallel-p1）の起動が
    重複として打ち切られ、前のテストのスレッドが次のテストの記録を読み書きする。"""
    wid = pl.workflow_id("p1")
    try:
        if durable.status(wid) not in pl.RUNNING:
            return
        durable.send_to(pl.KIND, pl.record_identity(main, "p1"), wid, {"kind": "stop", "force": True}, "inbox", f"{wid}:inbox:stop:force")
        end = time.monotonic() + 15
        while time.monotonic() < end and durable.status(wid) in pl.RUNNING:
            time.sleep(0.1)
    except Exception:  # 後片付けの失敗でテストの結果を変えない
        pass


def _build(m, n, s, k, v, builder):
    return prl.__dict__["_orig_build"](m, n, s, k, v, builder)


prl._orig_build = prl.build_plans


def calls(log: Path, sub: str) -> list[list[str]]:
    return [c for c in (json.loads(ln) for ln in log.read_text().splitlines()) if c[0] == sub] if log.exists() else []


def event(main, name, key):
    return durable.event(pl.workflow_id(name), key)


def until(pred, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = pred()
        if got:
            return got
        time.sleep(0.1)
    raise AssertionError("条件が成り立たないまま上限に達した")


def start(main):
    prun.open_parallel_record(main, "p1")
    durable.start(durable.WorkflowRef(pl.workflow_id("p1"), 1, "start"), prun.parallel_workflow, str(main), "p1")


def spawned(log):
    return [c[2] for c in calls(log, "spawn")]


def test_flow_spawns_in_order_releases_without_waiting_and_finishes(env, tmp_path):
    main, log, states = env["main"], env["log"], env["states"]
    start(main)
    until(lambda: spawned(log) == ["la"])  # lb は la が止まるまで起動しない（I8）
    states.write_text(json.dumps({"la": "running"}))
    # la の依頼 1 件で、他のレーンを待たずに提示 1 を組む（I9）
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release" and e)
    assert ev["n"] == 1 and ev["k"] == 1 and "--role helper" in ev["spawn"]
    d = prl.release_dir(main, "p1", 1, 1)
    (d / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10, 11]}))
    # 先頭が同じなら確定（I14）
    durable.send_to(pl.KIND, pl.record_identity(main, "p1"), pl.workflow_id("p1"), {"n": 1, "phase": "confirm"}, "resume", "c1")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed")
    durable.send_to(
        pl.KIND,
        pl.record_identity(main, "p1"),
        pl.workflow_id("p1"),
        {"n": 1, "phase": "settle", "result": "released", "version": "1.2.3"},
        "resume",
        "s1",
    )
    ans, _ = until(lambda: pl.read_answer(main, "p1", rid)[0] and pl.read_answer(main, "p1", rid))
    assert ans["result"] == "released" and ans["version"] == "1.2.3"
    assert event(main, "p1", "range-1")["prs"] == [10, 11]
    states.write_text(json.dumps({"la": "stopped"}))
    until(lambda: "lb" in spawned(log))
    states.write_text(json.dumps({"la": "stopped", "lb": "stopped"}))
    out = durable.output_of(pl.workflow_id("p1"))
    assert out["status"] == "finished" and out["releases"][0]["state"] == "released"
    assert spawned(log) == ["la", "lb"]  # 1 本のレーンは 1 回だけ起動する（I7）
    assert ["role", "--clear"] in calls(log, "role")


def test_moved_head_represents_once(env, tmp_path):
    main, heads = env["main"], env["heads"]
    start(main)
    pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    (prl.release_dir(main, "p1", 1, 1) / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10]}))
    heads["v"] = "sha2"
    send = lambda msg, key: durable.send_to(pl.KIND, pl.record_identity(main, "p1"), pl.workflow_id("p1"), msg, "resume", key)  # noqa: E731
    send({"n": 1, "phase": "confirm"}, "c1")
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("represent") and e)
    assert ev["k"] == 2
    (prl.release_dir(main, "p1", 1, 2) / "presented.json").write_text(json.dumps({"sha": "sha2", "prs": [10, 12]}))
    heads["v"] = "sha3"  # 出し直しへの答えでは比べない
    send({"n": 1, "phase": "confirm", "take": "first"}, "c2")
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed" and e)
    assert ev["k"] == 1 and ev["plan"].endswith("/1/1/release.json")


def test_stop_ends_workflow_and_stops_lanes(env):
    main, log = env["main"], env["log"]
    start(main)
    until(lambda: spawned(log) == ["la"])
    wid = pl.workflow_id("p1")
    durable.send_to(pl.KIND, pl.record_identity(main, "p1"), wid, {"kind": "stop"}, "inbox", f"{wid}:inbox:stop")
    out = durable.output_of(wid)
    assert out["status"] == "stopped" and ["stop", "la", "--json"] in calls(log, "stop")


def test_send_request_is_idempotent(env, tmp_path):
    main = env["main"]
    start(main)
    st = tmp_path / "st"
    a = pl.send_release_request(main, "p1", "la", [10], [1], st)
    b = pl.send_release_request(main, "p1", "la", [10], [1], st)
    assert a == b and (st / "work" / "request-id.txt").read_text().strip() == a
    until(lambda: (event(main, "p1", "lane-la") or {}).get("received") == 1)
    time.sleep(0.6)
    assert event(main, "p1", "lane-la")["received"] == 1


# ---- 現状固定（parallel_workflow の分岐。リファクタリングの前に今の振る舞いを記録する） ----


def resume(main, msg, key):
    durable.send_to(pl.KIND, pl.record_identity(main, "p1"), pl.workflow_id("p1"), msg, "resume", key)


def stop(main):
    wid = pl.workflow_id("p1")
    durable.send_to(pl.KIND, pl.record_identity(main, "p1"), wid, {"kind": "stop"}, "inbox", f"{wid}:inbox:stop")
    return durable.output_of(wid)


def test_char_invalid_plan_stops_without_spawning(env):
    main, log = env["main"], env["log"]
    (pp.plan_dir(main, "p1") / "plan.json").write_text(json.dumps({"version": 1}))
    start(main)
    out = durable.output_of(pl.workflow_id("p1"))
    assert out["status"] == "stopped" and out["lanes"] == {} and out["releases"] == [] and out["pending"] == []
    assert isinstance(out["errors"], list) and out["errors"]
    assert spawned(log) == [] and calls(log, "role") == []


def test_char_lane_missing_from_ls_counts_as_stopped_and_finishes(env):
    main, log = env["main"], env["log"]
    start(main)  # ls が何も返さないと、起動済みのレーンは止まったものとして扱う
    out = durable.output_of(pl.workflow_id("p1"))
    assert out["status"] == "finished" and spawned(log) == ["la", "lb"]
    assert {n: st["state"] for n, st in out["lanes"].items()} == {"la": "stopped", "lb": "stopped"}
    assert out["releases"] == [] and out["pending"] == []
    assert calls(log, "role")[0] == ["role", "organizer", "--plan", "p1"] and ["role", "--clear"] in calls(log, "role")


def test_char_awaiting_approval_pauses_with_notice(env):
    main, states = env["main"], env["states"]
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "running")
    states.write_text(json.dumps({"la": "awaiting_approval"}))
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "notice" and e)
    assert ev["lanes"] == ["la"] and ev["attach"] == [f"python3 {prun.RELAY} attach la"]
    out = stop(main)
    assert out["status"] == "stopped" and out["not_stopped"] == [] and out["lanes"]["la"]["state"] == "awaiting_approval"


def test_char_build_error_fails_release_and_stops(env, tmp_path, monkeypatch):
    main, log, states = env["main"], env["log"], env["states"]
    monkeypatch.setattr(prun, "dev_version", lambda _m: "")
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    out = durable.output_of(pl.workflow_id("p1"))
    assert out["status"] == "stopped" and out["not_stopped"] == [] and out["pending"] == [rid]
    assert [(r["n"], r["state"]) for r in out["releases"]] == [(1, "failed")] and "出す版を決められない" in out["releases"][0]["reason"]
    assert ["stop", "la", "--json"] in calls(log, "stop") and pl.read_answer(main, "p1", rid) == (None, None)


def test_char_rejected_settle_answers_with_reason(env, tmp_path):
    main, states = env["main"], env["states"]
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    (prl.release_dir(main, "p1", 1, 1) / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10]}))
    resume(main, {"n": 1, "phase": "confirm"}, "c1")
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed" and e)
    assert ev["n"] == 1 and ev["k"] == 1 and "--role helper" in ev["spawn"]
    resume(main, {"n": 1, "phase": "settle", "result": "rejected", "reason": "no"}, "s1")
    ans, _ = until(lambda: pl.read_answer(main, "p1", rid)[0] and pl.read_answer(main, "p1", rid))
    assert ans == {"result": "rejected", "version": None, "release": 1, "reason": "no", "at": None}
    out = stop(main)
    rel = out["releases"][0]
    assert (rel["state"], rel["reason"], rel["k"], rel["sha"], rel["prs"]) == ("rejected", "no", 1, "sha1", [10])
    assert out["pending"] == [] and event(main, "p1", "range-1") is None


def test_char_request_outside_range_is_deferred_to_next_release(env, tmp_path):
    main, states = env["main"], env["states"]
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    r1 = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "s1")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    r2 = pl.send_release_request(main, "p1", "la", [20], [2], tmp_path / "s2")
    until(lambda: (event(main, "p1", "lane-la") or {}).get("received") == 2)
    (prl.release_dir(main, "p1", 1, 1) / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10]}))
    resume(main, {"n": 1, "phase": "confirm"}, "c1")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed")
    resume(main, {"n": 1, "phase": "settle", "result": "released", "version": "1.2.4"}, "s1")
    ans, _ = until(lambda: pl.read_answer(main, "p1", r1)[0] and pl.read_answer(main, "p1", r1))
    assert (ans["result"], ans["version"], ans["release"]) == ("released", "1.2.4", 1)
    assert event(main, "p1", "range-1") == {"sha": "sha1", "version": "1.2.4", "prs": [10], "lanes": ["la"]}
    dfr = until(lambda: event(main, "p1", f"defer-{r2}"))
    assert dfr == {"seq": 1, "reason": "範囲に入らないため次のリリースへ回した", "at": None}
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release" and e.get("n") == 2 and e)
    assert ev["k"] == 1 and ev["represent"] is False
    wid = pl.workflow_id("p1")
    durable.send_to(pl.KIND, pl.record_identity(main, "p1"), wid, {"kind": "stop"}, "inbox", f"{wid}:inbox:stop")
    time.sleep(0.8)
    assert durable.status(wid) not in ("SUCCESS",)  # 提示のまま流れているリリースの途中の stop も settle まで終わらない
    resume(main, {"n": 2, "phase": "settle", "result": "rejected", "reason": "stop"}, "s2")
    out = durable.output_of(wid)
    assert [(r["n"], r["state"]) for r in out["releases"]] == [(1, "released"), (2, "rejected")]
    assert out["releases"][1]["requests"] == [r2] and out["pending"] == [r2]


def test_wait_returns_smaller_seq_first(env):
    main, states = env["main"], env["states"]
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    first = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "running" and e)
    wid = pl.workflow_id("p1")
    durable.send_to(pl.KIND, pl.record_identity(main, "p1"), wid, {"kind": "ask", "lane": "la", "q": "?"}, "inbox", f"{wid}:inbox:q1")
    q = until(lambda: event(main, "p1", "questions"))
    states.write_text(json.dumps({"la": "awaiting_approval"}))
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "notice")
    ident = pl.record_identity(main, "p1")
    got = durable.wait_from(pl.KIND, ident, wid, ["pause", "questions"], first["seq"], 5)
    assert got.kind == "event" and got.value["key"] == "questions" and got.value["seq"] == q["seq"]
    got = durable.wait_from(pl.KIND, ident, wid, ["pause", "questions"], q["seq"], 5)
    assert got.kind == "event" and got.value["key"] == "pause" and got.value["action"] == "notice"
    stop(main)


def test_stop_during_release_waits_for_settle(env, tmp_path):
    main, states = env["main"], env["states"]
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    (prl.release_dir(main, "p1", 1, 1) / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10]}))
    resume(main, {"n": 1, "phase": "confirm"}, "c1")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed")
    wid = pl.workflow_id("p1")
    durable.send_to(pl.KIND, pl.record_identity(main, "p1"), wid, {"kind": "stop"}, "inbox", f"{wid}:inbox:stop")
    time.sleep(0.8)
    assert durable.status(wid) not in ("SUCCESS",)  # リリースの途中の stop は settle まで終わらない
    resume(main, {"n": 1, "phase": "settle", "result": "released", "version": "1.2.3"}, "s1")
    out = durable.output_of(wid)
    assert out["status"] == "stopped" and out["releases"][0]["state"] == "released"
    assert pl.read_answer(main, "p1", rid)[0]["result"] == "released"


def test_stop_force_after_a_pending_stop_stops_the_helper(env, tmp_path):
    """settle を待って停止を保留している間も inbox を読み、後から届いた --force で一時レーンも止めて終える。"""
    main, log, states = env["main"], env["log"], env["states"]
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    wid = pl.workflow_id("p1")
    durable.send_to(pl.KIND, pl.record_identity(main, "p1"), wid, {"kind": "stop"}, "inbox", f"{wid}:inbox:stop")
    time.sleep(0.6)
    assert durable.status(wid) in pl.RUNNING
    durable.send_to(pl.KIND, pl.record_identity(main, "p1"), wid, {"kind": "stop", "force": True}, "inbox", f"{wid}:inbox:stop:force")
    out = durable.output_of(wid)
    assert out["status"] == "stopped" and ["stop", "p1-rel1", "--json"] in calls(log, "stop")


def test_notice_during_release_carries_launch(env, tmp_path):
    main, states = env["main"], env["states"]
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    rel = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release" and e)
    states.write_text(json.dumps({"la": "awaiting_approval"}))
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "notice" and e)
    assert ev["release"]["seq"] == rel["seq"] and ev["release"]["spawn"] == rel["spawn"]
    assert ev["release"]["pipeline"] == rel["pipeline"]


# ---- 問いをオーガナイザーへ集める（受け入れ条件 6c・6d・6f）と一時レーン ----

ASK = {"questions": [{"question": "gh の認証を直してよいか", "options": [{"label": "済んだ"}, {"label": "まだ"}]}],
       "metadata": {"source": "ndf-ask:human-action"}}  # fmt: skip


def questions(main):
    return event(main, "p1", "questions") or {"items": []}


def test_ask_request_raises_questions_and_the_answer_reaches_the_lane(env, tmp_path):
    main = env["main"]
    start(main)
    rid = pl.request_ask(main, "p1", "la", ASK, str(tmp_path), "led1")
    assert rid == "la-qled1"
    got = until(lambda: [i for i in questions(main)["items"] if i["id"] == rid])
    assert got[0]["kind"] == "ask" and got[0]["source"] == "ndf-ask:human-action" and "attach la" in got[0]["attach"]
    pl.send_answer(main, "p1", rid, "answered", choice=["済んだ"])
    ans = until(lambda: pl.answers_of(main, "p1", [rid])[rid]["answer"])
    assert ans["result"] == "answered" and ans["choice"] == ["済んだ"]
    until(lambda: not [i for i in questions(main)["items"] if i["id"] == rid])  # 答えた問いはもう返さない


def test_gate1_answer_event_carries_the_merge_result_and_who_approved(env, tmp_path):
    """設計の承認の答えのイベントに、答えの前に試みたマージの結果と担い手が載る（#2507）。同じ先端で戻した依頼は別の id。"""
    main = env["main"]
    start(main)
    rid = pl.request_gate1(main, "p1", "la", 11, 501, "r.md", "s.json", "a" * 40, verdict="unknown")
    again = pl.request_gate1(main, "p1", "la", 11, 501, "r.md", "s.json", "a" * 40, suffix="-m3")
    assert again == f"{rid}-m3"
    until(lambda: [i for i in questions(main)["items"] if i["id"] == rid])
    pl.send_answer(main, "p1", rid, "approved", merge={"result": "merged"}, by="mvv")
    ans = until(lambda: pl.answers_of(main, "p1", [rid])[rid]["answer"])
    assert ans["merge"] == {"result": "merged"} and ans["by"] == "mvv"
    until(lambda: [i["id"] for i in questions(main)["items"]] == [again])


def test_the_same_ask_id_is_counted_once(env, tmp_path):
    main = env["main"]
    start(main)
    pl.request_ask(main, "p1", "la", ASK, str(tmp_path), "led1")
    pl.request_ask(main, "p1", "la", ASK, str(tmp_path), "led1")
    until(lambda: questions(main)["items"])
    time.sleep(0.6)
    assert len(questions(main)["items"]) == 1


def test_sender_is_a_running_lane_or_a_helper_with_the_role_file(env, tmp_path):
    main = env["main"]
    helper = tmp_path / "helper"
    helper.mkdir()
    (helper / "role.json").write_text(json.dumps({"role": "helper", "plan": "p1"}))
    other = tmp_path / "other"
    other.mkdir()
    assert pl.lane_name_in_plans(main, "la", None) == "p1"  # ファイルだけで見る
    assert pl.lane_name_in_plans(main, "p1-rel1", str(helper)) == "p1"
    assert pl.lane_name_in_plans(main, "zz", str(other)) is None
    assert pl.sender_of(main, "la", None) is None  # 計画が動いていない
    start(main)
    until(lambda: pl.run_status(main, "p1") in pl.RUNNING)
    assert pl.sender_of(main, "la", None) == ("p1", "la")
    assert pl.sender_of(main, "p1-rel1", str(helper)) == ("p1", "p1-rel1")  # lanes に載らない一時レーンも送れる
    assert pl.sender_of(main, "zz", str(other)) is None


def test_helper_task_follows_the_last_launch(env, tmp_path):
    main = env["main"]
    start(main)
    pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    task = prun.helper_task(main, "p1", 1, 1)
    d = prl.release_dir(main, "p1", 1, 1)
    assert task["phase"] == "present" and f"queue {d / 'present.json'}" in task["pipeline"]
    assert "request-gate2 p1 1 1" in task["gate2"] and "settle p1 1 --done" in task["settle"]
    (d / "present.json").write_text(
        json.dumps({"steps": [{"id": "present", "cwd": "/r", "cmd": "parallel.py present p1 1 1 --approval issues/a.md && cat x"}]})
    )
    assert prun.helper_task(main, "p1", 1, 1)["gate2"].endswith("--report /r/issues/a.md")
    (d / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10]}))
    pl.request_gate2(main, "p1", 1, 1, str(d / "report.md"))
    item = until(lambda: [i for i in questions(main)["items"] if i["kind"] == "gate2"])[0]
    assert item["id"] == "p1-p1-1" and item["stamp"] == "sha1"
    resume(main, {"n": 1, "phase": "confirm"}, "c1")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed")
    until(lambda: not [i for i in questions(main)["items"] if i["kind"] == "gate2"])  # リリース承認ゲートの問いは答えで消える
    task = prun.helper_task(main, "p1", 1, 1)
    assert task["phase"] == "confirmed"
    assert (
        "'リリース承認ゲート'" in task["pipeline"]
        and "--from approve" in task["pipeline"]
        and f"queue {d / 'release.json'}" in task["pipeline"]
    )


def test_request_ask_cli_refuses_a_sender_outside_the_plan_with_4(env, monkeypatch):
    monkeypatch.delenv("NDF_RELAY_DIR", raising=False)
    got = subprocess.run(
        [sys.executable, str(SCRIPTS / "parallel.py"), "request-ask", "p1", "--lane", "zz"],
        input=json.dumps({"tool_input": ASK, "cwd": str(env["main"]), "ledger_id": "x"}),
        capture_output=True,
        text=True,
        cwd=str(env["main"]),
    )
    assert got.returncode == 4 and json.loads(got.stdout)["status"] == "stopped"


# ---- 異常系（確定仕様 docs/specifications/ndf-parallel-organizer.md のエラー処理と一時レーンの停止） ----

OLD = "2000-01-01T00:00:00Z"


def test_a_stalled_lane_is_asked_once(env):
    main, states = env["main"], env["states"]
    start(main)
    until(lambda: spawned(env["log"]) == ["la"])
    states.write_text(json.dumps({"la": {"state": "running", "at": OLD}}))
    item = until(lambda: [i for i in questions(main)["items"] if i["kind"] == "stall"])[0]
    assert item["lane"] == "la" and item["options"] == ["起こし直す", "止める", "待つ"] and "restart la" in item["commands"]["起こし直す"]
    time.sleep(0.8)
    assert len([i for i in questions(main)["items"] if i["kind"] == "stall"]) == 1  # 次の区切りまで立てない


def test_questions_of_the_same_cause_are_asked_once_and_answered_to_each(env, tmp_path):
    main = env["main"]
    env["states"].write_text(json.dumps({"la": "running", "lb": "running"}))
    start(main)
    a = pl.request_ask(
        main, "p1", "la", {**ASK, "questions": [{"question": "gh の認証が切れた（/work/la の #12）", "options": []}]}, str(tmp_path), "x1"
    )
    b = pl.request_ask(
        main, "p1", "lb", {**ASK, "questions": [{"question": "gh の認証が切れた（/work/lb の #34）", "options": []}]}, str(tmp_path), "x2"
    )
    item = until(lambda: [i for i in questions(main)["items"] if b in (i.get("also") or [])])[0]
    assert item["id"] == a and len(questions(main)["items"]) == 1 and "/work/lb の #34" in item["merged"][0]["question"]
    pl.send_answer(main, "p1", a, "answered", choice=["済んだ"])
    until(lambda: all(pl.answers_of(main, "p1", [a, b])[r]["answer"] for r in (a, b)))


def test_three_spawn_refusals_raise_a_question(env, monkeypatch):
    monkeypatch.setenv("FAKE_REFUSE", "la")
    main = env["main"]
    start(main)
    item = until(lambda: [i for i in questions(main)["items"] if i["kind"] == "spawn"])[0]
    assert item["lane"] == "la" and "limit" in item["question"] and len(calls(env["log"], "spawn")) >= 3


def test_a_stopped_helper_without_settle_is_asked(env, tmp_path):
    main, states = env["main"], env["states"]
    start(main)
    pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    states.write_text(json.dumps({"la": "running", "p1-rel1": {"state": "stopped", "role": "helper"}}))
    item = until(lambda: [i for i in questions(main)["items"] if i["kind"] == "helper"])[0]
    assert item["options"] == ["続きから起こし直す", "止める"] and "p1-rel1" in item["question"]


def test_stop_answer_to_a_stopped_helper_fails_the_release(env, tmp_path):
    """一時レーンの問いへ「止める」と答えると、リリースを失敗として締め、依頼へ failed を答える。"""
    main, states = env["main"], env["states"]
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    states.write_text(json.dumps({"la": "running", "p1-rel1": {"state": "stopped", "role": "helper"}}))
    item = until(lambda: [i for i in questions(main)["items"] if i["kind"] == "helper"])[0]
    pl.send_answer(main, "p1", item["id"], "answered", choice=["止める"])
    ans = until(lambda: pl.read_answer(main, "p1", rid)[0])
    assert ans["result"] == "failed"


def test_stop_answer_to_spawn_refusals_gives_up_the_lane(env, monkeypatch):
    """起動できないレーンの問いへ「止める」と答えると、起こし直しをやめて止まったものと数える。"""
    monkeypatch.setenv("FAKE_REFUSE", "la")
    main, log = env["main"], env["log"]
    start(main)
    item = until(lambda: [i for i in questions(main)["items"] if i["kind"] == "spawn"])[0]
    pl.send_answer(main, "p1", item["id"], "answered", choice=["止める"])
    until(lambda: not questions(main)["items"])
    time.sleep(1.0)
    before = len([c for c in calls(log, "spawn") if c[2] == "la"])
    time.sleep(1.0)
    assert len([c for c in calls(log, "spawn") if c[2] == "la"]) == before


def test_the_plan_waits_until_the_owner_applies_a_design_answer(env, monkeypatch, tmp_path):
    """全レーンが止まった後に設計の承認へ答えても、持ち主が答えを当てるまで計画を終えず、起こす 1 行を添える（6f）。"""
    main, states = env["main"], env["states"]
    applied = {"v": False}
    monkeypatch.setattr(prun, "design_held", lambda m, n, answered: ["la"] if answered and not applied["v"] else [])
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    rid = pl.request_ask(main, "p1", "la", ASK, str(tmp_path), "d1")
    until(lambda: questions(main)["items"])
    states.write_text(json.dumps({"la": "stopped", "lb": "stopped"}))
    time.sleep(1.0)
    assert "lb" not in spawned(env["log"])  # 保留中の la は依存の判定で止まったと数えない（I8）
    pl.send_answer(main, "p1", rid, "answered", choice=["済んだ"])
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("wake") and e)
    assert ev["wake"] == [f"python3 {prun.RELAY} restart la"]
    assert durable.status(pl.workflow_id("p1")) in pl.RUNNING
    applied["v"] = True
    states.write_text(json.dumps({"la": "running", "lb": "stopped"}))  # 起こした la が動き出し、答えを当てて止まる
    time.sleep(1.0)  # 動き出した la を観測させる
    states.write_text(json.dumps({"la": "stopped", "lb": "stopped"}))
    assert durable.output_of(pl.workflow_id("p1"))["status"] == "finished"


def test_stop_force_does_not_wait_for_settle_and_stops_the_helper(env, tmp_path):
    main, log = env["main"], env["log"]
    start(main)
    pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    wid = pl.workflow_id("p1")
    durable.send_to(pl.KIND, pl.record_identity(main, "p1"), wid, {"kind": "stop", "force": True}, "inbox", f"{wid}:inbox:stop:force")
    out = durable.output_of(wid)
    assert out["status"] == "stopped" and ["stop", "p1-rel1", "--json"] in calls(log, "stop")


def test_answered_pending_laps_of_stopped_loop_lanes_are_closed_by_a_helper_before_finishing(env, monkeypatch, tmp_path):
    main, states = env["main"], env["states"]
    monkeypatch.setattr(prun, "held_loops", lambda m, n, answered: ["lroot"] if answered else [])
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    (prl.release_dir(main, "p1", 1, 1) / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10]}))
    resume(main, {"n": 1, "phase": "confirm"}, "c1")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed")
    resume(main, {"n": 1, "phase": "settle", "result": "released", "version": "1.2.3"}, "s1")
    until(lambda: pl.read_answer(main, "p1", rid)[0])
    states.write_text(json.dumps({"la": "stopped", "lb": "stopped"}))
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "held" and e)
    assert ev["loops"] == ["lroot"] and "--parallel-helper p1 pending lroot" in ev["spawn"]
    assert durable.status(pl.workflow_id("p1")) in pl.RUNNING  # 流し終えるまで finished にしない


def test_settle_wakes_a_stopped_lane_of_the_range(env, tmp_path):
    main, log, states = env["main"], env["log"], env["states"]
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    states.write_text(json.dumps({"la": "stopped", "lb": "running"}))  # la は待ちだけで止まった（異常系 6）
    until(lambda: "lb" in spawned(log))
    (prl.release_dir(main, "p1", 1, 1) / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10]}))
    resume(main, {"n": 1, "phase": "confirm"}, "c1")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed")
    resume(main, {"n": 1, "phase": "settle", "result": "released", "version": "1.2.3"}, "s1")
    until(lambda: pl.read_answer(main, "p1", rid)[0])
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("wake") and e)
    assert ev["action"] == "notice" and ev["wake"] == [f"python3 {prun.RELAY} restart la"]
    states.write_text(json.dumps({"la": "stopped", "lb": "awaiting_approval"}))  # 後の知らせが pause を上書きしても
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("lanes") == ["lb"] and e)
    assert ev["wake"] == [f"python3 {prun.RELAY} restart la"]  # レーンが動き出すまで起こす行を添え続ける
    states.write_text(json.dumps({"la": "running", "lb": "running"}))
    pl.send_release_request(main, "p1", "lb", [11], [2], tmp_path / "st2")
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release" and e.get("n") == 2 and e)
    assert "wake" not in ev


def test_the_plan_does_not_finish_while_a_question_is_unanswered(env, tmp_path):
    main, states = env["main"], env["states"]
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    rid = pl.request_ask(main, "p1", "la", ASK, str(tmp_path), "led9")
    until(lambda: questions(main)["items"])
    states.write_text(json.dumps({"la": "stopped", "lb": "stopped"}))  # la が問いを保留して自分のラッパーを止めた
    time.sleep(1.0)
    assert "lb" not in spawned(env["log"])  # 保留中の la は依存の判定で止まったと数えない（I8）
    assert durable.status(pl.workflow_id("p1")) in pl.RUNNING
    pl.send_answer(main, "p1", rid, "answered", choice=["済んだ"])
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("wake") and e)  # 止まった持ち主が答えを当てるまで終えない
    assert ev["wake"] == [f"python3 {prun.RELAY} restart la"]
    assert "lb" not in spawned(env["log"])
    states.write_text(json.dumps({"la": "running", "lb": "stopped"}))
    time.sleep(1.0)  # 動き出した la を観測させる
    states.write_text(json.dumps({"la": "stopped", "lb": "stopped"}))
    until(lambda: "lb" in spawned(env["log"]))  # 答えを当てて止まった la の後に lb を起こす
    assert durable.output_of(pl.workflow_id("p1"))["status"] == "finished"


def test_irreversible_questions_with_different_numbers_are_not_merged(env, tmp_path):
    main = env["main"]
    env["states"].write_text(json.dumps({"la": "running", "lb": "running"}))
    start(main)

    def q(n):
        return {
            "questions": [{"question": f"PR #{n} を消してよいか", "options": [{"label": "はい"}]}],
            "metadata": {"source": "ndf-ask:irreversible"},
        }

    pl.request_ask(main, "p1", "la", q(12), str(tmp_path), "y1")
    pl.request_ask(main, "p1", "lb", q(34), str(tmp_path), "y2")
    until(lambda: len(questions(main)["items"]) == 2)


def test_answer_to_an_own_question_returns_the_chosen_command(monkeypatch, tmp_path):
    """耐久ワークフローが立てた問いの答えの後の 1 行は、選んだ答えの commands で、持ち主を起こす restart ではない。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("parallel_cli", Path(prun.__file__).resolve().parents[1] / "parallel.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    item = {"id": "h1", "kind": "helper", "lane": "p1-rel1", "commands": {"続きから起こし直す": "SPAWN"}}
    monkeypatch.setattr(durable, "event_from", lambda *_a, **_k: {"items": [item]})
    assert pa.owner_restart(tmp_path, "p1", "h1", ["続きから起こし直す"]) == "SPAWN"
    assert pa.owner_restart(tmp_path, "p1", "h1", ["止める"]) is None


def test_owner_is_woken_by_the_lane_side_wait_record(monkeypatch, tmp_path):
    """持ち主を起こす要否はラッパーの状態でなくレーンの側の待ちの記録で決める。ループは記録が waiting_only で止まっている、
    1 スプリントのレーンは引継ぎ文書に依頼の id がある。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("parallel_cli", Path(prun.__file__).resolve().parents[1] / "parallel.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    data = {
        "version": 1,
        "name": "p1",
        "rules": [],
        "lanes": [
            {"name": "la", "input": {"kind": "loop", "loop": "l1", "args": ["--milestones", "01 a"]}, "plans": 1, "touches": ["a/"]},
            {"name": "lb", "input": {"kind": "sprint", "issues": [7]}, "plans": 1, "touches": ["b/"]},
        ],
    }
    pp.plan_dir(tmp_path, "p1").mkdir(parents=True)
    (pp.plan_dir(tmp_path, "p1") / "plan.json").write_text(json.dumps(data))
    items = [{"id": "qa", "kind": "ask", "lane": "la"}, {"id": "qb", "kind": "ask", "lane": "lb"}]
    monkeypatch.setattr(durable, "event_from", lambda *_a, **_k: {"items": items})
    monkeypatch.setattr(
        prun, "relay", lambda *_a, **_k: (0, {"items": [{"name": "la", "state": "running"}, {"name": "lb", "state": "running"}]})
    )
    loop = tmp_path / ".ndf" / "loop" / "l1"
    loop.mkdir(parents=True)
    handoff = tmp_path / ".ndf" / "handoff"
    handoff.mkdir(parents=True)
    assert pa.owner_restart(tmp_path, "p1", "qa") is None and pa.owner_restart(tmp_path, "p1", "qb") is None
    (loop / "loop.json").write_text(json.dumps({"status": "running"}))
    (handoff / "issue-7.md").write_text("## 次にやること\n- 別の作業\n")
    assert pa.owner_restart(tmp_path, "p1", "qa") is None and pa.owner_restart(tmp_path, "p1", "qb") is None
    (loop / "loop.json").write_text(json.dumps({"status": "stopped", "stop": {"condition": "waiting_only"}}))
    (handoff / "issue-7.md").write_text("## 次にやること\n- 依頼 qb の答えを読んで続ける\n")
    assert pa.owner_restart(tmp_path, "p1", "qa") == f"python3 {prun.RELAY} restart la"
    assert pa.owner_restart(tmp_path, "p1", "qb") == f"python3 {prun.RELAY} restart lb"
    items.append({"id": "qh", "kind": "ask", "lane": "p1-rel1"})  # 記録を持たない一時レーンはラッパーが止まっているかで決める
    assert pa.owner_restart(tmp_path, "p1", "qh") == f"python3 {prun.RELAY} restart p1-rel1"
    # 同じ原因としてまとめた問いは、merged の持ち主もそれぞれの依頼の id で調べて起こす（異常系 5）
    items.append({"id": "qm", "kind": "ask", "lane": "la", "merged": [{"id": "qb", "lane": "lb"}]})
    assert pa.owner_restart(tmp_path, "p1", "qm") == f"python3 {prun.RELAY} restart la; python3 {prun.RELAY} restart lb"


def test_a_helper_ends_by_stopping_its_own_wrapper():
    """一時レーンは helper-done の後に自分のラッパーを止めて終える（判断 fca6fe464619 の (a)）。止めないと対話の claude が
    入力待ちで残り、続きの一時レーンの起動が同じ名前か上限で断られる。リリースと保留の周回のどちらの起動も同じ。"""
    for line in (prun.helper_spawn("p1", 2, 1), prun.helper_spawn("p1", 3, 0, pending="lroot")):
        name = line.split("--name ", 1)[1].split()[0]
        assert f"{prun.RELAY} stop {name}" in line.split(" -- ", 1)[1]


def _bare_run(monkeypatch, lanes):
    """耐久ワークフローの外で _Run を組む（知らせは捨てる）。"""
    monkeypatch.setattr(durable, "set_event", lambda *_a, **_k: None)
    return prun._Run("/m", "p1", lanes)


def test_a_held_lane_does_not_release_its_dependents(monkeypatch):
    """問いを保留して止まったレーン・答えを当てていない revive・waiting_only で待つループは、依存の判定で止まったと数えない（I8）。"""
    run = _bare_run(monkeypatch, [{"name": "la", "input": {"kind": "sprint"}}, {"name": "lb", "after": ["la"]}])
    run.lanes["la"].update(started=True, state="stopped")
    spawned, waiting = [], []
    monkeypatch.setattr(prun, "step_spawn", lambda _m, _n, names: spawned.extend(names) or {n: {"started": True} for n in names})
    monkeypatch.setattr(prun, "step_waiting_lanes", lambda *_a: list(waiting))
    run.questions = [{"id": "q1", "kind": "ask", "lane": "la"}]
    prun._spawn_ready(run, "/m")
    run.questions, run.revive = [], ["la"]
    prun._spawn_ready(run, "/m")
    run.revive, waiting[:] = [], ["la"]
    prun._spawn_ready(run, "/m")
    assert spawned == []
    waiting.clear()
    prun._spawn_ready(run, "/m")
    assert spawned == ["lb"]


def test_a_revived_lane_that_stops_between_observations_leaves_revive(monkeypatch):
    """起こしたレーンが観測の間隔の中で stopped → running → stopped と動いても、最後に動いた時刻が答えたときの観測より
    後へ進めば revive から外す。外さないと _finish_or_hold が設計の承認待ちを空にできず、計画が終わらない。"""
    run = _bare_run(monkeypatch, [{"name": "la", "input": {"kind": "sprint"}}])
    seen = {"now": 1_000_000.0, "rows": {"la": {"state": "stopped", "at": "1970-01-12T13:40:00Z"}}}  # 999_600
    monkeypatch.setattr(prun, "step_observe", lambda _m: seen)
    run.lanes["la"].update(started=True)
    prun._observe_run(run, "/m")
    run.questions = [{"id": "q1", "kind": "ask", "lane": "la"}]
    run.reply({"id": "q1", "result": "answered"})
    assert run.revive == ["la"]
    seen["now"] = 1_000_060.0
    prun._observe_run(run, "/m")
    assert run.revive == ["la"]  # まだ動いていない
    seen["rows"]["la"]["at"] = "1970-01-12T13:46:50Z"  # 1_000_010。答えの後に動いて、また止まった
    seen["now"] = 1_000_120.0
    prun._observe_run(run, "/m")
    assert run.revive == []


def test_a_helper_that_stopped_after_asking_is_not_asked_about(monkeypatch):
    """一時レーンが ask を送って自分のラッパーを止めたときは、「リリースの途中で止まった」を問わない（6g）。"""
    run = _bare_run(monkeypatch, [{"name": "la"}])
    run.current, run.launch = {"n": 1}, {"seq": 3, "spawn": "SPAWN"}
    helper = prl.helper_name("p1", 1)
    run.questions = [{"id": "qa", "kind": "ask", "lane": helper}]
    prun._ask_helper(run, {helper: {"state": "stopped"}})
    assert [q["kind"] for q in run.questions] == ["ask"]
    run.questions = []
    prun._ask_helper(run, {helper: {"state": "stopped"}})
    assert [q["kind"] for q in run.questions] == ["helper"]


def test_settle_answers_with_the_version_of_the_chosen_presentation(env, tmp_path, monkeypatch):
    """出し直しの提示 2 を選んだら、提示 2 を組んだ時点の版で依頼へ答える（settle に版が無くても提示 1 の版にしない）。"""
    main, heads = env["main"], env["heads"]
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    (prl.release_dir(main, "p1", 1, 1) / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10]}))
    heads["v"] = "sha2"
    monkeypatch.setattr(prun, "dev_version", lambda _m: "1.2.4")  # 提示 1 の後に別レーンの開発版が進んだ
    resume(main, {"n": 1, "phase": "confirm"}, "c1")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("represent"))
    (prl.release_dir(main, "p1", 1, 2) / "presented.json").write_text(json.dumps({"sha": "sha2", "prs": [10]}))
    resume(main, {"n": 1, "phase": "confirm", "take": "new"}, "c2")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed")
    resume(main, {"n": 1, "phase": "settle", "result": "released"}, "s1")
    ans, _ = until(lambda: pl.read_answer(main, "p1", rid)[0] and pl.read_answer(main, "p1", rid))
    assert ans["version"] == "1.2.4"


def test_a_late_request_is_answered_by_the_release_that_completed_it(monkeypatch):
    """リリース 1 とリリース 2 の PR を両方含む遅れた依頼には、リリース 2 の版と連番で答える（I15）。"""
    run = _bare_run(monkeypatch, [{"name": "la"}])
    got = []
    monkeypatch.setattr(run, "answer", lambda rid, result, version, n, reason=None: got.append((rid, version, n)))
    run.releases = [
        {"n": 1, "state": "released", "version": "1.2.3", "prs": [10]},
        {"n": 2, "state": "released", "version": "1.2.4", "prs": [20]},
    ]
    run.pending = [{"id": "r1", "lane": "la", "prs": [10, 20]}, {"id": "r0", "lane": "la", "prs": [10]}]
    run.answer_from_ranges()
    assert sorted(got) == [("r0", "1.2.3", 1), ("r1", "1.2.4", 2)] and run.pending == []


# ---------------------------------------------------------------- 状態の一覧の失敗（#2509 の I1・I2・決定 5）


def test_a_failed_ls_keeps_lane_states_and_the_last_observation(monkeypatch):
    """受け入れ条件 1・I1: 一覧が 0 以外で終わった回は、レーンの状態・最後に観測した時刻・起こし直しの待ち・問いを変えない。"""
    run = _bare_run(monkeypatch, [{"name": "la", "input": {"kind": "sprint"}}, {"name": "lb"}])
    seen = {"now": 1_000_000.0, "rows": {"la": {"state": "running", "at": "1970-01-01T00:00:00Z"}}, "code": 0}
    monkeypatch.setattr(prun, "step_observe", lambda _m: seen)
    run.lanes["la"].update(started=True)
    run.lanes["lb"].update(started=True)
    prun._observe_run(run, "/m")
    run.questions, run.stall_until = [], {}
    run.revive, run.revive_at = ["lb"], {"lb": 1_000_000.0}
    before = (json.dumps(run.lanes, sort_keys=True), run.seen_at, list(run.revive), dict(run.revive_at))
    seen.update(now=1_000_060.0, rows={}, code=3, err="ls: hub に届かない")
    prun._observe_run(run, "/m")
    assert (json.dumps(run.lanes, sort_keys=True), run.seen_at, list(run.revive), dict(run.revive_at)) == before
    assert run.questions == [] and run.stall_until == {}


def test_after_a_failed_ls_the_plan_is_not_finished(monkeypatch):
    """I2: 最後の観測が失敗した回の後は、全レーンが止まって見えていても終えない。成功に戻れば終えられる。"""
    run = _bare_run(monkeypatch, [{"name": "la"}])
    seen = {"now": 1.0, "rows": {}, "code": 0}
    monkeypatch.setattr(prun, "step_observe", lambda _m: seen)
    run.lanes["la"].update(started=True)
    prun._observe_run(run, "/m")
    assert prun._run_finished(run)
    seen["code"] = 3
    prun._observe_run(run, "/m")
    assert not prun._run_finished(run)
    seen["code"] = 0
    prun._observe_run(run, "/m")
    assert prun._run_finished(run)


def test_an_observation_recorded_without_code_reads_as_success(monkeypatch):
    """決定 5: 足す前の版で記録した観測（`code` が無い）は成功として読み、行の無い起動済みのレーンを止まったと読む。"""
    run = _bare_run(monkeypatch, [{"name": "la"}])
    monkeypatch.setattr(prun, "step_observe", lambda _m: {"now": 1.0, "rows": {}})
    run.lanes["la"].update(started=True)
    prun._observe_run(run, "/m")
    assert run.lanes["la"]["state"] == "stopped" and prun._run_finished(run)


def test_step_observe_carries_the_exit_code_and_the_last_stderr_line(env, tmp_path):
    (tmp_path / "ls.fail").write_text("")
    got = prun.step_observe(str(env["main"]))
    assert got["code"] == 3 and got["err"] == "ls: hub に届かない" and got["rows"] == {}


def test_the_plan_does_not_finish_while_ls_keeps_failing(env, tmp_path):
    """受け入れ条件 1・2: 一覧が失敗し続ける間は、起動済みのレーンを止まったと読まず、計画を finished で終えない。"""
    main, log = env["main"], env["log"]
    fail = tmp_path / "ls.fail"
    fail.write_text("")
    start(main)
    until(lambda: len(calls(log, "ls")) >= 5)
    assert spawned(log) == ["la"]  # la を止まったと読まないため、lb を起動しない
    assert durable.status(pl.workflow_id("p1")) in pl.RUNNING
    fail.unlink()  # 成功に戻ると、行の無い起動済みのレーンを止まったと読んで終える（受け入れ条件 3 の既存の振る舞い）
    out = until(lambda: durable.status(pl.workflow_id("p1")) not in pl.RUNNING and durable.output_of(pl.workflow_id("p1")))
    assert out["status"] == "finished" and spawned(log) == ["la", "lb"]
