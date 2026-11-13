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
