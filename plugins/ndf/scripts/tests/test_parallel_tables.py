"""計画の進行表とレーンの進行表（#3092 の受け入れ条件 13・17・I16。#3097 から取り込んだ）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import progress_db as pdb  # noqa: E402
import progress_lanes as plr  # noqa: E402
import progress_model as pm  # noqa: E402

D = pm.EventDraft


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    url = f"sqlite:///{tmp_path}/p.sqlite"
    store = pdb.connect(url)
    with pdb.transaction(store) as c:
        plan = pdb.create_table(
            c,
            "plan",
            pdb.progress_repo_key(repo),
            "p",
            None,
            None,
            [
                D("created", "organizer", payload={"max_lanes": 2}),
                D("lane_added", "organizer", payload={"lane": "a", "touches": []}),
                D("plan_started", "organizer"),
            ],
        )
        rid = pdb.open_request(c, plan, {"move": [3]}, "別のレーンへ", "workflow")
    return repo, url, store, plan, rid


def test_request_close_lets_plan_complete(world):
    repo, url, store, plan, rid = world
    with pdb.transaction(store) as c:
        view = pdb.plan_view_of(c, plan)
    assert not plr.plan_complete({"a": []}, list(view["requests"].values()))  # I16
    env = {**os.environ, "NDF_PROGRESS_DB": url}
    cmd = [sys.executable, str(SCRIPTS / "parallel.py"), "request-close", "p", rid, "--reason", "採らない"]
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=repo)
    assert p.returncode == 0, p.stdout + p.stderr
    with pdb.transaction(store) as c:
        view = pdb.plan_view_of(c, plan)
    assert plr.plan_complete({"a": []}, list(view["requests"].values()))
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=repo)
    assert p.returncode == 3 and json.loads(p.stdout.strip().splitlines()[-1])["status"] == "stopped"


def test_writers_per_level(world):
    _, _, store, plan, _ = world
    with pytest.raises(pm.Refused, match="I6"), pdb.transaction(store) as c:
        pdb.append_events(c, plan, [D("plan_completed", "organizer")])  # 完了は耐久ワークフローだけ
    with pytest.raises(pm.Refused, match="I6"), pdb.transaction(store) as c:
        pdb.append_events(c, plan, [D("lane_changed", "conductor", payload={"lane": "a"})])
    with pdb.transaction(store) as c:
        pdb.append_events(c, plan, [D("plan_completed", "durable")])
        assert pdb.plan_view_of(c, plan)["state"] == pm.COMPLETED


# ---- parallel.py の副命令が使う口（parallel_tables。受け入れ条件 13・14・16・17） ----

import types  # noqa: E402

import parallel_tables as ptab  # noqa: E402


def _lane(name, issues, touches, after=()):
    return types.SimpleNamespace(
        name=name, touches=list(touches), after=list(after), input=types.SimpleNamespace(issues=issues, args=["--x"])
    )


@pytest.fixture
def started(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.setenv("NDF_PROGRESS_DB", f"sqlite:///{tmp_path}/t.sqlite")
    plan = types.SimpleNamespace(name="q", rules=[], lanes=[_lane("a", [1], ["x/**"]), _lane("b", [2], ["y/**"], ["a"])])
    tid = ptab.create_plan_tables(repo, plan, max_lanes=3)
    return repo, plan, tid


def test_start_creates_plan_and_lane_tables_once(started):
    repo, plan, tid = started
    assert ptab.create_plan_tables(repo, plan) == tid  # 作り直さない（I1）
    view = ptab.lane_view(repo, "q")
    assert set(view) == {"a", "b"}
    assert view["a"]["rows"] == [{"row": 1, "issues": [1], "state": pm.PENDING}]
    assert ptab.wake_list(repo, "q") == [("a", 1)]  # b は a の後
    assert ptab.table_plan_state(repo, "q", "PENDING", ("PENDING",)) == "running"
    assert ptab.table_plan_state(repo, "q", None, ("PENDING",)) == "lost"


def test_lane_add_checks_touches_and_writes_first_row(started):
    repo, _, _ = started
    with pytest.raises(pm.Refused, match="I13"):
        ptab.record_lane(repo, "q", _lane("c", [3], ["x/**"]), "add", "足す")  # a と同時に動き得て触る場所が重なる
    assert "c" not in ptab.lane_view(repo, "q")
    ptab.record_lane(repo, "q", _lane("c", [3], ["z/**"]), "add", "足す")
    assert ptab.lane_view(repo, "q")["c"]["rows"][0]["issues"] == [3]
    with pytest.raises(pm.Refused):
        ptab.record_lane(repo, "q", _lane("c", [3], ["z/**"]), "add", "二度")


def test_finish_only_when_all_rows_end_and_cancel(started):
    repo, _, tid = started
    assert not ptab.finish_plan(repo, "q")  # 未着手の行が残る
    running = ptab.cancel_plan(repo, "q", "止める")
    assert running == []
    assert all(r["state"] == pm.CANCELLED for v in ptab.lane_view(repo, "q").values() for r in v["rows"])
    assert ptab.table_plan_state(repo, "q", None, ("PENDING",)) == "closed"
    assert not ptab.finish_plan(repo, "q")  # 中止した計画を完了にしない


def test_no_table_reads_as_none(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_PROGRESS_DB", f"sqlite:///{tmp_path}/none.sqlite")
    assert ptab.read_plan(tmp_path, "p") is None
    assert ptab.lane_view(tmp_path, "p") == {}
    assert not (tmp_path / "none.sqlite").exists()  # 読むだけでは DB を作らない


def test_row_input_points_the_lane_at_its_row(started):
    """前のスプリントを終えたレーンを起こし直す入力は、レーンの進行表の ID と行で progress.py new を指す（決定 7）。"""
    repo, _, _ = started
    got = ptab.row_input(repo, "q", "b", 1)
    assert "#2" in got and f"--lane {ptab.lane_table_id(repo, 'q', 'b')} --row 1" in got and "progress.py" in got


def test_plan_state_reads_the_plan_table_first(started, monkeypatch):
    """計画の状態の語は計画の進行表から読み、台帳を読まない（受け入れ条件 16）。"""
    import parallel_ledger as plg

    repo, _, _ = started
    monkeypatch.setattr(plg, "ledger_plan_state", lambda *a: pytest.fail("台帳を読んだ"))
    assert plg.plan_state(repo, "q", "PENDING") == "running"
    ptab.cancel_plan(repo, "q", "止める")
    assert plg.plan_state(repo, "q", None) == "closed"
