"""計画を進める耐久ワークフロー（#3097 の受け入れ条件 1・2・4）。

判断（`run_decide`）は DB を使わずに確かめる。写し（`read_snapshot`）は `opened`（SQLite と PostgreSQL）で、
耐久ワークフロー（`advance`）は DBOS を起こして、レーンを起こす所（`launch.spawn`）だけを差し替えて流す。
差し替えた起動は、起きた conductor の代わりにスプリントを作って行を始め、すぐ終える。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.core import loop
from ndf.progress import lane, plan, store
from ndf.progress.lane import launch
from ndf.progress.plan import run, run_decide
from ndf.progress.plan.run_decide import Complete, Done, Launch, Observe, Policy, RunState, Sleep

REPO = "https://example.invalid/o/r"
POLICY = Policy(poll=30.0, attempts=2)
# 耐久ワークフローを流すテストの読み直しの間隔（起こした後の待ちを短くする）
FAST = Policy(poll=0.05, attempts=2)


def _decl(name, rows, touches=("src/a",), after=()):
    return {
        "name": name,
        "plans": 1,
        "touches": list(touches),
        "after": list(after),
        "rows": [{"kind": "sprint", "issues": i} for i in rows],
    }


def _doc(*lanes):
    return {"version": 1, "name": "p6", "rules": [], "lanes": list(lanes)}


def _snap(state="running", complete=False, wakeable=(), running=()):
    return run_decide.snapshot(state, complete, list(wakeable), list(running))


def _step(state, done):
    nxt = run_decide.decide(POLICY, state, done)
    return nxt.act, nxt.state, nxt.finished


# --- 判断（純粋） ----------------------------------------------------------------------------------


def test_first_act_is_to_observe_and_a_sleep_leads_to_observe_again():
    act, state, _ = _step(RunState(), None)
    assert act == Observe()
    act, _, _ = _step(state, Done(Sleep(30.0)))
    assert act == Observe()


def test_each_wakeable_lane_is_launched_once_then_it_sleeps():
    state = RunState()
    act, state, _ = _step(state, Done(Observe(), _snap(wakeable=[("a", "r1"), ("b", "r1")])))
    assert act == Launch("a", "r1")
    act, state, _ = _step(state, Done(act, {"ok": True, "detail": "起こした"}))
    assert act == Launch("b", "r1")
    act, state, _ = _step(state, Done(act, {"ok": True, "detail": "起こした"}))
    assert act == Sleep(30.0)
    # 起こしたレーンがまだ行を始めていなくても、次の読み直しで起こし直さない
    act, state, _ = _step(state, Done(Observe(), _snap(wakeable=[("a", "r1"), ("b", "r1")])))
    assert act == Sleep(30.0) and state.launched == ("a/r1", "b/r1")


def test_the_next_row_of_the_same_lane_is_launched_after_the_row_ends():
    """スプリントの終わりで行が完了になり、同じレーンの次の行が起こせる行になれば起こす（受け入れ条件 2）。"""
    state = RunState(launched=("a/r1",))
    act, state, _ = _step(state, Done(Observe(), _snap(wakeable=[("a", "r2")])))
    assert act == Launch("a", "r2") and state.launched == ()


def test_a_failed_launch_is_retried_on_the_next_poll_and_given_up_after_the_attempts():
    state = RunState()
    act, state, _ = _step(state, Done(Observe(), _snap(wakeable=[("a", "r1")])))
    act, state, _ = _step(state, Done(act, {"ok": False, "detail": "tmux が無い"}))
    assert act == Sleep(30.0) and state.count("a/r1") == 1
    act, state, _ = _step(state, Done(Observe(), _snap(wakeable=[("a", "r1")])))
    assert act == Launch("a", "r1")
    act, state, _ = _step(state, Done(act, {"ok": False, "detail": "tmux が無い"}))
    assert act == Sleep(30.0) and state.failed == ("a/r1",) and len(state.errors) == 2
    act, state, _ = _step(state, Done(Observe(), _snap(wakeable=[("a", "r1")])))
    assert act == Sleep(30.0)


def test_complete_plan_is_written_when_all_rows_are_done_and_the_run_finishes():
    act, state, _ = _step(RunState(), Done(Observe(), _snap(complete=True)))
    assert act == Complete()
    _, state, finished = _step(state, Done(act, True))
    assert finished and state.outcome == "completed"
    act, _, finished = _step(state, Done(Complete(), False))
    assert act == Observe() and not finished


def test_a_stopped_plan_cancels_running_sprints_then_ends_as_stopped():
    act, state, _ = _step(RunState(), Done(Observe(), _snap(state="cancelled", running=[("a", "r1", "s1")])))
    assert act == Launch("a", "r1", cancel="s1")
    act, state, _ = _step(state, Done(act, {"ok": True, "detail": "起こした"}))
    assert act == Sleep(30.0)
    _, state, finished = _step(state, Done(Observe(), _snap(state="cancelled")))
    assert finished and state.outcome == "stopped"


def test_a_plan_not_yet_started_is_only_watched():
    act, _, _ = _step(RunState(), Done(Observe(), _snap(state="pending", wakeable=[("a", "r1")])))
    assert act == Sleep(30.0)


def test_policy_reads_the_poll_from_the_environment(monkeypatch):
    monkeypatch.setenv(run.POLL_ENV, "5")
    assert run.policy().poll == 5.0
    monkeypatch.delenv(run.POLL_ENV)
    assert run.policy().poll > 5.0


# --- 進行表からの写し ------------------------------------------------------------------------------


def _create(st, doc):
    with store.transaction(st) as conn:
        return plan.create_plan(conn, REPO, doc, True)


def _snapshot(st, pid):
    with store.transaction(st) as conn:
        return run.read_snapshot(conn, pid)


def _start(conn, pid, lane_name, key):
    view, _ = lane.read_plan(conn, pid)
    sid = store.create_table(conn, ("sprint", REPO, f"{lane_name}-{key}"), view.lanes[lane_name].table_id, None, [])
    lane.start_row(conn, pid, lane_name, key, sid)
    return sid


def test_snapshot_reads_both_tables_and_follows_after(opened):
    """計画の状態は計画とレーンの進行表から読める（受け入れ条件 4）。b は a の行 r1 の後に起こせる。"""
    pid = _create(opened, _doc(_decl("a", [[1], [2]]), _decl("b", [[3]], touches=("src/b",), after=("a/r1",))))
    assert _snapshot(opened, pid) == _snap(wakeable=[("a", "r1")])
    with store.transaction(opened) as conn:
        sid = _start(conn, pid, "a", "r1")
    assert _snapshot(opened, pid) == _snap(running=[("a", "r1", sid)])
    with store.transaction(opened) as conn:
        lane.end_row(conn, sid, "completed", "conductor")
    assert _snapshot(opened, pid) == _snap(wakeable=[("a", "r2"), ("b", "r1")])


def test_first_input_starts_the_row_with_the_lane_table_as_parent(opened):
    pid = _create(opened, _doc(_decl("a", [[1, 2]])))
    with store.transaction(opened) as conn:
        text = run.first_input(conn, pid, Launch("a", "r1"))
        view, _ = lane.read_plan(conn, pid)
    assert "progress" in text and "--parent" in text and view.lanes["a"].table_id in text and '"start"' in text


# --- 耐久ワークフロー -------------------------------------------------------------------------------


@pytest.fixture
def where(progress_url, root, tmp_path):
    return run.Where(root=root, url=progress_url, repo=REPO, cwd=tmp_path)


def _conductor(where, outcome):
    """起きた conductor の代わり: 起こされた行を始め、`outcome` で終える。"""
    launched = []

    def spawn(plan_name: str, lane_name: str, cwd: Path, first: str) -> dict:
        with store.open_store(where.url, where.root) as st, store.transaction(st) as conn:
            pid = plan.plan_id(REPO, plan_name)
            view, _ = lane.read_plan(conn, pid)
            nxt = lane.next_row(view, lane.folded(lane.read_lanes(conn, view)), lane_name)
            sid = _start(conn, pid, lane_name, nxt["row"])
            lane.end_row(conn, sid, outcome, "conductor")
        launched.append((lane_name, nxt["row"]))
        return {"ok": True, "detail": "起こした"}

    return spawn, launched


def test_the_workflow_runs_every_row_of_the_lane_in_order_and_completes_the_plan(where, monkeypatch):
    with store.open_store(where.url, where.root) as st:
        pid = _create(st, _doc(_decl("a", [[1], [2]]), _decl("b", [[3]], touches=("src/b",), after=("a",))))
    spawn, launched = _conductor(where, "completed")
    monkeypatch.setattr(launch, "spawn", spawn)
    final = run.advance(where, "p6", FAST)
    assert final["outcome"] == "completed" and final["failed"] == []
    assert launched == [("a", "r1"), ("a", "r2"), ("b", "r1")]
    with store.open_store(where.url, where.root) as st, store.transaction(st) as conn:
        view, _ = lane.read_plan(conn, pid)
    assert view.state == "completed"
    # 終わった計画で打ち直しても流し直さない
    assert run.advance(where, "p6", FAST)["outcome"] == "completed" and len(launched) == 3


def test_the_workflow_cancels_running_sprints_of_a_stopped_plan(where, monkeypatch):
    with store.open_store(where.url, where.root) as st:
        pid = _create(st, _doc(_decl("a", [[1], [2]])))
        with store.transaction(st) as conn:
            sid = _start(conn, pid, "a", "r1")
            running = plan.stop_plan(conn, pid, "放棄")
    assert running == [sid]
    cancelled = []

    def spawn(plan_name, lane_name, cwd, first):
        assert "cancel" in first and sid in first
        with store.open_store(where.url, where.root) as st, store.transaction(st) as conn:
            lane.end_row(conn, sid, "cancelled", "conductor")
        cancelled.append(lane_name)
        return {"ok": True, "detail": "起こした"}

    monkeypatch.setattr(launch, "spawn", spawn)
    final = run.advance(where, "p6", FAST)
    assert final["outcome"] == "stopped" and cancelled == ["a"]


def test_loop_without_dbos_gives_the_same_decisions():
    """判断のループは DBOS を起こさずに同じ判断を回せる（落ちた後の回復と同じ判断を返す）。"""
    snaps = iter([_snap(wakeable=[("a", "r1")]), _snap(complete=True)])

    def perform(act):
        if isinstance(act, Observe):
            return Done(act, next(snaps))
        if isinstance(act, Launch):
            return Done(act, {"ok": True, "detail": "起こした"})
        return Done(act, True)

    final = loop.run(lambda s, d: run_decide.decide(POLICY, s, d), perform, RunState())
    assert final.as_result() == {"outcome": "completed", "launched": ["a/r1"], "failed": [], "errors": []}
