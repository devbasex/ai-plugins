"""進行表のワークフロー（lib/progress_run.py。#3092 の受け入れ条件 2・4・6・7・14・決定 6・7・17）。

アクティビティの関数を差し替えた別のプロセスで `run_next` を流す。承認の待ちはプロセスを `os._exit(10)` で抜けるため、
同じプロセスでは流さない。差し替えたアクティビティは呼ばれた行を記録のファイルへ書き、終わった行を流し直さないことを数える。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
LIB = SCRIPTS / "lib"
sys.path.insert(0, str(LIB))
import progress_db as pdb  # noqa: E402
import progress_model as pm  # noqa: E402

CHILD = textwrap.dedent(
    """
    import json, sys
    sys.path.insert(0, {lib!r})
    import progress_activities as acts, progress_db as pdb, progress_run as pr

    log = {log!r}

    def plan(inp, args):
        with open(log, "a") as f:
            f.write(inp["stage"] + "\\n")
        if inp["stage"] == {hold!r} and not __import__("os").path.exists(log + ".released"):
            m = {{"answer_hold": {{"plan": "p", "lane": "l"}}, "park": "python3 parallel.py park p --lane l --table t"}}
            return {{"tool": "ndf.plan", "status": "gate", "summary": "答え待ちの停止のため流さなかった: p l", "items": [], "metrics": m}}
        if inp["stage"] == {fail!r}:
            return {{"tool": "ndf.plan", "status": "stopped", "summary": "落ちた", "items": [], "metrics": {{}}}}
        return {{"tool": "ndf.plan", "status": "ok", "summary": "済んだ", "items": [], "metrics": {{}}}}

    acts.act_plan = plan
    if {version!r}:  # 決定 21 の確かめ: NDF の版が上がった後の起動を、アプリの版を替えて作る
        _launch = pr.durable.launch
        pr.durable.launch = lambda *a, **kw: _launch(*a, **{{**kw, "app_version": {version!r}}})
    acts.act_record = lambda store, tid, row, phase: {{"tool": "ndf.record", "status": "ok", "summary": "", "items": [], "metrics": {{}}}}
    import progress_model as pm
    store = pdb.connect(sys.argv[1])
    try:
        rep, code = pr.run_next(store, sys.argv[2])
    except pm.Refused as e:
        rep, code = {{"status": "stopped", "summary": e.reason}}, 3
    print(json.dumps(rep, ensure_ascii=False))
    sys.exit(code)
    """
)


@pytest.fixture
def world(tmp_path):
    url = f"sqlite:///{tmp_path}/p.sqlite"
    os.environ.setdefault("NDF_DBOS_DIR", str(tmp_path / "dbos"))
    store = pdb.connect(url)
    return tmp_path, url, store


def _new(store, name, mode, lane=None, row=None):
    import workflow_def as wd

    snap = wd.load_mode_definition(mode)
    enabled = {s.name: wd.Enablement(True) for s in snap.stages}
    doc = {"snapshot": snap.to_dict(), "enabled": {k: vars(v) for k, v in enabled.items()}, "issues": [1], "pace": "normal"}
    drafts = [pm.EventDraft("created", "conductor", None, 1, payload={"lane_row": row} if lane else None)]
    drafts += [pm.EventDraft("created", "conductor", s.name, 1) for s in snap.stages]
    with pdb.transaction(store) as c:
        tid = pdb.create_table(c, "sprint", "r", name, lane, doc, drafts)
        if lane:
            pdb.lane_write(c, lane, pm.EventDraft("row_started", "conductor", str(row), payload={"row": row, "sprint": tid}))
    return tid


def _next(tmp, url, tid, fail="", version="", hold=""):
    script = tmp / f"child-{fail or 'ok'}-{version or 'src'}-{hold or 'run'}.py"
    script.write_text(CHILD.format(lib=str(LIB), log=str(tmp / "calls.log"), fail=fail, version=version, hold=hold))
    p = subprocess.run([sys.executable, str(script), url, tid], capture_output=True, text=True, timeout=300)
    return json.loads(p.stdout.strip().splitlines()[-1]), p.returncode


def _calls(tmp):
    f = tmp / "calls.log"
    return f.read_text().splitlines() if f.exists() else []


def test_gate_answer_then_complete_without_rerunning(world):
    tmp, url, store = world
    tid = _new(store, "s", "light")
    rep, code = _next(tmp, url, tid)
    assert code == 10 and rep["status"] == "gate" and rep["items"][0]["row"] == "デプロイ/release"
    before = _calls(tmp)
    assert before[-1] == "デプロイ" and "棚卸し" not in before
    with pdb.transaction(store) as c:
        pdb.append_events(
            c,
            tid,
            [
                pm.EventDraft(
                    "answered", "organizer", "デプロイ/release", 1, payload={"answer": "released", "release": {"version": "1.0.0"}}
                )
            ],
        )
    rep, code = _next(tmp, url, tid)
    assert code == 0 and rep["status"] == "ok", rep
    assert _calls(tmp) == before + ["棚卸し"]  # 終わった工程を流し直さない（受け入れ条件 4）
    with pdb.transaction(store) as c:
        rows = pm.fold(pdb.read_progress_events(c, tid), 1)
    assert pm.table_state(rows) == pm.COMPLETED
    assert all(rows[n].state == pm.FINISHED for n in ("要求と受け入れ条件", "デプロイ", "棚卸し"))
    assert rows["デプロイ"].at  # 終了の印の時刻（受け入れ条件 2）


def test_failure_stops_and_retry_continues(world):
    tmp, url, store = world
    tid = _new(store, "f", "light")
    rep, code = _next(tmp, url, tid, fail="完了判定")
    assert code == 1 and rep["items"] == [{"row": "完了判定", "kind": "failed"}]
    with pdb.transaction(store) as c:
        assert pm.fold(pdb.read_progress_events(c, tid), 1)["Pull Request"].state == pm.PENDING  # 失敗の後ろを流さない
        pdb.append_events(c, tid, [pm.EventDraft("retry", "conductor", "完了判定", 1, reason="直した")])
    n = len(_calls(tmp))
    rep, code = _next(tmp, url, tid)
    assert code == 10  # デプロイの待ちまで進む
    assert _calls(tmp)[n] == "完了判定" and _calls(tmp).count("実装") == 1


def test_completion_ends_lane_row_in_same_transaction(world):
    tmp, url, store = world
    with pdb.transaction(store) as c:
        plan = pdb.create_table(
            c,
            "plan",
            "r",
            "p",
            None,
            None,
            [
                pm.EventDraft("created", "organizer", payload={"max_lanes": 2}),
                pm.EventDraft("lane_added", "organizer", payload={"lane": "a", "touches": []}),
                pm.EventDraft("plan_started", "organizer"),
            ],
        )
        lane = pdb.create_table(
            c,
            "lane",
            "r",
            "p/a",
            plan,
            None,
            [pm.EventDraft("created", "organizer")]
            + [pm.EventDraft("created", "organizer", str(n), payload={"row": n, "issues": [n]}) for n in (1, 2)],
        )
    tid = _new(store, "l1", "light", lane, 1)
    with pdb.transaction(store) as c:
        pdb.append_events(c, tid, [pm.EventDraft("answered", "organizer", "デプロイ/release", 1, payload={"answer": "released"})])
    rep, code = _next(tmp, url, tid)
    assert code == 0, rep
    assert rep["metrics"]["next_row"] == {"lane": "a", "row": 2, "issues": [2], "args": []}  # 受け入れ条件 14
    with pdb.transaction(store) as c:
        assert [e.kind for e in pdb.read_progress_events(c, lane)].count("row_ended") == 1


def test_plan_cancelled_stops_before_next_stage(world):
    tmp, url, store = world
    with pdb.transaction(store) as c:
        plan = pdb.create_table(
            c,
            "plan",
            "r",
            "q",
            None,
            None,
            [
                pm.EventDraft("created", "organizer"),
                pm.EventDraft("lane_added", "organizer", payload={"lane": "a"}),
                pm.EventDraft("plan_started", "organizer"),
                pm.EventDraft("plan_cancelled", "organizer", reason="やめる"),
            ],
        )
        lane = pdb.create_table(
            c,
            "lane",
            "r",
            "q/a",
            plan,
            None,
            [pm.EventDraft("created", "organizer"), pm.EventDraft("created", "organizer", "1", payload={"row": 1})],
        )
    tid = _new(store, "c1", "light", lane, 1)
    rep, code = _next(tmp, url, tid)
    assert code == 3 and rep["metrics"]["plan_cancelled"] is True
    assert _calls(tmp) == []


def test_second_next_is_refused_while_running(world):
    tmp, url, store = world
    tid = _new(store, "x", "light")
    with pdb.run_lock(store, tid):
        rep, code = _next(tmp, url, tid)
    assert code == 3 and "I14" in rep["summary"]


def _executions(store, tid):
    with pdb.transaction(store) as c:
        return [e.payload["wid"] for e in pdb.read_progress_events(c, tid) if e.kind == "execution"]


def test_next_after_gate_exit_recovers_and_waits_again(world):
    """待ちでプロセスを抜けた後の next は、途中の実行を回復して同じ待ちへ戻り、終わった工程を流し直さない（決定 7）。"""
    tmp, url, store = world
    tid = _new(store, "w", "light")
    rep, code = _next(tmp, url, tid)
    assert code == 10
    before, wids = _calls(tmp), _executions(store, tid)
    rep, code = _next(tmp, url, tid)
    assert code == 10 and rep["items"][0]["row"] == "デプロイ/release"
    assert _calls(tmp) == before  # 回復は記録から再生し、アクティビティを流し直さない
    assert _executions(store, tid) == wids  # 新しい実行を始めていない


def test_stale_version_execution_is_cancelled_and_running_rows_fail(world):
    """版が違う途中の実行は取り消し、実行中の行を失敗にしてから新しい実行で止まる（決定 21）。"""
    import durable

    tmp, url, store = world
    tid = _new(store, "v", "light")
    rep, code = _next(tmp, url, tid)
    assert code == 10
    old = _executions(store, tid)[-1]
    with pdb.transaction(store) as c:
        running = sorted(k for k, r in pm.fold(pdb.read_progress_events(c, tid), 1).items() if r.state == pm.RUNNING)
    assert "デプロイ" in running
    rep, code = _next(tmp, url, tid, version="ndf-next-version")
    assert code == 1 and rep["status"] == "stopped", rep
    assert sorted(it["row"] for it in rep["items"]) == running
    assert durable.workflow_version_from(url, old)[0] == "CANCELLED"
    with pdb.transaction(store) as c:
        rows = pm.fold(pdb.read_progress_events(c, tid), 1)
    assert all(rows[k].state == pm.FAILED for k in running)
    assert _executions(store, tid)[-1] != old


def test_answer_hold_returns_gate_and_keeps_row_running(world):
    """答え待ちの停止（#2707）: 新しい承認を生むプランは流さず gate と park の 1 行で返し、行を失敗にしない。解けたら同じ行から続ける。"""
    tmp, url, store = world
    tid = _new(store, "h", "light")
    rep, code = _next(tmp, url, tid, hold="実装")
    assert code == 10 and rep["status"] == "gate", rep
    assert rep["items"][0]["kind"] == "answer_hold" and "park" in rep["metrics"]["park"]
    with pdb.transaction(store) as c:
        rows = pm.fold(pdb.read_progress_events(c, tid), 1)
    assert rows["実装"].state == pm.RUNNING
    (tmp / "calls.log.released").write_text("")
    rep, code = _next(tmp, url, tid, hold="実装")
    assert code == 10 and rep["items"][0]["row"] == "デプロイ/release", rep  # 停止が解けたら実装から続け、デプロイの待ちまで進む
    with pdb.transaction(store) as c:
        assert pm.fold(pdb.read_progress_events(c, tid), 1)["実装"].state == pm.FINISHED


def test_mvv_gate_applies_only_to_fast_and_auto_and_not_to_lane_release():
    """pace: fast / auto の承認ゲートとリリースの待ちだけに MVV 判定を当て、オーガナイザーへ依頼を送った待ちには当てない。"""
    import progress_activities as acts

    assert acts.mvv_gate_of({"pace": "normal"}, "gate", {}) is None
    assert acts.mvv_gate_of({"pace": "auto"}, "gate", {}) == "design"
    assert acts.mvv_gate_of({"pace": "fast"}, "release", {"request": {"metrics": {"requested": False}}}) == "release"
    assert acts.mvv_gate_of({"pace": "auto"}, "release", {"request": {"metrics": {"requested": True}}}) is None
    assert acts.mvv_gate_of({"pace": "auto"}, "work", {}) is None


@pytest.mark.parametrize("code, answered_by", [(0, "mvv"), (10, None)])
def test_mvv_answer_writes_answered_only_when_it_follows(world, monkeypatch, code, answered_by):
    """mvv-gate.py が従う（0）ときだけ待ちの行へ by: mvv の answered を書き、ほか（10）は人の答えを待つ。"""
    import progress_activities as acts

    tmp, url, store = world
    tid = _new(store, f"m{code}", "light")
    fake = tmp / "mvv-gate.py"
    fake.write_text(f"import sys\nsys.exit({code})\n")
    monkeypatch.setattr(acts, "MVV_GATE", fake)
    state = tmp / "state.json"
    state.write_text("{}")
    inp = {"pace": "auto", "state": str(state), "work": str(tmp / "w"), "cwd": str(tmp), "mode": "light", "previous": {}}
    got = acts.act_mvv(store, tid, 1, "デプロイ/release", inp, "release")
    with pdb.transaction(store) as c:
        events = [e for e in pdb.read_progress_events(c, tid) if e.kind == "answered"]
    if answered_by is None:
        assert got is None and events == []
    else:
        assert got["answer"] == "approved" and got["by"] == answered_by
        assert events and events[-1].payload["by"] == "mvv" and events[-1].payload["record"].endswith("mvv-note.md")
