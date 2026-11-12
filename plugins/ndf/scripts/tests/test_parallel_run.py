"""並列の進行の耐久ワークフロー（#2436 の受け入れ条件 4・6・8・11・12、I7・I9・I10・I11・I14・I15）。

本物の DBOS（SQLite。置き場は根の conftest.py が `NDF_DBOS_DIR` へ向ける）で流し、リレーハブの CLI は
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
import parallel_reply  # noqa: E402
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

TEMPLATE_DECL = {"release": {"form": "package-plugin", "plugin": "ndf"}}
# この印の後の記録の道（終わりの記録で数える・提示 1 で確定する。#3058・#3070）は test_parallel_end.py で確かめる。
# ここの流れのテストは印の無い記録の道で確かめる（別のプロセスで記録するテストも同じ値にする）
LEGACY_MARKS = ("parallel-lane-end", "parallel-approved-sha")


def legacy_patched(orig):
    return lambda n: False if n in LEGACY_MARKS else orig(n)


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
        # 一時レーンの起動と再起動は一覧へ running で現れる。FAKE_DOWN の回数だけは現れない（#2936 の受け入れ条件 3）
        name = sys.argv[3] if sub == "spawn" else sys.argv[2] if sub == "restart" else None
        if name and (sub == "restart" or "--role" in sys.argv):
            down, used = int(os.environ.get("FAKE_DOWN") or 0), os.environ["FAKE_LOG"] + ".down"
            n = int(open(used).read()) if os.path.exists(used) else 0
            if n < down:
                open(used, "w").write(str(n + 1))
            else:
                import time
                st = json.load(open(states)) if os.path.exists(states) else {}
                now = time.time()
                at = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now % 1 * 1000):03d}Z"
                st[name] = {"state": "running", "at": at, "role": "helper" if sub == "spawn" else None}
                json.dump(st, open(states + ".tmp", "w"))
                os.replace(states + ".tmp", states)
        print(json.dumps({"items": [{"result": "started"}], "metrics": {}}))
    """
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    main = tmp_path / "repo"
    (main / ".ndf").mkdir(parents=True)
    (main / ".ndf" / "supervise.json").write_text(json.dumps(TEMPLATE_DECL))
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
    monkeypatch.setenv("NDF_PARALLEL_LAUNCH_SECONDS", "1")
    monkeypatch.setattr(durable, "patched", legacy_patched(durable.patched))
    heads = {"v": "sha1"}
    monkeypatch.setattr(prun, "git_head", lambda _m: heads["v"])
    monkeypatch.setattr(prun, "dev_version", lambda _m: "1.2.3")

    def builder(_main, _name, d, _v, **_kw):
        return {"steps": []}, {"steps": []}

    monkeypatch.setattr(prl, "build_plans", lambda m, n, s, k, v, builder_=None, **kw: _build(m, n, s, k, v, builder, **kw))
    yield {"main": main, "log": log, "states": states, "heads": heads}
    _stop_running(main)
    durable.close()


_SEND_TO = durable.send_to  # テストの monkeypatch に左右されない後片付けの送り手


def _stop_running(main):
    """テストが残した耐久ワークフローを止め、スレッドが抜けるのを待つ。

    close() は DBOS を止めてもワークフローのスレッドを止めない。残すと次のテストの同じ ID（parallel-p1）の起動が
    重複として打ち切られ、前のテストのスレッドが次のテストの記録を読み書きする。
    env の後片付けは monkeypatch を戻す前に走るため、テストが差し替えた `durable.send_to` ではなく元の関数で送る。"""
    wid = pl.workflow_id("p1")
    try:
        if durable.status(wid) not in pl.RUNNING:
            return
        _SEND_TO(pl.KIND, pl.record_identity(main, "p1"), wid, {"kind": "stop", "force": True}, "inbox", f"{wid}:inbox:stop:force")
        end = time.monotonic() + 15
        while time.monotonic() < end and durable.status(wid) in pl.RUNNING:
            time.sleep(0.1)
    except Exception:  # 後片付けの失敗でテストの結果を変えない
        pass


def _build(m, n, s, k, v, builder, **kw):
    return prl.__dict__["_orig_build"](m, n, s, k, v, builder, **kw)


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


def lanes_spawned(log):
    """計画のレーンの起動（一時レーンの起動を除く）。"""
    return [c[2] for c in calls(log, "spawn") if "--role" not in c]


def helper_spawns(log):
    """耐久ワークフローが打った一時レーンの起動（#2936）。"""
    return [c[2] for c in calls(log, "spawn") if "--role" in c and c[c.index("--role") + 1] == "helper"]


def helper_up(states, name):
    """偽の relay.py が起動したレーンを一覧へ書くまで待つ（書く前に上書きすると、後から running に戻る）。"""
    until(lambda: states.exists() and name in json.loads(states.read_text() or "{}"))


def set_state(states, name, state, at=None):
    """一覧の 1 行を書き換える（他の行は残す）。`at` を省けば今の時刻。"""
    st = json.loads(states.read_text()) if states.exists() else {}
    now = time.time()  # 一覧の at はミリ秒まで持つ（秒で切ると、打った直後の止まりを打つ前の行と読む）
    st[name] = {"state": state, "at": at or time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now % 1 * 1000):03d}Z"}
    states.write_text(json.dumps(st))


def test_flow_spawns_in_order_releases_without_waiting_and_finishes(env, tmp_path):
    main, log, states = env["main"], env["log"], env["states"]
    start(main)
    until(lambda: spawned(log) == ["la"])  # lb は la が止まるまで起動しない（I8）
    states.write_text(json.dumps({"la": "running"}))
    # la の依頼 1 件で、他のレーンを待たずに提示 1 を組む（I9）
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release" and e)
    assert ev["n"] == 1 and ev["k"] == 1 and "spawn" not in ev
    until(lambda: helper_spawns(log) == ["p1-rel1"])  # 提示の一時レーンは耐久ワークフローが起動する（#2936）
    helper_up(states, "p1-rel1")
    d = prl.release_dir(main, "p1", 1, 1)
    (d / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10, 11]}))
    set_state(states, "p1-rel1", "stopped")  # 提示の一時レーンはリリース承認ゲートの依頼を送って自分を止める
    # 先頭が同じなら確定（I14）
    durable.send_to(pl.KIND, pl.record_identity(main, "p1"), pl.workflow_id("p1"), {"n": 1, "phase": "confirm"}, "resume", "c1")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed")
    until(lambda: helper_spawns(log) == ["p1-rel1", "p1-rel1"])  # 確定の一時レーンも耐久ワークフローが起動する
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
    assert lanes_spawned(log) == ["la", "lb"]  # 1 本のレーンは 1 回だけ起動する（I7）
    assert ["role", "--clear", "--plan-dir", str(pp.plan_dir(main, "p1").resolve())] in calls(log, "role")


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
    plan_dir = str(pp.plan_dir(main, "p1").resolve())
    assert calls(log, "role")[0] == ["role", "organizer", "--plan", "p1", "--plan-dir", plan_dir]
    assert ["role", "--clear", "--plan-dir", plan_dir] in calls(log, "role")


def test_char_awaiting_approval_is_recorded_without_a_notice(env):
    """承認待ちへ移ったレーンは状態に写すだけで、オーガナイザーへの知らせを立てない（#2936 の I5。入る 1 行は status から引く）。"""
    main, states = env["main"], env["states"]
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    first = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "running" and e)
    states.write_text(json.dumps({"la": "awaiting_approval"}))
    time.sleep(0.8)
    assert event(main, "p1", "pause")["seq"] == first["seq"] and event(main, "p1", "questions") is None
    out = stop(main)
    assert out["status"] == "stopped" and out["not_stopped"] == [] and out["lanes"]["la"]["state"] == "awaiting_approval"


def test_char_build_error_fails_release_and_stops(env, tmp_path, monkeypatch):
    """リリースだけが失敗で止まり、計画は止まらない（#2616 の受け入れ条件 10・14 と I4・I5）。"""
    main, log, states = env["main"], env["log"], env["states"]
    monkeypatch.setattr(prun, "dev_version", lambda _m: "")
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    ans = read(main, rid)
    assert (ans["result"], ans["version"], ans["release"]) == ("failed", None, 1) and "出す版を決められない" in ans["reason"]
    q = build_question(main)
    assert q["id"] == "p1-build-1" and q["options"] == ["直してから組み直す", "止める"] and [r["id"] for r in q["requests"]] == [rid]
    assert "lane" not in q and "attach" not in q
    states.write_text(json.dumps({"la": "stopped"}))
    until(lambda: "lb" in spawned(log))  # 問いが開いていても依存先のレーンは起動する
    out = stop(main)
    assert out["status"] == "stopped" and out["pending"] == []
    rel = out["releases"][0]
    assert (rel["n"], rel["state"], rel["prs"], rel["requests"]) == (1, "failed", [10], [rid]) and "出す版を決められない" in rel["reason"]


def test_char_rejected_settle_answers_with_reason(env, tmp_path):
    main, states = env["main"], env["states"]
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    (prl.release_dir(main, "p1", 1, 1) / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10]}))
    resume(main, {"n": 1, "phase": "confirm"}, "c1")
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed" and e)
    assert ev["n"] == 1 and ev["k"] == 1 and "spawn" not in ev
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
    assert event(main, "p1", "range-1") == {"sha": "sha1", "version": "1.2.4", "prs": [10], "added": [], "lanes": ["la"]}
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
    pl.send_release_request(main, "p1", "la", [10], [1], env["main"].parent / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    ident = pl.record_identity(main, "p1")
    got = durable.wait_from(pl.KIND, ident, wid, ["pause", "questions"], first["seq"], 5)
    assert got.kind == "event" and got.value["key"] == "questions" and got.value["seq"] == q["seq"]
    got = durable.wait_from(pl.KIND, ident, wid, ["pause", "questions"], q["seq"], 5)
    assert got.kind == "event" and got.value["key"] == "pause" and got.value["action"] == "release"
    resume(main, {"n": 1, "phase": "settle", "result": "rejected", "reason": "test"}, "s1")
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
    until(lambda: [i for i in questions(main)["items"] if i["id"] == again])  # 同じ行の新しい依頼が前の依頼を置き換える（#2510 の I11）
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
    assert (
        item["lane"] == "la" and item["options"] == ["起こし直す", "止める", "待つ"] and "commands" not in item
    )  # 答えは耐久ワークフローが当てる
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


@pytest.mark.parametrize("value, want", [("5", 5), (None, 3), ("0", 3), ("x", 3)])
def test_the_refusals_before_asking_can_be_overridden(monkeypatch, value, want):
    if value is None:
        monkeypatch.delenv("NDF_PARALLEL_SPAWN_REFUSALS", raising=False)
    else:
        monkeypatch.setenv("NDF_PARALLEL_SPAWN_REFUSALS", value)
    assert prun.spawn_refusals() == want


def test_five_spawn_refusals_raise_a_question_when_overridden(env, monkeypatch):
    monkeypatch.setenv("FAKE_REFUSE", "la")
    monkeypatch.setenv("NDF_PARALLEL_SPAWN_REFUSALS", "5")
    main = env["main"]
    start(main)
    item = until(lambda: [i for i in questions(main)["items"] if i["kind"] == "spawn"])[0]
    assert "5 回" in item["question"] and len(calls(env["log"], "spawn")) >= 5


def test_a_stopped_helper_without_settle_is_asked(env, tmp_path):
    main, states = env["main"], env["states"]
    start(main)
    pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    helper_up(states, "p1-rel1")
    set_state(states, "p1-rel1", "stopped")  # 今の起動の後に、依頼も settle も送らずに止まった
    item = until(lambda: [i for i in questions(main)["items"] if i["kind"] == "helper"])[0]
    assert item["options"] == ["続きから起こし直す", "止める"] and "p1-rel1" in item["question"]


def test_stop_answer_to_a_stopped_helper_fails_the_release(env, tmp_path):
    """一時レーンの問いへ「止める」と答えると、リリースを失敗として締め、依頼へ failed を答える。"""
    main, states = env["main"], env["states"]
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    helper_up(states, "p1-rel1")
    set_state(states, "p1-rel1", "stopped")  # 今の起動の後に、依頼も settle も送らずに止まった
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
    """全レーンが止まった後に設計の承認へ答えても、持ち主が答えを当てるまで計画を終えず、耐久ワークフローが持ち主を再起動する（6f・#2936）。"""
    main, states = env["main"], env["states"]
    applied = {"v": False}
    monkeypatch.setattr(prun.prt, "design_held", lambda m, n, answered: ["la"] if answered and not applied["v"] else [])
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    rid = pl.request_ask(main, "p1", "la", ASK, str(tmp_path), "d1")
    until(lambda: questions(main)["items"])
    states.write_text(json.dumps({"la": "stopped", "lb": "stopped"}))
    time.sleep(1.0)
    assert "lb" not in spawned(env["log"])  # 保留中の la は依存の判定で止まったと数えない（I8）
    pl.send_answer(main, "p1", rid, "answered", choice=["済んだ"])
    until(lambda: ["restart", "la"] in calls(env["log"], "restart"))
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
    monkeypatch.setattr(prun.prt, "held_loops", lambda m, n, answered: ["lroot"] if answered else [])
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    (prl.release_dir(main, "p1", 1, 1) / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10]}))
    resume(main, {"n": 1, "phase": "confirm"}, "c1")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed")
    resume(main, {"n": 1, "phase": "settle", "result": "released", "version": "1.2.3"}, "s1")
    until(lambda: pl.read_answer(main, "p1", rid)[0])
    time.sleep(1.0)  # 範囲の止まったレーンの再起動（偽の relay.py が一覧へ running を書く）を済ませてから止める
    states.write_text(json.dumps({"la": "stopped", "lb": "stopped"}))
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "held" and e)
    assert ev["loops"] == ["lroot"] and "spawn" not in ev
    got = until(lambda: [c for c in calls(env["log"], "spawn") if "--parallel-helper p1 pending lroot" in c[-1]])
    assert got[0][2] == "p1-rel2"  # 保留したループの 1 回の一時レーンも耐久ワークフローが起動する（#2936）
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
    until(lambda: ["restart", "la"] in calls(log, "restart"))  # 範囲の止まったレーンは耐久ワークフローが再起動する（#2936）
    time.sleep(1.0)
    assert calls(log, "restart").count(["restart", "la"]) == 1  # 立ち上がったら打ち直さない
    assert all("wake" not in (event(main, "p1", k) or {}) for k in ("pause", "questions"))


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
    until(lambda: ["restart", "la"] in calls(env["log"], "restart"))  # 止まった持ち主を再起動し、答えを当てるまで終えない
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
    """耐久ワークフローが立てた問い（helper・stall・spawn）の答えの後に 1 行を返さない。起こし直しは耐久ワークフローが打つ（#2936 の I1）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("parallel_cli", Path(prun.__file__).resolve().parents[1] / "parallel.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    items = [
        {"id": "h1", "kind": "helper", "lane": "p1-rel1"},
        {"id": "s1", "kind": "stall", "lane": "la"},
        {"id": "x1", "kind": "spawn", "lane": "p1-rel1"},
    ]
    monkeypatch.setattr(durable, "event_from", lambda *_a, **_k: {"items": items})
    for rid, choice in (("h1", "続きから起こし直す"), ("h1", "止める"), ("s1", "起こし直す"), ("s1", "止める"), ("x1", "起こし直す")):
        assert pa.owner_next(tmp_path, "p1", rid, [choice]) == (None, [])


@pytest.fixture
def settle_cli(monkeypatch, tmp_path):
    """`parallel.py` の `cmd_settle` を本体のまま呼ぶ（受け付けの判定と送り先だけ差し替え、送った知らせを記録する）。"""
    import argparse
    import importlib.util

    spec = importlib.util.spec_from_file_location("parallel_cli", Path(prun.__file__).resolve().parents[1] / "parallel.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    state = {"accept": {}}
    sent: list[tuple] = []
    monkeypatch.setattr(cli, "main_of", lambda: tmp_path)
    monkeypatch.setattr(cli, "accepting", lambda _m, _n: state["accept"])
    monkeypatch.setattr(cli.pl, "resume_plan", lambda m, name, msg, key: sent.append((m, name, msg, key)))

    def call(capsys, n=1, done=None, rejected=False, reason=None, name="p1"):
        a = argparse.Namespace(name=name, n=n, external=False, done=done, rejected=rejected, reason=reason)
        with pytest.raises(SystemExit) as ex:
            cli.cmd_settle(a)
        return ex.value.code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    queued = "。耐久ワークフローが動いていないため、台帳に残し作り直しの後に反映する"
    return {"call": call, "sent": sent, "state": state, "dir": tmp_path, "queued": queued}


def _done(d, items, **extra):
    p = d / "done.json"
    p.write_text(json.dumps({"items": items, **extra}))
    return str(p)


@pytest.mark.parametrize(
    ("items", "extra", "want_res", "want_version"),
    [
        ([{"result": "完了"}, {"result": "完了"}], {"version": "1.2.3"}, "released", "1.2.3"),
        ([{"result": "完了"}], {"metrics": {"version": "1.2.4"}}, "released", "1.2.4"),
        ([{"result": "完了"}], {"version": "1.2.3", "metrics": {"version": "9.9.9"}}, "released", "1.2.3"),
        ([{"result": "完了"}, {"result": "失敗"}], {"version": "1.2.3"}, "failed", "1.2.3"),
        ([], {}, "failed", None),
        (["x", {"result": "完了"}], {}, "released", None),  # dict でない行は数えない
        ([{}], {}, "failed", None),  # result の無い行は "None" と読む
    ],
)
def test_char_settle_reads_the_done_and_sends_the_result(settle_cli, capsys, items, extra, want_res, want_version):
    """現状固定: `settle --done` は done の行がすべて「完了」なら released、そうでなければ failed を送る。版は version か metrics.version。"""
    code, out = settle_cli["call"](capsys, n=2, done=_done(settle_cli["dir"], items, **extra))
    assert code == 0 and out["status"] == "ok"
    assert out["summary"] == f"リリース 2 の結果 {want_res} を送った"
    assert out["metrics"] == {"result": want_res, "version": want_version}
    msg = {"n": 2, "phase": "settle", "result": want_res, "version": want_version, "reason": None}
    assert settle_cli["sent"] == [(settle_cli["dir"], "p1", msg, "settle:2")]


def test_char_settle_rejected_sends_the_reason_and_a_queued_mark(settle_cli, capsys):
    """現状固定: `--rejected` は done を読まずに rejected を送り、失われた計画では知らせに残す旨と queued を添える。"""
    settle_cli["state"]["accept"] = {"queued": True}
    code, out = settle_cli["call"](capsys, n=3, rejected=True, reason="no")
    assert code == 0 and out["summary"] == f"リリース 3 の結果 rejected を送った{settle_cli['queued']}"
    assert out["metrics"] == {"result": "rejected", "version": None, "queued": True}
    msg = {"n": 3, "phase": "settle", "result": "rejected", "version": None, "reason": "no"}
    assert settle_cli["sent"] == [(settle_cli["dir"], "p1", msg, "settle:3")]


def test_char_settle_stops_at_a_gate_or_an_unreadable_done_without_sending(settle_cli, capsys):
    """現状固定: done に「関門」の行があれば 4、done を読めなければ 2 で止まり、どちらも送らない。計画名が無ければ 1。"""
    code, out = settle_cli["call"](capsys, done=_done(settle_cli["dir"], [{"result": "完了"}, {"result": "関門"}]))
    assert code == 4 and out["status"] == "stopped" and out["summary"] == "本番のプランが承認ゲートで止まっている（送らない）"
    code, out = settle_cli["call"](capsys, done=str(settle_cli["dir"] / "missing.json"))
    assert code == 2 and out["status"] == "stopped" and out["summary"].startswith("done を読めない: ")
    (settle_cli["dir"] / "bad.json").write_text("{")
    code, out = settle_cli["call"](capsys, done=str(settle_cli["dir"] / "bad.json"))
    assert code == 2 and out["summary"].startswith("done を読めない: ")
    code, out = settle_cli["call"](capsys, name=None, rejected=True)
    assert code == 1 and out["summary"] == "計画名か --external が要る"
    assert settle_cli["sent"] == []


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
    owners = lambda rid: pa.owner_lanes(tmp_path, "p1", rid, next(q for q in items if q["id"] == rid))  # noqa: E731
    assert owners("qa") == [] and owners("qb") == []
    (loop / "loop.json").write_text(json.dumps({"status": "running"}))
    (handoff / "issue-7.md").write_text("## 次にやること\n- 別の作業\n")
    assert owners("qa") == [] and owners("qb") == []
    (loop / "loop.json").write_text(json.dumps({"status": "stopped", "stop": {"condition": "waiting_only"}}))
    (handoff / "issue-7.md").write_text("## 次にやること\n- 依頼 qb の答えを読んで続ける\n")
    assert owners("qa") == ["la"] and owners("qb") == ["lb"]
    items.append({"id": "qh", "kind": "ask", "lane": "p1-rel1"})  # 記録を持たない一時レーンはラッパーが止まっているかで決める
    assert owners("qh") == ["p1-rel1"]
    # 同じ原因としてまとめた問いは、merged の持ち主もそれぞれの依頼の id で調べる（異常系 5）
    items.append({"id": "qm", "kind": "ask", "lane": "la", "merged": [{"id": "qb", "lane": "lb"}]})
    assert owners("qm") == ["la", "lb"]
    assert pa.owner_next(tmp_path, "p1", "qm")[0] is None  # 再起動は耐久ワークフローが打つ（#2936 の I1）


def test_a_helper_ends_by_stopping_its_own_wrapper():
    """一時レーンは helper-done の後に自分のラッパーを止めて終える（判断 fca6fe464619 の (a)）。止めないと対話の claude が
    入力待ちで残り、続きの一時レーンの起動が同じ名前か上限で断られる。リリースと保留したループの 1 回のどちらの起動も同じ。"""
    for spec in (prun.helper_spawn("p1", 2, 1), prun.helper_spawn("p1", 3, 0, pending="lroot")):
        argv = spec["argv"]
        name = argv[argv.index("--name") + 1]
        assert f"{prun.RELAY} stop {name}" in argv[argv.index("--") + 1]


def test_char_helper_spawn_and_verify_spawn_inputs():
    """現状固定: 一時レーンを起こす入力（helper_spawn・verify_spawn）の argv と cwd をそのまま記録する。"""
    import parallel_helper as ph

    def expect(helper, what, cwd):
        end = f"[ndf-parallel] helper-done p1 の行を出し、python3 {ph.RELAY} stop {helper} で自分のラッパーを止めたとき"
        first = f"/goal /ndf:development-workflow --parallel-helper p1 {what} -- 終わり: {end}"
        return {"argv": ["spawn", "--name", helper, "--role", "helper", "--plan", "p1", "--json", "--", first], "cwd": cwd}

    assert ph.helper_spawn("p1", 2, 1) == expect("p1-rel2", "2 1", None)
    assert ph.helper_spawn("p1", 3, 0, pending="lroot") == expect("p1-rel3", "pending lroot", None)
    assert ph.helper_spawn("p1", 3, 0, pending="lroot", cwd="/other") == expect("p1-rel3", "pending lroot", "/other")
    assert ph.helper_spawn("p1", 4, 2, cwd="/other") == expect("p1-rel4", "4 2", "/other")
    # verify_spawn は一時レーンの名前を引数で受け、cwd は常に None
    assert ph.verify_spawn("p1", "q1", "p1-ver1") == expect("p1-ver1", "verify q1", None)
    assert ph.verify_spawn("p1", "q1-again2", "h") == expect("h", "verify q1-again2", None)
    assert prun.helper_spawn is ph.helper_spawn


def _bare_run(monkeypatch, lanes):
    """耐久ワークフローの外で _Run を組む（知らせは捨てる）。起動の判定はマシンを数えず、テストが差し替えた step_spawn へ渡す。"""
    monkeypatch.setattr(durable, "set_event", lambda *_a, **_k: None)
    monkeypatch.setattr(prun, "step_admit", lambda m, n, names: prun.step_spawn(m, n, names))
    monkeypatch.setattr(prun, "step_progress", lambda *_a: None)
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
    monkeypatch.setattr(prun, "step_observe", lambda *_a: seen)
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


def test_stopping_a_lane_that_cannot_start_leaves_wake_and_revive(monkeypatch):
    """起動の断りの問いへ「止める」と答えたら、reply が加えた再開待ちと起こす 1 行からそのレーンを外す。
    残すと _finish_or_hold が計画を終えられず、利用者が止めたレーンの再開を案内し続ける。"""
    run = _bare_run(monkeypatch, [{"name": "la", "input": {"kind": "sprint"}}])
    run.lanes["la"].update(started=True, state="stopped")
    run.ask("spawn", "s1", "la", "起動できない", ["起こし直す", "止める"])
    run.reply({"id": "s1", "result": "answered", "choice": ["止める"]})
    assert run.lanes["la"].get("given_up") is True
    assert "la" not in run.revive and "la" not in run.wake and "la" not in run.revive_at


def _answers(monkeypatch):
    """立った答えのイベントを {キー: 値} で集める。"""
    box = {}
    monkeypatch.setattr(durable, "set_event", lambda key, value: box.update({key: value}))
    return box


def test_an_answer_to_an_unknown_id_is_held_until_the_question_arrives(monkeypatch, capsys):
    """一覧に無い id への答えは捨てずに保留して警告を残し、その id の問いが届いたら当てる（#2617）。"""
    run = _bare_run(monkeypatch, [{"name": "la"}])
    box = _answers(monkeypatch)
    run.take({"kind": "answer", "id": "q1", "result": "answered", "note": "n", "by": "user"})
    assert "answer-q1" not in box and "q1" not in run.answered
    assert "q1" in capsys.readouterr().err
    run.take({"kind": "ask", "id": "q1", "lane": "la", "question": "?"})
    assert box["answer-q1"]["result"] == "answered" and box["answer-q1"]["note"] == "n"
    assert run.questions == [] and "q1" in run.answered and run.held == {}


def test_a_held_answer_reaches_a_question_merged_by_cause(monkeypatch):
    """保留した答えの問いが同じ原因の問いへまとまったら、まとめた問いごと答える。"""
    run = _bare_run(monkeypatch, [{"name": "la"}, {"name": "lb"}])
    box = _answers(monkeypatch)
    run.questions = [{"id": "q0", "kind": "ask", "lane": "la", "cause": "c"}]
    run.take({"kind": "answer", "id": "q1", "result": "answered"})
    monkeypatch.setattr(prun, "cause_of", lambda _m: "c")
    run.take({"kind": "ask", "id": "q1", "lane": "lb"})
    assert box["answer-q0"]["result"] == box["answer-q1"]["result"] == "answered" and run.questions == []


def test_a_held_answer_reaches_a_question_the_workflow_asks_itself(monkeypatch):
    """耐久ワークフローが自分で立てる問いにも、保留した答えを当てる。"""
    run = _bare_run(monkeypatch, [{"name": "la"}])
    box = _answers(monkeypatch)
    run.take({"kind": "answer", "id": "s1", "result": "answered", "choice": ["止める"]})
    run.ask("spawn", "s1", "la", "起動できない", ["止める"])
    assert box["answer-s1"]["choice"] == ["止める"] and run.lanes["la"].get("given_up") is True


def test_a_repeated_answer_stands_once_and_warns_once(monkeypatch, capsys):
    """既に答えた id の重複は今のまま 1 回だけで、保留もしない。保留中の id の重複は最初の答えを保つ。"""
    run = _bare_run(monkeypatch, [{"name": "la"}])
    box = _answers(monkeypatch)
    run.questions = [{"id": "q1", "kind": "ask", "lane": "la"}]
    run.reply({"id": "q1", "result": "answered"})
    box.clear()
    run.reply({"id": "q1", "result": "rejected"})
    assert box == {} and run.held == {}
    capsys.readouterr()
    run.reply({"id": "q2", "result": "first"})
    run.reply({"id": "q2", "result": "second"})
    assert run.held["q2"]["result"] == "first" and capsys.readouterr().err.count("q2") == 1


def test_a_helper_that_stopped_after_asking_is_not_asked_about(monkeypatch):
    """一時レーンが ask を送って自分のラッパーを止めたときは、「リリースの途中で止まった」を問わない（6g）。"""
    run, helper = _helper_run(monkeypatch)
    rows = {helper: {"state": "stopped", "at": AFTER}}
    run.questions = [{"id": "qa", "kind": "ask", "lane": helper}]
    for _ in range(3):
        prun._ask_helper(run, rows)
    assert [q["kind"] for q in run.questions] == ["ask"]
    run.questions = []
    prun._ask_helper(run, rows)
    prun._ask_helper(run, rows)
    q = run.questions[0]
    assert (q["kind"], q["purpose"], q["launch"]["argv"], q["n"]) == ("helper", "rel-1-1-present", ["spawn", "x"], 1)


LAUNCHED, BEFORE, AFTER = "2026-11-10T00:00:10Z", "2026-11-10T00:00:00.000Z", "2026-11-10T00:01:00.000Z"


def _helper_run(monkeypatch, result="up"):
    """提示の一時レーンを今の起動として持つ _Run（起動の記録は LAUNCHED に `result`）。"""
    run = _bare_run(monkeypatch, [{"name": "la"}])
    run.current = {"n": 1}
    run.launch = {"seq": 3, "action": "release", "n": 1, "k": 1, "purpose": "rel-1-1-present", "argv": ["spawn", "x"], "cwd": None}
    run.launches = {"rel-1-1-present": {"lane": "p1-rel1", "result": result, "attempts": 1, "at": LAUNCHED, "reason": None}}
    return run, prl.helper_name("p1", 1)


def test_a_helper_stopped_after_the_launch_is_asked_only_on_the_second_sight(monkeypatch):
    """今の起動の後に止まった一時レーンは、2 回続けて止まって観えたときだけ問う（#2936 の I4）。動いて観えたら数え直す。"""
    run, helper = _helper_run(monkeypatch)
    prun._ask_helper(run, {helper: {"state": "stopped", "at": AFTER}})
    assert run.questions == []
    prun._ask_helper(run, {helper: {"state": "running", "at": AFTER}})
    prun._ask_helper(run, {helper: {"state": "stopped", "at": AFTER}})
    assert run.questions == []
    prun._ask_helper(run, {helper: {"state": "stopped", "at": AFTER}})
    assert [q["kind"] for q in run.questions] == ["helper"]


def test_a_helper_row_from_before_the_launch_is_not_asked(monkeypatch):
    """続きの一時レーンを起動した直後に一覧に残る前の起動の止まった行では問わない（#2629）。"""
    run, helper = _helper_run(monkeypatch)
    for _ in range(3):
        prun._ask_helper(run, {helper: {"state": "stopped", "at": BEFORE}})
    assert run.questions == []


def test_a_helper_row_without_a_time_is_not_asked(monkeypatch):
    run, helper = _helper_run(monkeypatch)
    for _ in range(3):
        prun._ask_helper(run, {helper: {"state": "stopped"}})
    assert run.questions == []


def test_a_helper_is_not_asked_before_its_launch_came_up(monkeypatch):
    """立ち上がりを確かめていない起動（記録が failed・まだ無い）の止まりは問わない。立ち上がらない起動は区分 spawn の問いが受け持つ。"""
    run, helper = _helper_run(monkeypatch, result="failed")
    for _ in range(3):
        prun._ask_helper(run, {helper: {"state": "stopped", "at": AFTER}})
    run.launches = {}
    for _ in range(3):
        prun._ask_helper(run, {helper: {"state": "stopped", "at": AFTER}})
    assert run.questions == []


def test_a_helper_that_sent_the_gate2_request_is_not_asked(monkeypatch):
    """リリース承認ゲートの依頼を送ってから止まった一時レーンは問わない（台帳を観測より先に読むため、依頼が先に入る）。"""
    run, helper = _helper_run(monkeypatch)
    run.questions = [{"id": "g", "kind": "gate2", "n": 1, "lane": helper}]
    for _ in range(3):
        prun._ask_helper(run, {helper: {"state": "stopped", "at": AFTER}})
    assert [q["kind"] for q in run.questions] == ["gate2"]


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
    monkeypatch.setattr(prun, "step_observe", lambda *_a: seen)
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
    monkeypatch.setattr(prun, "step_observe", lambda *_a: seen)
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
    monkeypatch.setattr(prun, "step_observe", lambda *_a: {"now": 1.0, "rows": {}})
    run.lanes["la"].update(started=True)
    prun._observe_run(run, "/m")
    assert run.lanes["la"]["state"] == "stopped" and prun._run_finished(run)


def test_step_observe_carries_the_exit_code_and_the_last_stderr_line(env, tmp_path):
    (tmp_path / "ls.fail").write_text("")
    got = prun.step_observe(str(env["main"]), "p1")
    assert got["code"] == 3 and got["err"] == "ls: hub に届かない" and got["rows"] == {} and got["can_ask"] is True


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


# ---------------------------------------------------------------- 一覧の失敗の問い（#2509 の I3・I4）


def _failing_run(monkeypatch):
    """起動済みのレーン 1 本の計画と、書き換えられる観測の結果。停滞の区切りは 1 分。"""
    monkeypatch.setenv("NDF_PARALLEL_STALL_MINUTES", "1")
    run = _bare_run(monkeypatch, [{"name": "la"}])
    run.lanes["la"].update(started=True, state="running")
    seen = {"now": 1000.0, "rows": {}, "code": 3, "err": "ls: hub に届かない", "can_ask": True}
    monkeypatch.setattr(prun, "step_observe", lambda *_a: seen)
    return run, seen


def _observe_at(run, seen, now):
    seen["now"] = now
    prun._observe_run(run, "/m")
    return [q for q in run.questions if q["kind"] == "observe"]


def test_a_long_ls_failure_raises_one_question_per_limit(monkeypatch):
    """受け入れ条件 4・I3: 失敗が区切りより長く続くと問いが 1 件立ち、本文に終了コードと標準エラーの末尾 1 行が入る。同じ区切りの間は増えない。"""
    run, seen = _failing_run(monkeypatch)
    assert _observe_at(run, seen, 1000.0) == [] and _observe_at(run, seen, 1059.0) == []
    got = _observe_at(run, seen, 1060.0)
    assert len(got) == 1
    q = got[0]
    assert q["id"] == "p1-observe-1060" and q["source"] == "ndf-ask:human-action"
    assert "終了コード 3" in q["question"] and "ls: hub に届かない" in q["question"] and "1 分" in q["question"]
    assert "lane" not in q and "attach" not in q
    assert q["options"] == ["ラッパーの環境を作り直す", "待つ"]
    assert q["commands"] == {"ラッパーの環境を作り直す": f"python3 {prun.RELAY} status"}
    assert len(_observe_at(run, seen, 1119.0)) == 1  # 同じ区切りの間は重ねない
    assert len(_observe_at(run, seen, 1120.0)) == 2  # 次の区切りで次の 1 件


def test_replaying_a_record_from_before_the_question_keeps_the_recorded_calls(monkeypatch):
    """決定 5: 問いを持たない版の失敗の記録の再生では問いも DBOS の呼び出しも足さない。続きの観測は続いた時間で問う。"""
    run, seen = _failing_run(monkeypatch)
    del seen["can_ask"]
    calls = []
    monkeypatch.setattr(durable, "set_event", lambda *a, **_k: calls.append(a))
    assert _observe_at(run, seen, 1000.0) == [] and _observe_at(run, seen, 1060.0) == []
    assert _observe_at(run, seen, 1200.0) == [] and calls == []
    seen["can_ask"] = True
    assert len(_observe_at(run, seen, 1201.0)) == 1 and len(calls) == 1


def test_a_waited_ls_question_is_not_asked_again_before_the_next_limit(monkeypatch):
    run, seen = _failing_run(monkeypatch)
    _observe_at(run, seen, 1000.0)
    rid = _observe_at(run, seen, 1060.0)[0]["id"]
    run.take({"kind": "answer", "id": rid, "result": "answered", "choice": ["待つ"]})
    assert run.questions == [] and run.lanes["la"]["state"] == "running"
    assert _observe_at(run, seen, 1119.0) == []
    assert len(_observe_at(run, seen, 1120.0)) == 1


def test_a_recovered_ls_drops_the_question_and_restarts_the_tracking(monkeypatch):
    """I4: 成功に戻ると答えていない一覧の失敗の問いが外れ、失敗の始まりも数え直す。ほかの問いは残す。"""
    run, seen = _failing_run(monkeypatch)
    run.questions.append({"id": "q1", "kind": "ask", "lane": "la"})
    _observe_at(run, seen, 1000.0)
    assert len(_observe_at(run, seen, 1060.0)) == 1
    seen["code"] = 0
    assert _observe_at(run, seen, 1061.0) == [] and [q["id"] for q in run.questions] == ["q1"]
    seen["code"] = 3
    assert _observe_at(run, seen, 1062.0) == []
    assert _observe_at(run, seen, 1121.0) == []  # 新しい始まり（1062）から区切りまでは問わない
    assert len(_observe_at(run, seen, 1122.0)) == 1


def test_a_recovered_ls_lets_the_plan_finish_with_the_question_gone(env, tmp_path, monkeypatch):
    """I3・I4: 耐久ワークフローを通しても、失敗が続けば問いが届き、成功に戻れば問いが外れて計画を終える。"""
    main = env["main"]
    monkeypatch.setattr(prun, "stall_seconds", lambda: 0.3)
    fail = tmp_path / "ls.fail"
    fail.write_text("")
    start(main)
    item = until(lambda: [i for i in questions(main)["items"] if i["kind"] == "observe"])[0]
    assert "終了コード 3" in item["question"] and "lane" not in item
    assert durable.status(pl.workflow_id("p1")) in pl.RUNNING
    fail.unlink()
    out = until(lambda: durable.status(pl.workflow_id("p1")) not in pl.RUNNING and durable.output_of(pl.workflow_id("p1")))
    assert out["status"] == "finished" and not [i for i in questions(main)["items"] if i["kind"] == "observe"]


def test_answering_rebuild_returns_the_status_command(tmp_path):
    item = {"id": "p1-observe-1", "kind": "observe", "commands": {"ラッパーの環境を作り直す": "python3 relay.py status"}}
    assert pa.owner_next(tmp_path, "p1", item["id"], ["ラッパーの環境を作り直す"], [item])[0] == "python3 relay.py status"
    assert pa.owner_next(tmp_path, "p1", item["id"], ["待つ"], [item])[0] is None


# ---- リリースの組み方（#2616） ----

WORKTREE = {"base_branch": "develop", "production_branch": "main"}
CD = [{"kind": "auto", "channel": "base", "target": "stg"}, {"kind": "auto", "channel": "production", "target": "prod", "production": True}]
HAND = [{"kind": "manual", "target": "prod", "trigger": "手で流す", "production": True}]


def declare(main, delivery="absent", form=False):
    """配る形の宣言を置く。`delivery` の既定は宣言が無い形。文字列はそのまま書く。`form` が真なら `release.form`。"""
    sv = main / ".ndf" / "supervise.json"
    sv.unlink(missing_ok=True)
    if form:
        sv.write_text(json.dumps(TEMPLATE_DECL))
    (main / ".ndf" / "worktree.json").write_text(json.dumps(WORKTREE))
    pj = main / ".ndf" / "project.json"
    pj.unlink(missing_ok=True)
    if delivery != "absent":
        pj.write_text(delivery if isinstance(delivery, str) else json.dumps({"delivery": delivery}))


def read(main, rid):
    return until(lambda: pl.read_answer(main, "p1", rid)[0])


def build_question(main):
    return until(lambda: next((q for q in (event(main, "p1", "questions") or {}).get("items") or [] if q.get("kind") == "build"), None))


def no_build_ran(main):
    """`parallel.build` は最初に依頼の一覧を書く。一覧も提示の置き場も無ければ呼ばれていない。"""
    return not (pp.plan_dir(main, "p1") / "releases").exists()


@pytest.mark.parametrize("rows, said", [(CD, "stg はマージで反映する"), ([], "デプロイしない")])
def test_merge_or_no_delivery_repo_answers_released_without_building(env, tmp_path, rows, said):
    """受け入れ条件 1〜3・5・9。"""
    main, log, states = env["main"], env["log"], env["states"]
    declare(main, rows)
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    ans = read(main, rid)
    assert (ans["result"], ans["version"], ans["release"]) == ("released", None, 1) and said in ans["reason"]
    assert no_build_ran(main) and event(main, "p1", "pause")["action"] == "running" and event(main, "p1", "range-1") is None
    states.write_text(json.dumps({"la": "stopped"}))
    until(lambda: "lb" in spawned(log))
    states.write_text(json.dumps({"la": "stopped", "lb": "stopped"}))
    out = durable.output_of(pl.workflow_id("p1"))
    assert out["status"] == "finished" and out["pending"] == [] and no_build_ran(main)
    rel = out["releases"][0]
    assert (rel["state"], rel["version"], rel["prs"], rel["skipped"]) == ("released", None, [10], True) and said in rel["reason"]
    assert "| 1 | released | - | - |" in (pp.plan_dir(main, "p1") / "plan.md").read_text()


@pytest.mark.parametrize(
    "rows, said",
    [
        (HAND, "手で流す"),
        ([CD[0], HAND[0]], "手で流す"),
        ("absent", "delivery も"),
        (json.dumps({"delivery": {"unknown": "調べていない"}}), "調べていない"),
        ("{", "読めない"),
    ],
)
def test_manual_or_unreadable_repo_answers_failed_and_keeps_running(env, tmp_path, rows, said):
    """受け入れ条件 6・7。"""
    main, states = env["main"], env["states"]
    declare(main, rows)
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    ans = read(main, rid)
    assert ans["result"] == "failed" and said in ans["reason"] and "project.json" in ans["reason"]
    assert no_build_ran(main) and build_question(main)["reason"] == ans["reason"]
    assert durable.status(pl.workflow_id("p1")) in pl.RUNNING


def test_template_repo_still_builds(env, tmp_path):
    """受け入れ条件 8。"""
    main = env["main"]
    start(main)
    pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    assert (prl.release_dir(main, "p1", 1, 1) / "present.json").is_file()


def test_build_exception_and_seq_limit_answer_failed(env, tmp_path, monkeypatch):
    """受け入れ条件 11・12。組み立ての例外と連番の上限は、同じ答えと問いになる。"""
    main, states = env["main"], env["states"]

    def boom(*_a, **_k):
        raise RuntimeError("組み立ての失敗")

    monkeypatch.setattr(prl, "build_plans", boom)
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    r1 = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "s1")
    ans = read(main, r1)
    assert ans["result"] == "failed" and "組み立ての失敗" in ans["reason"] and build_question(main)["id"] == "p1-build-1"
    monkeypatch.setattr(prl, "MAX_HELPER_SEQ", 0)
    r2 = pl.send_release_request(main, "p1", "la", [20], [2], tmp_path / "s2")
    ans = read(main, r2)
    assert ans["result"] == "failed" and ans["release"] == 2 and "上限" in ans["reason"]
    assert stop(main)["status"] == "stopped"


def test_same_build_cause_keeps_one_question_across_releases(env, tmp_path, monkeypatch):
    """I6。同じ原因の組み立ての例外は、リリースの連番が進んでも問いが 1 件のままである。"""
    main, states = env["main"], env["states"]

    def boom(*_a, **_k):
        raise RuntimeError("組み立ての失敗")

    monkeypatch.setattr(prl, "build_plans", boom)
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    r1 = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "s1")
    assert read(main, r1)["release"] == 1  # 台帳の行はループの 1 周でまとめて読むため、答えの後に次の依頼を送る
    r2 = pl.send_release_request(main, "p1", "la", [20], [2], tmp_path / "s2")
    assert read(main, r2)["release"] == 2
    q = until(lambda: len((got := build_question(main))["requests"]) == 2 and got)
    assert q["id"] == "p1-build-1" and [r["id"] for r in q["requests"]] == [r1, r2]
    assert len([x for x in event(main, "p1", "questions")["items"] if x.get("kind") == "build"]) == 1
    assert stop(main)["status"] == "stopped"


def test_second_present_failure_closes_the_range_and_defers_the_rest(env, tmp_path, monkeypatch):
    """受け入れ条件 13・14（I4・I6）。範囲の外の依頼は次のリリースで答えを受け、理由が同じなら問いは 1 件のままである。"""
    main, heads, states = env["main"], env["heads"], env["states"]
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    r1 = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "s1")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    r2 = pl.send_release_request(main, "p1", "la", [20], [2], tmp_path / "s2")
    until(lambda: (event(main, "p1", "lane-la") or {}).get("received") == 2)
    (prl.release_dir(main, "p1", 1, 1) / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10]}))
    heads["v"] = "sha2"
    monkeypatch.setattr(prun, "dev_version", lambda _m: "")
    resume(main, {"n": 1, "phase": "confirm"}, "c1")
    first, second = read(main, r1), read(main, r2)
    assert (first["result"], first["release"]) == ("failed", 1) and (second["result"], second["release"]) == ("failed", 2)
    q = until(lambda: len((got := build_question(main))["requests"]) == 2 and got)
    assert q["id"] == "p1-build-1" and [r["id"] for r in q["requests"]] == [r1, r2]
    assert len([x for x in event(main, "p1", "questions")["items"] if x.get("kind") == "build"]) == 1
    assert durable.status(pl.workflow_id("p1")) in pl.RUNNING


def test_stop_choice_stops_the_plan(env, tmp_path):
    """受け入れ条件 15 の「止める」。"""
    main, states = env["main"], env["states"]
    declare(main, HAND)
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    pl.send_answer(main, "p1", build_question(main)["id"], "answered", choice=["止める"])
    assert durable.output_of(pl.workflow_id("p1"))["status"] == "stopped"


def test_rebuild_choice_reads_the_fixed_declaration_and_builds(env, tmp_path):
    """受け入れ条件 15 の「直してから組み直す」と決定 5。元の依頼の答えは変えず、計画は組み直しが流れ切るまで終わらない。"""
    main, log, states = env["main"], env["log"], env["states"]
    declare(main, HAND)
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    failed = read(main, rid)
    q = build_question(main)
    declare(main, form=True)
    pl.send_answer(main, "p1", q["id"], "answered", choice=["直してから組み直す"])
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release" and e)
    assert ev["n"] == 2 and pl.read_answer(main, "p1", rid)[0] == failed
    (prl.release_dir(main, "p1", 2, 1) / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10]}))
    resume(main, {"n": 2, "phase": "confirm"}, "c1")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed")
    states.write_text(json.dumps({"la": "stopped", "lb": "stopped"}))
    until(lambda: "lb" in spawned(log))
    time.sleep(0.6)
    assert durable.status(pl.workflow_id("p1")) in pl.RUNNING
    resume(main, {"n": 2, "phase": "settle", "result": "released", "version": "1.2.3"}, "s1")
    out = durable.output_of(pl.workflow_id("p1"))
    assert out["status"] == "finished" and out["pending"] == []
    assert [(r["n"], r["state"]) for r in out["releases"]] == [(1, "failed"), (2, "released")]
    assert out["releases"][1]["requests"] == [f"{rid}-again2"]
    assert read(main, f"{rid}-again2")["result"] == "released" and "wake" not in event(main, "p1", "pause")


CRASH = textwrap.dedent(
    """
    import os, sys, time
    from pathlib import Path
    main, mode, state = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    sys.path[:0] = [sys.argv[4], sys.argv[4] + "/lib"]
    import dbos
    import durable, parallel_lane as pl, parallel_release as prl, parallel_run as prun
    prun.dev_version = lambda _m: "1.2.3"
    prun.git_head = lambda _m: "sha1"
    orig = prl.build_plans
    prl.build_plans = lambda m, n, s, k, v: orig(m, n, s, k, v, lambda *_a: ({"steps": []}, {"steps": []}))
    if mode == "old":
        durable.patched = lambda _n: False  # parallel.route の無い版の並びで記録する
    else:
        orig_patched = durable.patched  # env と同じ記録の道（LEGACY_MARKS）
        durable.patched = lambda n: False if n in ("parallel-lane-end", "parallel-approved-sha") else orig_patched(n)
    prun.open_parallel_record(main, "p1")
    wid = pl.workflow_id("p1")
    durable.start(durable.WorkflowRef(wid, 1, "start"), prun.parallel_workflow, str(main), "p1")
    rid = pl.send_release_request(main, "p1", "la", [10], [1], state)
    end = time.monotonic() + 30
    while time.monotonic() < end:
        ev = durable.event(wid, "pause") or {}
        if ev.get("action") == "release" or pl.read_answer(main, "p1", rid)[0]:
            print(rid, flush=True)
            os._exit(0)
        time.sleep(0.1)
    os._exit(1)
    """
)


def crash_after_first_request(env, tmp_path, mode):
    """別のプロセスで最初の依頼まで記録し、後始末をせずに落とす。依頼の ID を返す。"""
    script, states = tmp_path / "crash.py", env["states"]
    script.write_text(CRASH)
    states.write_text(json.dumps({"la": "running"}))
    argv = [sys.executable, str(script), str(env["main"]), mode, str(tmp_path / "st"), str(SCRIPTS)]
    got = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, got.stderr[-2000:]
    return got.stdout.strip().splitlines()[-1]


def test_recorded_run_of_the_old_steps_continues_with_the_new_code(env, tmp_path):
    """I7: `parallel.build` まで古い並びで記録した実行を続けても落ちず、次の依頼は新しい処理を通る。"""
    main = env["main"]
    rid = crash_after_first_request(env, tmp_path, "old")
    prun.open_parallel_record(main, "p1")  # 落ちた実行を記録から続ける
    (prl.release_dir(main, "p1", 1, 1) / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10]}))
    resume(main, {"n": 1, "phase": "confirm"}, "c1")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed")
    resume(main, {"n": 1, "phase": "settle", "result": "released", "version": "1.2.3"}, "s1")
    assert read(main, rid)["version"] == "1.2.3"
    declare(main, CD)
    r2 = pl.send_release_request(main, "p1", "la", [20], [2], tmp_path / "s2")
    ans = read(main, r2)
    assert (ans["result"], ans["version"], ans["release"]) == ("released", None, 2)
    assert durable.status(pl.workflow_id("p1")) in pl.RUNNING
    assert not (pp.plan_dir(main, "p1") / "checkpoint.json").exists()  # 途中の記録は台帳を読まない今までの道を通る（#2647 の条件 19）


def test_new_record_replays_to_the_same_values(env, tmp_path):
    """I7: 新しい手順の記録は、落ちた後の打ち直しで同じ値に戻る。宣言が変わっても記録した分岐を通る。"""
    main = env["main"]
    declare(main, CD)
    rid = crash_after_first_request(env, tmp_path, "new")
    declare(main, HAND)
    prun.open_parallel_record(main, "p1")
    out = stop(main)
    assert out["status"] == "stopped" and [(r["n"], r["state"], r["requests"]) for r in out["releases"]] == [(1, "released", [rid])]


# ---- 答えの届け方（#2510） ----


def _route(monkeypatch, lanes=None):
    run = _bare_run(monkeypatch, lanes or [{"name": "la", "input": {"kind": "loop"}}, {"name": "lb", "input": {"kind": "loop"}}])
    box = _answers(monkeypatch)
    for ln in run.lanes.values():
        ln.update(started=True, state="stopped")
    return run, box


def test_an_answer_wakes_a_stopped_loop_lane_that_waits_on_its_record(monkeypatch):
    """記録が waiting_only で止まったループのレーンも再起動の対象に入る（#2474 の原因）。"""
    run, box = _route(monkeypatch)
    monkeypatch.setattr(parallel_reply, "step_waiting_lanes", lambda *_a: ["la"])
    run.questions = [{"kind": "gate1", "id": "q", "lane": "la", "pr": 1}]
    run.reply({"id": "q", "result": "approved"})
    assert box["answer-q"]["result"] == "approved" and run.revive == ["la"] and run.wake == ["la"]
    assert "pause" not in box  # 再起動は周の終わりに耐久ワークフローが打つ。知らせは立てない（#2936）


def test_a_stopped_loop_lane_that_does_not_wait_is_not_woken(monkeypatch):
    run, _ = _route(monkeypatch)
    monkeypatch.setattr(parallel_reply, "step_waiting_lanes", lambda *_a: [])
    run.questions = [{"kind": "gate1", "id": "q", "lane": "la", "pr": 1}]
    run.reply({"id": "q", "result": "approved"})
    assert run.revive == [] and run.wake == []


def test_an_unknown_answer_is_held_and_shown_as_unmatched_until_the_question_arrives(monkeypatch):
    run, box = _route(monkeypatch)
    run.take({"kind": "answer", "id": "q1", "result": "approved"})
    q = run.questions[0]
    assert (q["kind"], q["id"], q["answered"], q["options"]) == ("unmatched", "q1-unmatched", "q1", ["確かめた"])
    assert q["reason"] and box["questions"]["items"] == [q]
    run.take({"kind": "ask", "id": "q1", "lane": "la", "question": "?"})
    assert run.questions == [] and box["answer-q1"]["result"] == "approved"


def test_a_lane_notice_of_an_unmatched_answer_blocks_the_finish_until_answered(monkeypatch):
    run, _ = _route(monkeypatch)
    run.take({"kind": "unmatched", "id": "a-unmatched", "answered": "a", "lane": "la", "reason": "動いた", "question": "?"})
    run.ls_ok = True
    assert not prun._run_finished(run)
    run.reply({"id": "a-unmatched", "result": "answered", "choice": ["確かめた"]})
    assert prun._run_finished(run)


def test_a_new_request_of_the_same_row_replaces_the_old_and_the_old_answer_wakes_the_owner(monkeypatch):
    run, box = _route(monkeypatch)
    monkeypatch.setattr(parallel_reply, "step_waiting_lanes", lambda *_a: ["la"])
    run.take({"kind": "gate1", "id": "la-g1-a", "lane": "la", "pr": 1, "stamp": "a"})
    run.take({"kind": "gate1", "id": "la-g1-b", "lane": "la", "pr": 1, "stamp": "b"})
    run.take({"kind": "gate1", "id": "la-g1-c", "lane": "la", "pr": 1, "stamp": "c"})
    run.take({"kind": "gate1", "id": "lb-g2-a", "lane": "lb", "pr": 2, "stamp": "z"})
    q = run.questions[0]
    assert [x["id"] for x in run.questions] == ["la-g1-c", "lb-g2-a"]
    assert q["replaced"] == [{"id": "la-g1-b", "stamp": "b"}, {"id": "la-g1-a", "stamp": "a"}]
    run.reply({"id": "la-g1-a", "result": "approved"})
    assert box["answer-la-g1-a"]["result"] == "approved" and run.wake == ["la"] and len(run.questions) == 2


PRIOR = {"kind": "gate1", "id": "lroot2-w12-l3", "loop": "lroot2", "issue": 12, "pr": 412, "lap": 3, "stamp": "a"}


def test_prior_waits_enter_the_list_once_and_do_not_block_the_finish(monkeypatch):
    run, _ = _route(monkeypatch)
    run.receive_prior({"requests": [PRIOR], "skipped": []})
    run.take(dict(PRIOR))  # レーンのループの 1 回の境からの同じ行
    assert [q["id"] for q in run.questions] == ["lroot2-w12-l3"] and prun._run_finished(run)
    assert [q["id"] for q in run.summary("finished")["prior_waits"]] == ["lroot2-w12-l3"]


def test_a_skipped_loop_is_shown_like_an_unmatched_answer(monkeypatch):
    run, _ = _route(monkeypatch)
    run.receive_prior({"requests": [], "skipped": [{"loop": "bad", "reason": "壊れている"}]})
    assert run.questions[0]["kind"] == "unmatched" and "bad" in run.questions[0]["question"]


def test_an_answer_to_a_prior_wait_goes_to_its_loop(monkeypatch):
    run, box = _route(monkeypatch)
    calls = []
    monkeypatch.setattr(
        parallel_reply, "step_prior_answer", lambda m, n, q, a, stamp=None: calls.append((q["id"], a["result"], stamp)) or {}
    )
    run.questions = [dict(PRIOR)]
    run.reply({"id": PRIOR["id"], "result": "approved"})
    assert calls == [(PRIOR["id"], "approved", None)] and run.questions == [] and box[f"answer-{PRIOR['id']}"]["result"] == "approved"
    assert run.revive == []


def test_a_prior_head_that_moved_replaces_the_question_and_an_old_answer_lands_on_merge_only(monkeypatch):
    run, _ = _route(monkeypatch)
    new = {**PRIOR, "id": "lroot2-w12-l3-bbb", "stamp": "b"}
    monkeypatch.setattr(parallel_reply, "step_prior_answer", lambda *a: {"reask": new})
    run.questions = [dict(PRIOR)]
    run.reply({"id": PRIOR["id"], "result": "approved"})
    assert [q["id"] for q in run.questions] == [new["id"]] and run.questions[0]["replaced"] == [{"id": PRIOR["id"], "stamp": "a"}]
    run.answered.discard(PRIOR["id"])  # 置き換える前の id へ後から答えた形
    calls = []
    monkeypatch.setattr(parallel_reply, "step_prior_answer", lambda m, n, q, a, stamp=None: calls.append(stamp) or {})
    run.reply({"id": PRIOR["id"], "result": "approved"})
    assert calls == ["a"] and run.questions == []


def test_a_prior_answer_that_cannot_land_is_unmatched(monkeypatch):
    run, _ = _route(monkeypatch)
    monkeypatch.setattr(parallel_reply, "step_prior_answer", lambda *a: {"unmatched": "断った"})
    run.questions = [dict(PRIOR)]
    run.reply({"id": PRIOR["id"], "result": "rejected"})
    assert [q["kind"] for q in run.questions] == ["unmatched"] and run.questions[0]["reason"] == "断った"


def test_a_replay_of_an_old_record_keeps_the_old_steps(monkeypatch):
    run, box = _route(monkeypatch)
    run.routed = False
    monkeypatch.setattr(parallel_reply, "step_waiting_lanes", lambda *_a: pytest.fail("古い記録の打ち直しでは打たない"))
    run.questions = [{"kind": "gate1", "id": "q", "lane": "la", "pr": 1}]
    run.reply({"id": "q", "result": "approved"})
    run.reply({"id": "unknown", "result": "approved"})
    assert run.questions == [] and "unknown" in run.held


def test_other_repo_lane_asks_through_the_plan_home_and_reads_the_answer(env, tmp_path):
    """#2733 の受け入れ条件 1・2: 別のリポジトリのレーンが計画を見つけ、問いが計画の置き場の依頼の一覧に（`repo` つきで）
    載り、答えをレーンの側から読める。"""
    import parallel_homes

    main = env["main"]
    other = tmp_path / "factory"
    (other / ".ndf").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    other = other.resolve()
    raw = json.loads((pp.plan_dir(main, "p1") / "plan.json").read_text())
    raw["lanes"].append({"name": "lf", "input": {"kind": "sprint", "issues": [1]}, "plans": 1, "touches": ["a/"], "repo": str(other)})
    (pp.plan_dir(main, "p1") / "plan.json").write_text(json.dumps(raw))
    parallel_homes.record_home(main)
    start(main)
    got = until(lambda: pl.lane_of(other, "lf"))
    assert got[0] == "p1" and got.home.resolve() == main.resolve() and got.own_release()
    rid = pl.request_ask(got.home, "p1", "lf", ASK, str(other), "led9")
    items = until(lambda: [i for i in questions(main)["items"] if i["id"] == rid])
    assert pa.with_repo(main, "p1", items)[0]["repo"] == str(other)
    pl.send_answer(main, "p1", rid, "answered", choice=["済んだ"])
    ans = until(lambda: pl.answers_of(pl.plan_home(other, "p1", "lf"), "p1", [rid])[rid]["answer"])
    assert ans["choice"] == ["済んだ"]


# ---- マシン全体のプランの本数による起動の待ち（#2999 の受け入れ条件 4・5、I12・I14・I15） ----


def _busy_machine(tmp_path, plans):
    """別のリポジトリに動いている計画 c1（レーン ox<i> の本数 `plans`）を置き、計画の所在の一覧へ書く。"""
    import parallel_homes

    other = tmp_path / "carul"
    (other / ".ndf").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    d = pp.plan_dir(other, "c1")
    d.mkdir(parents=True)
    lanes = [{"name": f"ox{i}", "input": {"kind": "sprint", "issues": [9]}, "plans": n, "touches": ["x/"]} for i, n in enumerate(plans)]
    (d / "plan.json").write_text(json.dumps({"version": 1, "name": "c1", "rules": [], "lanes": lanes}))
    (d / "run.done").write_text("PENDING")  # 動いている計画として読ませる（settled_status が読む）
    parallel_homes.record_home(other)
    return other


def test_a_lane_waits_for_room_on_the_machine_then_starts(env, tmp_path, monkeypatch):
    import parallel_status as pst

    monkeypatch.delenv("NDF_PARALLEL_MAX_PLANS", raising=False)
    main, log, states = env["main"], env["log"], env["states"]
    _busy_machine(tmp_path, [3, 3])
    states.write_text(json.dumps({"ox0": "running", "ox1": "awaiting_approval"}))  # マシンの合計 6
    start(main)
    plan = pp.load_valid_plan(main, "p1")
    row = until(lambda: [r for r in pst.lane_rows(main, "p1", plan) if r["lane"] == "la" and r["waiting"]])[0]
    w = row["waiting"]
    assert (w["reason"], w["machine_plans"], w["plans"], w["max_plans"]) == ("machine-plans", 6, 1, 6) and w["since"]
    assert "起動待ち（マシン 6 + このレーン 1 > 上限 6）" in (pp.plan_dir(main, "p1") / "plan.md").read_text()
    assert "起動待ち 1" in pst.status_summary(pst.lane_rows(main, "p1", plan))
    time.sleep(1.5)  # 見回りが何度も重なっても、待ちは断りに数えず問わない（I12）
    assert lanes_spawned(log) == [] and not [i for i in questions(main)["items"] if i["kind"] == "spawn"]
    set_state(states, "ox1", "stopped")  # 別のリポジトリのレーンが止まった（合計 3）
    until(lambda: lanes_spawned(log) == ["la"])
    until(lambda: not [r for r in pst.lane_rows(main, "p1", plan) if r["waiting"]])
    assert "起動待ち" not in (pp.plan_dir(main, "p1") / "plan.md").read_text()


def test_an_old_record_starts_lanes_without_counting_the_machine(env, tmp_path, monkeypatch):
    monkeypatch.delenv("NDF_PARALLEL_MAX_PLANS", raising=False)
    main, log, states = env["main"], env["log"], env["states"]
    _busy_machine(tmp_path, [3, 3])
    states.write_text(json.dumps({"ox0": "running", "ox1": "running"}))
    orig = durable.patched
    monkeypatch.setattr(durable, "patched", lambda n: False if n == "parallel-admit" else orig(n))
    start(main)
    until(lambda: lanes_spawned(log) == ["la"])  # 印の無い記録は今までの parallel.spawn を打つ（I15）


# ---------------------------------------------------------------- 中身の無い依頼（#3085。#3083 の設計の I3〜I7）


def _closing_run(monkeypatch, closed, releases=()):
    """2 本のレーンの _Run。la だけが止まり、Pull Request の状態の取得は `closed`（番号の並びか、失敗の理由の文字列）を返す。"""
    import parallel_route as prt

    run = _bare_run(monkeypatch, [{"name": "la"}, {"name": "lb"}])
    run.lanes["la"].update(started=True, state="stopped")
    run.releases = list(releases)
    got = {"answers": [], "fetched": 0}
    monkeypatch.setattr(
        run, "answer", lambda rid, result, version, n, reason=None: got["answers"].append((rid, result, version, n, reason))
    )

    def fetch(_main, prs):
        got["fetched"] += 1
        return {"closed": [], "error": closed} if isinstance(closed, str) else {"closed": [n for n in closed if n in prs], "error": ""}

    monkeypatch.setattr(prt, "step_closed_prs", fetch)  # 耐久ワークフローの外では耐久ステップごと差し替える
    return run, got


def test_a_request_whose_prs_all_closed_is_answered_and_dropped(monkeypatch):
    """受け入れ条件 5 / I3: すべて閉じた依頼へ released・版なし・閉じた番号の理由で答えて外し、組まない。止まった宛先は再起動の対象へ入る。"""
    run, got = _closing_run(monkeypatch, [3079])
    run.pending = [{"id": "lgate-r1", "lane": "la", "prs": [3079]}]
    assert run.drop_closed() is False and run.pending == []
    [(rid, result, version, n, reason)] = got["answers"]
    assert (rid, result, version, n) == ("lgate-r1", "released", None, None) and "#3079" in reason
    assert run.wake == ["la"]


@pytest.mark.parametrize("releases, answered", [([{"n": 1, "state": "released", "version": "1.2.3", "prs": [10]}], True), ([], False)])
def test_a_partly_closed_request_is_judged_by_its_remaining_prs(monkeypatch, releases, answered):
    """受け入れ条件 6 / I4: 一部が閉じた依頼は残りの番号で範囲の判定を受け、除いた番号は依頼に残す。"""
    run, got = _closing_run(monkeypatch, [11], releases)
    run.pending = [{"id": "r1", "lane": "la", "prs": [10, 11]}]
    assert run.drop_closed() is (not answered)
    if answered:
        assert run.pending == [] and got["answers"] == [("r1", "released", "1.2.3", 1, None)] and run.wake == ["la"]
    else:
        assert run.pending == [{"id": "r1", "lane": "la", "prs": [10], "closed": [11]}] and got["answers"] == []
        assert run.wake == []


@pytest.mark.parametrize("closed", [[], "gh に届かない"], ids=["open-or-merged", "fetch-failed"])
def test_requests_without_closed_prs_are_kept(monkeypatch, closed):
    """受け入れ条件 8 / I5: マージ済みか開いている Pull Request の依頼と、取得に失敗したときの依頼は外さずに組む。"""
    run, got = _closing_run(monkeypatch, closed)
    pending = [{"id": "r1", "lane": "la", "prs": [10]}]
    run.pending = list(pending)
    assert run.drop_closed() is True and run.pending == pending and got["answers"] == []


def test_closed_prs_reads_only_prs_closed_without_a_merge(monkeypatch):
    """I5: 閉じた番号は state が CLOSED のものだけで、読めない Pull Request と取得の失敗は閉じたと見なさない。"""
    import parallel_route as prt
    from release_lib import included

    states = {10: {"state": "CLOSED"}, 11: {"state": "MERGED"}, 12: {"state": "OPEN"}, 13: None}
    monkeypatch.setattr(included, "pr_fields", lambda prs, cwd=None: ({n: states[n] for n in prs}, ""))
    assert prt.closed_prs(Path("/m"), [10, 11, 12, 13]) == {"closed": [10], "error": ""}
    monkeypatch.setattr(included, "pr_fields", lambda prs, cwd=None: (None, "gh が無い"))
    assert prt.closed_prs(Path("/m"), [10]) == {"closed": [], "error": "gh が無い"}


def test_the_pr_states_are_read_only_right_before_building(monkeypatch):
    """I6: リリースが流れている見回りと計画全体の停止の見回りでは読まず、組もうとする見回りで 1 回だけ読む。"""
    import parallel_hold as ph

    run, got = _closing_run(monkeypatch, [])
    built = []
    monkeypatch.setattr(run, "open_release", lambda: built.append(1))
    run.pending = [{"id": "r1", "lane": "la", "prs": [10]}]
    run.current = {"n": 1}
    prun._tick_release(run, "/m", "p1", 0)
    run.current, run.hold = None, ph.Hold(plan=True)
    prun._tick_release(run, "/m", "p1", 0)
    assert got["fetched"] == 0 and built == []
    run.hold = ph.Hold()
    prun._tick_release(run, "/m", "p1", 0)
    assert got["fetched"] == 1 and built == [1]


def test_a_closed_request_lets_the_plan_finish(env, tmp_path, monkeypatch):
    """受け入れ条件 5・7: すべて閉じた依頼だけを受けた計画は組まずに答え、全レーンが止まれば finished で終わる。"""
    import parallel_route as prt

    main, log, states = env["main"], env["log"], env["states"]
    monkeypatch.setattr(prt, "closed_prs", lambda _m, prs: {"closed": list(prs), "error": ""})
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [3079], [1], tmp_path / "st")
    ans = read(main, rid)
    assert (ans["result"], ans["version"], ans["release"]) == ("released", None, None) and "#3079" in ans["reason"]
    states.write_text(json.dumps({"la": "stopped"}))
    until(lambda: "lb" in spawned(log))
    states.write_text(json.dumps({"la": "stopped", "lb": "stopped"}))
    out = until(lambda: durable.output_of(pl.workflow_id("p1")))
    assert out["status"] == "finished" and out["pending"] == [] and out["releases"] == [] and no_build_ran(main)


def test_an_old_record_does_not_read_the_pr_states(env, tmp_path, monkeypatch):
    """I7: 印 parallel-closed-requests の無い記録は Pull Request の状態を読まず、今までどおり依頼から組む。"""
    import parallel_route as prt

    main, states = env["main"], env["states"]
    orig = durable.patched
    monkeypatch.setattr(durable, "patched", lambda n: False if n == "parallel-closed-requests" else orig(n))
    monkeypatch.setattr(prt, "closed_prs", lambda *_a: pytest.fail("古い記録で Pull Request の状態を読んだ"))
    states.write_text(json.dumps({"la": "running"}))
    start(main)
    pl.send_release_request(main, "p1", "la", [3079], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
