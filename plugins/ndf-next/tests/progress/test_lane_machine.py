"""マシン全体のプランの本数の上限（#3097・#2999）。数え方と判定は値で、別のリポジトリの計画は一時のリポジトリの
進行表（SQLite）で、同時の判定は起動の排他を 2 本のスレッドで取り合って確かめる。"""

from __future__ import annotations

import subprocess
import threading
from datetime import UTC, datetime

import pytest

from ndf import identity
from ndf.core import durable
from ndf.progress import command, lane, plan, store
from ndf.progress.lane import launch, liveness, machine
from ndf.progress.plan import run_decide
from ndf.progress.plan.run_decide import Done, Launch, Observe, Policy, RunState, Sleep
from plan_decls import REPO, decl, doc


def _row(running=True, plan_name="q1", cwd="/other"):
    return {"running": running, "role": launch.ROLE, "plan": plan_name, "cwd": cwd}


OTHER = machine.PlanLanes("plan:other", (("o-a", 3, False), ("o-b", 2, True), ("o-c", 4, False)))
OWN = machine.PlanLanes("plan:own", (("w-a", 2, False), ("w-b", 1, False)))


def test_count_adds_the_lanes_that_run_a_row_or_a_wrapper_but_not_the_lane_to_launch():
    """動いているレーンは、ラッパーが動いているか実行中の行を持つ（答えを待つ行を含む）レーン。起こすレーン自身は数えない。"""
    listed = {"o-a": _row(), "o-c": _row(running=False), "w-a": _row(plan_name="p6", cwd="/own")}
    got = machine.count([OTHER, OWN], listed, "w-a")
    assert got.total == 5 and got.lanes == ("o-a", "o-b") and got.unreadable == ()


def test_a_lane_waits_while_the_machine_total_would_pass_the_cap_and_starts_once_it_fits():
    """別のリポジトリで 5 本動いていれば plans 2 のレーンは待ち、4 本以下になれば起こす（受け入れ条件 4・5）。"""
    assert not machine.admit(5, 2, 6)
    assert machine.admit(4, 2, 6)
    wait = machine.waiting(machine.RULE, machine.Count(5, ("o-a",), ()), 2, 6)
    assert wait["ok"] is False and wait["waiting"]["machine_plans"] == 5 and wait["waiting"]["max_plans"] == 6


def test_nothing_running_on_the_machine_never_holds_a_launch():
    """マシンで動いているレーンが無ければ、レーンの plans が上限を超えていても待たせない（受け入れ条件 7）。"""
    assert machine.admit(0, 9, 6)


def test_an_unreadable_plan_counts_each_running_wrapper_once_and_is_named():
    broken = machine.PlanLanes("/other:q1", readable=False)
    listed = {"x-1": _row(), "x-2": _row(), "x-3": _row(running=False), "y": {"running": True, "role": "organizer"}}
    got = machine.count([broken], listed, "")
    assert got.total == 2 and got.unreadable == ("/other:q1",)


def test_discover_lists_each_plan_of_the_conductor_wrappers_once():
    listed = {"a": _row(), "b": _row(), "c": _row(plan_name="q2"), "d": {"running": True, "role": "organizer", "plan": "q3", "cwd": "/x"}}
    assert [(str(c), p) for c, p in machine.discover(listed)] == [("/other", "q1"), ("/other", "q2")]


# --- 別のリポジトリの計画 ---------------------------------------------------------------------------


@pytest.fixture
def other_repo(tmp_path):
    """別のリポジトリ（origin を持つ）に計画 q1 を作り、レーン a（plans 4）の行を実行中にする。"""
    path = tmp_path / "other"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", "https://example.invalid/o/other"], check=True)
    root = command.store_root_of(path)
    root.mkdir(parents=True, exist_ok=True)
    durable.prepare_store(root)
    with store.open_store(store.default_url(root), root) as st, store.transaction(st) as conn:
        repo = command.repo_id(path, st.root)
        pid = plan.create_plan(conn, repo, {**doc(decl("a", [[1]], plans=4), decl("b", [[2]], touches=("src/b",))), "name": "q1"}, True)
        view, _ = lane.read_plan(conn, pid)
        sid = store.create_table(conn, ("sprint", repo, "a-r1"), view.lanes["a"].table_id, None, [])
        lane.start_row(conn, pid, "a", "r1", sid)
    yield path, repo
    durable.release_store(root)


def test_machine_count_reads_the_plan_of_another_repository_from_its_checkout(other_repo):
    """登録簿に載る別のリポジトリの計画の宣言を、そのチェックアウトの進行表から読んで数える（受け入れ条件 4）。"""
    path, repo = other_repo
    listed = {launch.wrapper_name(repo, "q1", "a"): _row(running=False, cwd=str(path))}
    got = machine.machine_count(OWN, listed, "w-a")
    assert got.total == 4 and got.unreadable == ()
    # 進行表の DB の無いチェックアウトの計画は読めないとして、動いているラッパーを 1 本と数える
    gone = {"z": _row(cwd=str(path.parent / "nowhere"))}
    got = machine.machine_count(OWN, gone, "w-a")
    assert got.total == 1 and len(got.unreadable) == 1


# --- 起動の排他と同時の判定 ---------------------------------------------------------------------------


@pytest.fixture
def state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_STATE_HOME", str(tmp_path / "state"))
    return identity.load().state_dir


def test_two_admissions_at_once_do_not_pass_the_cap(state_home, monkeypatch):
    """2 本の判定が同時に走っても、起動の排他の中で数えるので、起こした後の合計が上限を超えない（受け入れ条件 6）。"""
    listed = {"o-a": _row()}
    plans = {"o-a": 3}
    monkeypatch.setattr(
        machine,
        "machine_count",
        lambda own, table, skip: machine.count([machine.PlanLanes("all", tuple((n, plans[n], False) for n in table))], table, skip),
    )
    gate = threading.Barrier(2)
    results = {}

    def admit(name):
        def go():
            listed[name] = _row(plan_name="p6", cwd="/own")
            plans[name] = 2
            return {"ok": True, "detail": "起こした"}

        gate.wait()
        results[name] = machine.admitted(machine.PlanLanes("plan:own"), name, 2, lambda: dict(listed), go)

    threads = [threading.Thread(target=admit, args=(n,)) for n in ("w-a", "w-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 上限 6: 動いている 3 本に 1 本目の 2 本を足すと 5、2 本目を足すと 7 で超える
    assert [r["ok"] for r in results.values()].count(True) == 1 and sum(plans[n] for n in listed) <= machine.cap()
    (held,) = [r for r in results.values() if not r["ok"]]
    assert held["waiting"]["reason"] == machine.RULE and held["waiting"]["machine_plans"] == 5


def test_a_held_lock_makes_the_launch_wait(state_home):
    with machine.admit_lock(1.0):
        got = machine.admitted(machine.PlanLanes("plan:own"), "w-a", 1, lambda: {}, lambda: {"ok": True, "detail": ""})
    assert got["ok"] is False and got["waiting"]["reason"] == machine.LOCK


def test_an_unreadable_list_decides_by_the_own_plan_alone(state_home):
    own = machine.PlanLanes("plan:own", (("w-b", 5, True),))
    go = lambda: {"ok": True, "detail": "起こした"}  # noqa: E731
    assert machine.admitted(own, "w-a", 1, lambda: None, go)["ok"]
    held = machine.admitted(own, "w-a", 2, lambda: None, go)
    assert held["waiting"]["reason"] == machine.OBSERVE


# --- 耐久ワークフローの判断と status ------------------------------------------------------------------


def test_a_launch_held_by_the_machine_cap_is_not_a_failure_and_is_tried_again():
    pol = Policy(poll=30.0, attempts=2)
    snap = run_decide.with_stalls(run_decide.snapshot("running", False, [("a", "r1")], []), [])

    def step(state, done):
        nxt = run_decide.decide(pol, state, done)
        return nxt.act, nxt.state

    act, state = step(RunState(), Done(Observe(), snap))
    held = machine.waiting(machine.RULE, machine.Count(5, (), ()), 2, 6)
    act, state = step(state, Done(act, held))
    assert act == Sleep(30.0) and state.count("a/r1") == 0 and state.failed == () and state.launched == ()
    act, state = step(state, Done(Observe(), snap))
    assert act == Launch("a", "r1")


def test_status_shows_a_lane_waiting_for_the_machine(opened, monkeypatch):
    """起動を待っているレーンの行に、マシンの合計・このレーンの本数・上限が載る（受け入れ条件 4）。"""
    with store.transaction(opened) as conn:
        pid = plan.create_plan(conn, REPO, doc(decl("a", [[1]], plans=2)), True)
    listed = {f"x-{i}": _row() for i in range(5)}
    found = liveness.Probes(wrapper_name=lambda name: f"w-{name}", wrappers=lambda: listed, sprint=lambda sid: None)
    monkeypatch.setattr(machine, "read_lanes", lambda cwd, plan_name, skip=(): machine.PlanLanes(f"{cwd}:{plan_name}", readable=False))
    with store.transaction(opened) as conn:
        out = plan.status(conn, pid, datetime.now(UTC), found)
    (item,) = out["items"]
    assert item["machine_wait"] == {"reason": "machine-plans", "machine_plans": 5, "plans": 2, "max_plans": 6, "unreadable": ["/other:q1"]}
