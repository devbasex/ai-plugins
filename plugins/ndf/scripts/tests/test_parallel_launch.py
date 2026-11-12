"""一時レーンの起動とレーンの再起動を耐久ワークフローが打つ（#2936 の受け入れ条件 1〜7、I1〜I6）。

本物の DBOS と、起動に失敗する回数を `FAKE_DOWN` で指定できる偽の `relay.py`（`test_parallel_run.FAKE`）で流す。
"""

from __future__ import annotations

import json
import sys
import time

import pytest

import test_parallel_run as tpr
from test_parallel_run import calls, env, event, helper_spawns, helper_up, questions, set_state, start, until  # noqa: F401  env は fixture

sys.path.insert(0, str(tpr.SCRIPTS / "lib"))
import durable  # noqa: E402
import parallel_lane as pl  # noqa: E402
import parallel_launch as plc  # noqa: E402
import parallel_ledger as plg  # noqa: E402
import parallel_plan as pp  # noqa: E402
import parallel_release as prl  # noqa: E402
import test_parallel_hold_flow as tph  # noqa: E402

ACTIONS = ("questions", "answer_hold", "finished", "stopped", "error")


def checkpoint(main):
    p = plg.checkpoint_path(main, "p1")
    return json.loads(p.read_text()) if p.exists() else {}


def spawn_questions(main):
    return [i for i in questions(main)["items"] if i["kind"] == "spawn"]


@pytest.mark.parametrize("down, want", [(0, 1), (1, 2), (2, 2)])
def test_a_launch_is_retried_once_and_asked_only_after_the_second_failure(env, tmp_path, monkeypatch, down, want):  # noqa: F811
    """受け入れ条件 3: 1 回目か 2 回目で立ち上がれば問いは立たない。2 回とも立ち上がらなければ区分 spawn の問いが 1 つだけ届く。"""
    monkeypatch.setenv("FAKE_DOWN", str(down))
    monkeypatch.setenv("NDF_PARALLEL_LAUNCH_SECONDS", "0.5")
    main, log, states = env["main"], env["log"], env["states"]
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: "rel-1-1-present" in (checkpoint(main).get("launches") or {}))
    rec = checkpoint(main)["launches"]["rel-1-1-present"]
    assert helper_spawns(log) == ["p1-rel1"] * want and rec["attempts"] == want
    time.sleep(0.8)
    got = spawn_questions(main)
    if down < 2:
        assert rec["result"] == "up" and got == []
    else:
        assert rec["result"] == "failed" and len(got) == 1 and got[0]["lane"] == "p1-rel1" and got[0]["n"] == 1
        assert len(helper_spawns(log)) == 2  # 失敗の記録のある目的は打ち直さない（I3）


def test_restarting_from_a_spawn_question_is_a_new_purpose_with_its_own_retry(env, tmp_path, monkeypatch):  # noqa: F811
    """I2: 区分 spawn の「起こし直す」は答えた問いのキーを添えた新しい目的で流し、立ち上がれば問いは残らない。"""
    monkeypatch.setenv("FAKE_DOWN", "2")
    monkeypatch.setenv("NDF_PARALLEL_LAUNCH_SECONDS", "0.5")
    main, log, states = env["main"], env["log"], env["states"]
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    q = until(lambda: spawn_questions(main))[0]
    pl.send_answer(main, "p1", q["id"], "answered", choice=["起こし直す"])
    until(lambda: f"rel-1-1-present-a{q['id']}" in (checkpoint(main).get("launches") or {}))
    rec = checkpoint(main)["launches"][f"rel-1-1-present-a{q['id']}"]
    assert rec["result"] == "up" and len(helper_spawns(log)) == 3
    assert checkpoint(main)["launches"]["rel-1-1-present"]["result"] == "failed"
    assert spawn_questions(main) == []


def test_a_stall_answer_restarts_the_lane_that_still_looks_running(env, monkeypatch):  # noqa: F811
    """I3: 区分 stall の「起こし直す」は、動いて観えるまま止まったレーンへ一覧を見ずに restart を打つ。答えに 1 行は付かない。"""
    main, log, states = env["main"], env["log"], env["states"]
    start(main)
    until(lambda: tpr.spawned(log) == ["la"])
    states.write_text(json.dumps({"la": {"state": "running", "at": tpr.OLD}}))
    q = until(lambda: [i for i in questions(main)["items"] if i["kind"] == "stall"])[0]
    pl.send_answer(main, "p1", q["id"], "answered", choice=["起こし直す"])
    until(lambda: ["restart", "la"] in calls(log, "restart"))
    rec = until(lambda: (checkpoint(main).get("launches") or {}).get(f"ans-la-{q['id']}"))
    assert rec["result"] == "up" and rec["attempts"] == 1
    assert pa_next(main, q) is None


def pa_next(main, q):
    import parallel_answer as pa

    return pa.owner_next(main, "p1", q["id"], ["起こし直す"], [q])[0]


def test_a_stall_answer_to_stop_stops_the_lane(env):  # noqa: F811
    main, log, states = env["main"], env["log"], env["states"]
    start(main)
    until(lambda: tpr.spawned(log) == ["la"])
    states.write_text(json.dumps({"la": {"state": "running", "at": tpr.OLD}}))
    q = until(lambda: [i for i in questions(main)["items"] if i["kind"] == "stall"])[0]
    pl.send_answer(main, "p1", q["id"], "answered", choice=["止める"])
    until(lambda: ["stop", "la", "--json"] in calls(log, "stop"))


def test_a_lane_already_running_is_not_launched_again(env, tmp_path):  # noqa: F811
    """I3: 打つ前の一覧でそのレーンが動いていれば打たずに立ち上がったと数える。"""
    main, log, states = env["main"], env["log"], env["states"]
    states.write_text(json.dumps({"la": "running", "p1-rel1": "running"}))
    start(main)
    pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    rec = until(lambda: (checkpoint(main).get("launches") or {}).get("rel-1-1-present"))
    assert (rec["result"], rec["attempts"]) == ("up", 0) and helper_spawns(log) == []


def test_came_up_reads_a_short_lived_lane_and_a_fresh_restart():
    now = time.time()
    later = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now + 2))
    earlier = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now - 60))
    assert plc.came_up({"state": "stopped", "at": later}, now, False)  # 短い作業を終えて止まったもの
    assert not plc.came_up({"state": "stopped", "at": earlier}, now, False)
    assert not plc.came_up({"state": "running", "at": earlier}, now, True)  # 区分 stall の再起動は at が進むまで待つ
    assert plc.came_up({"state": "running", "at": later}, now, True)


# ---- 受け入れ条件 5・6: リリース 1 回で wait が返す action ----


def test_one_release_wakes_the_organizer_only_for_the_gate_and_the_end(env, tmp_path, monkeypatch, capsys):  # noqa: F811
    """提示 → リリース承認ゲート → 承認 → 確定 → 結果を流し、wait の action の列が questions 1 回と finished 1 回だけになる。
    一時レーンは依頼を送ってから止まり、settle の後に止まる。どちらでも区分 helper・stall の問いは立たない（受け入れ条件 4）。"""
    main, log, states = env["main"], env["log"], env["states"]
    cli = tph.load_cli()
    monkeypatch.setattr(cli, "main_of", lambda: main)
    seen: list[str] = []
    box = {"after": 0}

    def wait_once():
        code, out = tph.wait(cli, capsys, "--after", str(box["after"]), "--timeout", "1")
        if code == 3:
            box["after"] = int(out["metrics"].get("seq") or box["after"])
            return None
        seen.append(out["metrics"]["action"])
        box["after"] = int(out["metrics"].get("seq") or box["after"])
        assert not any("relay.py spawn" in json.dumps(v) or "relay.py restart" in json.dumps(v) for v in out["metrics"].values())
        return out

    states.write_text(json.dumps({"la": "running"}))
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    helper_up(states, "p1-rel1")  # 提示の一時レーンは耐久ワークフローが起動する
    (prl.release_dir(main, "p1", 1, 1) / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10]}))
    assert wait_once() is None  # 起動の知らせでオーガナイザーを起こさない
    pl.request_gate2(main, "p1", 1, 1, "report.md")  # 一時レーンがリリース承認ゲートの依頼を送り、自分を止める
    set_state(states, "p1-rel1", "stopped")
    out = until(wait_once)
    assert out["metrics"]["action"] == "questions" and [i["kind"] for i in out["items"]] == ["gate2"]
    pl.resume_plan(main, "p1", {"n": 1, "phase": "confirm", "take": None}, "confirm:1:-")  # 承認の答え
    until(lambda: helper_spawns(log) == ["p1-rel1", "p1-rel1"])  # 確定の一時レーンも耐久ワークフローが起動する
    helper_up(states, "p1-rel1")
    until(lambda: json.loads(states.read_text())["p1-rel1"]["state"] == "running")
    assert wait_once() is None  # 答えの後の空の questions では返らない
    pl.resume_plan(main, "p1", {"n": 1, "phase": "settle", "result": "released", "version": "1.2.3", "reason": None}, "settle:1")
    set_state(states, "p1-rel1", "stopped")  # settle の後に helper-done を出して自分を止める
    until(lambda: pl.read_answer(main, "p1", rid)[0])
    set_state(states, "la", "stopped")
    until(lambda: "lb" in tpr.spawned(log))
    time.sleep(0.6)
    set_state(states, "lb", "stopped")
    until(lambda: wait_once() and seen[-1] == "finished")
    assert seen == ["questions", "finished"] and all(a in ACTIONS for a in seen)
    out = durable.output_of(pl.workflow_id("p1"))
    assert not [q for q in out.get("prior_waits") or []] and out["status"] == "finished"
    assert not [k for k in ("helper", "stall") if any(i["kind"] == k for i in (questions(main)["items"]))]


# ---- 受け入れ条件 7: レーンの様子 ----

LOOP_PLAN = {
    "version": 1,
    "name": "p1",
    "rules": [],
    "lanes": [
        {"name": "la", "input": {"kind": "loop", "loop": "la", "args": ["--milestones", "01 a"]}, "plans": 1, "touches": ["a/"]},
        {"name": "lb", "input": {"kind": "sprint", "issues": [2]}, "plans": 1, "touches": ["b/"]},
    ],
}


def _status(main, monkeypatch, capsys):
    cli = tph.load_cli()
    monkeypatch.setattr(cli, "main_of", lambda: main)
    with pytest.raises(SystemExit) as e:
        cli.main(["status", "p1"])
    return e.value.code or 0, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_status_returns_one_row_per_lane_with_the_attach_line(env, monkeypatch, capsys):  # noqa: F811
    main, states = env["main"], env["states"]
    plan = pp.check_data(LOOP_PLAN)[0]
    (pp.plan_dir(main, "p1") / "plan.json").write_text(json.dumps(plan.model_dump(mode="json")))
    from sprint_loop_names import LOOP_DIR

    rec = main / LOOP_DIR / "la" / "loop.json"
    rec.parent.mkdir(parents=True)
    laps = [
        {"n": 1, "cost": 1.5, "selection": [{"number": 7, "decision": "選んだ"}]},
        {"n": 2, "cost": 2.0, "selection": [{"number": 8, "decision": "選んだ"}, {"number": 9, "decision": "外した"}]},
    ]
    rec.write_text(json.dumps({"status": "running", "laps": laps}))
    states.write_text(json.dumps({"la": "running", "lb": "awaiting_approval", "p1-rel3": {"state": "stopped", "role": "helper"}}))
    pl.request_ask(main, "p1", "la", tpr.ASK, str(main), "s1")
    code, out = _status(main, monkeypatch, capsys)
    rows = {r["lane"]: r for r in out["items"]}
    assert code == 0 and list(rows) == ["la", "lb", "p1-rel3"]
    la, lb, h = rows["la"], rows["lb"], rows["p1-rel3"]
    assert (la["state"], la["lap"], la["issues"], la["cost_usd"]) == ("running", 2, [8], 3.5)
    assert la["last_at"] and la["attach"].endswith("attach la")  # 答えていない依頼を送ったレーン
    assert (lb["state"], lb["lap"], lb["cost_usd"], lb["last_at"]) == ("awaiting_approval", None, None, None)
    assert lb["attach"].endswith("attach lb") and h["attach"] is None and h["state"] == "stopped"
    assert "レーン 3 本" in out["summary"]


def test_status_fills_nulls_when_nothing_can_be_read(env, monkeypatch, capsys):  # noqa: F811
    """I6: 一覧・ループの記録・台帳のどれが読めなくても終了コード 0 で全レーンの行を返す。"""
    main = env["main"]
    (env["main"].parent / "ls.fail").write_text("")
    (pp.plan_dir(main, "p1") / "ledger.jsonl").write_text("{壊れた行\n")
    code, out = _status(main, monkeypatch, capsys)
    assert code == 0 and [r["lane"] for r in out["items"]] == ["la", "lb"]
    assert all(r["state"] is None and r["lap"] is None and r["attach"] is None for r in out["items"])


def test_status_of_an_unknown_plan_exits_2(env, monkeypatch, capsys):  # noqa: F811
    cli = tph.load_cli()
    monkeypatch.setattr(cli, "main_of", lambda: env["main"])
    with pytest.raises(SystemExit) as e:
        cli.main(["status", "nope"])
    assert e.value.code == 2
