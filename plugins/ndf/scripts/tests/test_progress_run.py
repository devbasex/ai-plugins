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
        if inp["stage"] == {fail!r}:
            return {{"tool": "ndf.plan", "status": "stopped", "summary": "落ちた", "items": [], "metrics": {{}}}}
        return {{"tool": "ndf.plan", "status": "ok", "summary": "済んだ", "items": [], "metrics": {{}}}}

    acts.act_plan = plan
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


def _next(tmp, url, tid, fail=""):
    script = tmp / f"child-{fail or 'ok'}.py"
    script.write_text(CHILD.format(lib=str(LIB), log=str(tmp / "calls.log"), fail=fail))
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
