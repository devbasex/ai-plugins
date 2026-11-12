"""耐久ワークフローの作り直し（#2647）: 耐久の記録を実際に開き、ERROR か記録の消失から台帳で状態を戻す。"""

from __future__ import annotations

import json
import time
import subprocess
import sys
import threading

import pytest

from test_parallel_run import SCRIPTS, calls, env, event, helper_spawns, lanes_spawned, questions, read, spawned, start, until  # noqa: F401,E501  env は fixture
import durable  # noqa: E402
import parallel_lane as pl  # noqa: E402
import parallel_ledger as plg  # noqa: E402
import parallel_release as prl  # noqa: E402
import parallel_run as prun  # noqa: E402
from durable_keys import record_path  # noqa: E402

WID = pl.workflow_id("p1")


@pytest.fixture(autouse=True)
def records(tmp_path, monkeypatch):
    """耐久の記録と退避の置き場を一時のディレクトリへ向ける（共有の置き場へ退避のファイルを残さない）。"""
    monkeypatch.setenv("NDF_DBOS_DIR", str(tmp_path / "dbos"))


ASK = {"questions": [{"question": "どちらで進めるか", "options": [{"label": "A"}, {"label": "B"}]}]}


def ask(main, ledger_id="L1"):
    return pl.request_ask(main, "p1", "la", ASK, str(main), ledger_id)


def running(main, log, states):
    states.write_text(json.dumps({"la": "running"}))
    plg.open_run(main, "p1", None)  # run_plan と同じく、始める前に台帳を開く
    start(main)
    until(lambda: spawned(log) == ["la"])
    until(lambda: ((checkpoint(main).get("lanes") or {}).get("la") or {}).get("started"))  # 1 周の終わりのチェックポイント


def checkpoint(main):
    return plg.load_checkpoint(main, "p1")[0] or {}


def break_run(monkeypatch):
    """次の観測で耐久ワークフローを例外で落とし、ERROR になるのを待つ（#2509 の連鎖の 3 番を模す）。"""
    orig = prun._observe_run

    def boom(*_a, **_k):
        raise RuntimeError("観測の途中で環境が消えた")

    monkeypatch.setattr(prun, "_observe_run", boom)
    until(lambda: durable.status(WID) == "ERROR")
    monkeypatch.setattr(prun, "_observe_run", orig)


def rebuild(main, drop_record=False):
    """run のプロセスが落ちた後に start が起動する run と同じく、run_plan で続ける（作り直し）。"""
    durable.close()
    if drop_record:
        path = record_path(pl.KIND, pl.record_identity(main, "p1"), pl.record_start(main))
        for suffix in ("", "-wal", "-shm"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)
    threading.Thread(target=prun.run_plan, args=(main, "p1"), daemon=True).start()
    until(lambda: durable.launched() is not None and durable.status(WID) in pl.RUNNING)


def cli(main, *args):
    got = subprocess.run([sys.executable, str(SCRIPTS / "parallel.py"), *args], cwd=main, capture_output=True, text=True, timeout=60)
    return got.returncode, json.loads(got.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("drop_record", [False, True], ids=["error", "record-gone"])
def test_rebuild_restores_open_questions_and_requests(env, tmp_path, monkeypatch, drop_record):  # noqa: F811
    """条件 1・2・6・9: ERROR（か記録の消失）の後の作り直しで、答えていない問いが questions に、
    ERROR の間に受けた依頼がリリースの知らせに出る。seq は作り直しの前より大きい。"""
    main, log, states = env["main"], env["log"], env["states"]
    running(main, log, states)
    qid = ask(main)
    before = until(lambda: (q := questions(main)) and any(i["id"] == qid for i in q["items"]) and q)
    break_run(monkeypatch)
    code, res = cli(main, "wait", "p1", "--timeout", "1")
    assert code == 1 and res["metrics"]["next"].endswith("start p1")  # 失われた計画の wait は作り直しの 1 行を返す
    code, res = cli(main, "request", "p1", "--lane", "la", "--state", str(tmp_path / "st"), "--prs", "10", "--issues", "1")
    assert code == 10 and res["metrics"]["queued"] is True  # 失われた計画でも受け付け、台帳に残す（条件 9）
    rid = res["metrics"]["id"]
    rebuild(main, drop_record)
    assert any(r["kind"] == "rebuilt" for r in plg.read_ledger_rows(main, "p1").rows)
    after = until(lambda: (q := questions(main)) and any(i["id"] == qid for i in q["items"]) and q)
    assert after["seq"] > before["seq"] and after["seq"] > plg.REBUILD_SEQ  # 作り直しの前の seq の wait が取りこぼさない（条件 6）
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release" and e)
    assert ev["n"] == 1 and rid in [r["id"] for r in pl.requests_for(main, "p1")]
    if not drop_record:
        assert list(record_path(pl.KIND, pl.record_identity(main, "p1"), pl.record_start(main)).parent.glob("*.sqlite.retired-*"))


def test_answers_survive_the_rebuild_and_late_questions_receive_held_answers(env, tmp_path, monkeypatch):  # noqa: F811
    """条件 3・9・10・I7: 作り直しの前の答えを後で読め、その問いは再び出ない。ERROR の間に送った答えも当たり、
    一覧に無い依頼への答えは保留されて、作り直しの後に届いた問いへ当たる。"""
    main, log, states = env["main"], env["log"], env["states"]
    running(main, log, states)
    q1, q2 = ask(main, "L1"), ask(main, "L2")
    until(lambda: {q1, q2} <= {i["id"] for i in questions(main)["items"]})
    pl.send_answer(main, "p1", q1, "answered", choice=["A"])
    until(lambda: pl.read_answer(main, "p1", q1)[0])
    until(lambda: q1 in (checkpoint(main).get("answered") or []))
    break_run(monkeypatch)
    code, res = cli(main, "answer", "p1", q2, "--choice", "B")
    assert code == 0 and res["metrics"]["queued"] is True
    code, res = cli(main, "answer", "p1", "la-qL3", "--choice", "A")
    assert code == 0 and res["metrics"]["warning"]  # 一覧に無い依頼への答え
    rebuild(main)
    assert pl.read_answer(main, "p1", q1)[0]["choice"] == ["A"]  # 耐久の記録に無い答えをチェックポイントの控えから読む
    assert until(lambda: pl.read_answer(main, "p1", q2)[0])["choice"] == ["B"]
    until(lambda: (q := questions(main)) and q["seq"] > plg.REBUILD_SEQ)
    assert {q1, q2}.isdisjoint({i["id"] for i in questions(main)["items"]})
    ask(main, "L3")  # 作り直しの後に届いた問いへ、保留した答えが当たる
    assert until(lambda: pl.read_answer(main, "p1", "la-qL3")[0])["choice"] == ["A"]


def test_release_numbers_continue_and_lanes_are_not_spawned_again(env, tmp_path, monkeypatch):  # noqa: F811
    """条件 4・7・I9・I10: 作り直しの後のリリースは使った連番の次で、起動済みのレーンを起動し直さず、
    起動していないレーンは依存の条件どおりに起動する。"""
    main, log, states = env["main"], env["log"], env["states"]
    running(main, log, states)
    for n in (1, 2, 3):
        prl.release_dir(main, "p1", n, 1).mkdir(parents=True)
    break_run(monkeypatch)
    rebuild(main, drop_record=True)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release" and e)
    assert ev["n"] == 4 and rid in [r["id"] for r in pl.requests_for(main, "p1")]
    assert lanes_spawned(log) == ["la"]
    states.write_text(json.dumps({"la": "stopped"}))
    until(lambda: "lb" in spawned(log))
    assert lanes_spawned(log) == ["la", "lb"]


def test_settle_lands_on_the_release_in_flight_after_the_rebuild(env, tmp_path, monkeypatch):  # noqa: F811
    """条件 5: リリースが流れている途中で作り直しても、settle がそのリリースに当たり依頼へ答えが立つ。"""
    main, log, states = env["main"], env["log"], env["states"]
    running(main, log, states)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    (prl.release_dir(main, "p1", 1, 1) / "presented.json").write_text(json.dumps({"sha": "sha1", "prs": [10]}))
    pl.resume_plan(main, "p1", {"n": 1, "phase": "confirm", "take": None}, "confirm:1:-")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed")
    until(lambda: [r.get("state") for r in checkpoint(main).get("releases") or []] == ["confirmed"])
    until(lambda: "rel-1-1-confirmed" in (checkpoint(main).get("launches") or {}))
    before = len(helper_spawns(log))
    break_run(monkeypatch)
    rebuild(main)
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "running" and e)
    assert ev["release"]["action"] == "confirmed"  # 一時レーンが流すものを決める最後の起動を添える
    time.sleep(0.8)
    assert len(helper_spawns(log)) == before  # 起動の記録のある目的は、作り直しの後に打ち直さない（#2936 の I3）
    pl.resume_plan(main, "p1", {"n": 1, "phase": "settle", "result": "released", "version": "1.2.3", "reason": None}, "settle:1")
    assert read(main, rid)["version"] == "1.2.3"


def test_a_request_is_seen_even_when_the_notice_fails(env, tmp_path, monkeypatch):  # noqa: F811
    """条件 11・I1: 台帳へ書いた後の知らせが失敗しても、次の観測で依頼を受ける。"""
    main, log, states = env["main"], env["log"], env["states"]
    running(main, log, states)

    def refuse(*_a, **_k):
        raise durable.DurableError("知らせを送れない")

    monkeypatch.setattr(durable, "send_to", refuse)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    assert rid in [r["id"] for r in pl.requests_for(main, "p1")]


def test_a_closed_plan_is_not_rebuilt_and_refuses_requests(env, tmp_path, monkeypatch):  # noqa: F811
    """I11: 失われた計画を stop で閉じると、start は作り直さず、依頼を断る。"""
    main, log, states = env["main"], env["log"], env["states"]
    running(main, log, states)
    break_run(monkeypatch)
    assert cli(main, "stop", "p1")[0] == 0
    assert plg.plan_state(main, "p1", "ERROR") == "closed"
    code, res = cli(main, "request", "p1", "--lane", "la", "--state", str(tmp_path / "st"), "--prs", "10", "--issues", "1")
    assert code == 1
    assert prun.run_plan(main, "p1")[0]["status"] == "closed"
    assert spawned(log) == ["la"]
