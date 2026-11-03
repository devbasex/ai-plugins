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
        print(json.dumps({"items": [{"name": n, "state": s} for n, s in st.items()], "metrics": {"hub": True, "tmux": True}}))
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
    durable.close()


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
    out = stop(main)
    assert [(r["n"], r["state"]) for r in out["releases"]] == [(1, "released"), (2, "presented")]
    assert out["releases"][1]["requests"] == [r2] and out["pending"] == [r2]


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
