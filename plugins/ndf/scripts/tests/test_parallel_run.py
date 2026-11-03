"""並列の進行の耐久ワークフロー（#2436 の受け入れ条件 4・6・8・11・12、I7・I9・I10・I11・I14・I15）。

本物の DBOS（SQLite。置き場は根の conftest.py が `NDF_DBOS_DIR` へ向ける）で流し、統括役の CLI は
`NDF_PARALLEL_RELAY` の差し替え（呼び出しを記録し、`ls` の状態をファイルから返す）で、git と配布のプランの作りは
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
    assert dfr == {"seq": 1, "reason": "範囲に入らないため次の配布へ回した", "at": None}
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release" and e.get("n") == 2 and e)
    assert ev["k"] == 1 and ev["represent"] is False
    wid = pl.workflow_id("p1")
    durable.send_to(pl.KIND, pl.record_identity(main, "p1"), wid, {"kind": "stop"}, "inbox", f"{wid}:inbox:stop")
    time.sleep(0.8)
    assert durable.status(wid) not in ("SUCCESS",)  # 提示のまま流れている配布の途中の stop も settle まで終わらない
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
    assert durable.status(wid) not in ("SUCCESS",)  # 配布の途中の stop は settle まで終わらない
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
    until(lambda: not [i for i in questions(main)["items"] if i["kind"] == "gate2"])  # 承認ゲート 2 の問いは答えで消える
    task = prun.helper_task(main, "p1", 1, 1)
    assert task["phase"] == "confirmed"
    assert "'関門 2'" in task["pipeline"] and "--from approve" in task["pipeline"] and f"queue {d / 'release.json'}" in task["pipeline"]


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


# ---- 異常系（設計 issue-2436-design-failures.md の 3・5・11・12・17 と一時レーンの停止） ----

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
    """一時レーンの問いへ「止める」と答えると、配布を失敗として締め、依頼へ failed を答える。"""
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
    until(lambda: "lb" in spawned(env["log"]))
    pl.send_answer(main, "p1", rid, "answered", choice=["済んだ"])
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("wake") and e)
    assert ev["wake"] == [f"python3 {prun.RELAY} restart la"]
    assert durable.status(pl.workflow_id("p1")) in pl.RUNNING
    applied["v"] = True
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
    states.write_text(json.dumps({"la": "stopped", "lb": "stopped"}))
    until(lambda: "lb" in spawned(env["log"]))
    time.sleep(1.0)
    assert durable.status(pl.workflow_id("p1")) in pl.RUNNING
    pl.send_answer(main, "p1", rid, "answered", choice=["済んだ"])
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
    assert cli._owner_restart(tmp_path, "p1", "h1", ["続きから起こし直す"]) == "SPAWN"
    assert cli._owner_restart(tmp_path, "p1", "h1", ["止める"]) is None
