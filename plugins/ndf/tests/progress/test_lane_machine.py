"""マシン全体の稼働中のレーンの数の上限（#3097・#2999・#3989）。数え方と判定は値で、別のリポジトリの計画は一時のリポジトリの
ステートマシン（SQLite）で確かめる。どれも回数と状態で判定し、時間で区切らない。"""

from __future__ import annotations

import contextlib
import subprocess
from collections import Counter
from datetime import UTC, datetime

import pytest

from ndf import identity
from ndf.contracts import named_cap
from ndf.core import durable
from ndf.progress import command, lane, plan, store
from ndf.progress.lane import launch, liveness, machine
from ndf.progress.plan import run_decide
from ndf.progress.plan.run_decide import Done, Launch, Observe, Policy, RunState, Sleep
from plan_decls import REPO, decl, doc


def _row(running=True, plan_name="q1", cwd="/other", role=launch.ROLE):
    return {"running": running, "role": role, "plan": plan_name, "cwd": cwd}


OTHER = machine.PlanLanes("plan:other", (("o-a", False), ("o-b", True), ("o-c", False)))
OWN = machine.PlanLanes("plan:own", (("w-a", False), ("w-b", False)))


def test_count_adds_each_live_lane_and_each_running_wrapper_once_but_not_the_lane_to_launch():
    """稼働中のレーン（実行中の行を持つ）と動いているラッパーを 1 本ずつ数える。起動するレーン自身は数えない（受け入れ条件 5）。"""
    listed = {"o-a": _row(), "o-c": _row(running=False), "w-a": _row(plan_name="p6", cwd="/own")}
    got = machine.count([OTHER, OWN], listed, "w-a")
    assert got.total == 2 and got.lanes == ("o-a", "o-b") and got.unreadable == ()


def test_count_takes_wrappers_of_any_role_and_wrappers_outside_the_registry():
    """オーガナイザーのような計画に無いラッパーも、登録簿に無く錠だけを持つラッパーも 1 本と数える（受け入れ条件 7）。"""
    listed = {"org": {"name": "org", "running": True, "role": "organizer"}, "bare": {"name": "bare", "running": True, "registered": False}}
    assert machine.count([OWN], listed, "w-a") == machine.Count(2, ("bare", "org"), ())


def test_a_live_lane_whose_wrapper_stopped_keeps_its_seat():
    own = machine.PlanLanes("plan:own", (("w-a", True),))
    assert machine.count([own], {}, "w-b").total == 1
    assert machine.count([own], {}, "w-a").total == 0


def test_admit_counts_the_lane_to_launch_as_one():
    """数えた本数 + 1 が上限以内なら起動し、ちょうど上限なら待つ（受け入れ条件 5）。"""
    assert machine.admit(5, 6) and not machine.admit(6, 6)
    assert machine.admit(0, 1) and not machine.admit(1, 1)
    wait = machine.waiting(machine.RULE, machine.Count(6, ("o-a",), ()), 6)
    assert wait["ok"] is False and wait["waiting"] == {"reason": "machine-lanes", "machine_lanes": 6, "cap": 6, "unreadable": []}
    assert machine.waiting(machine.LOCK, None, None)["waiting"] == {"reason": "lock", "machine_lanes": None, "cap": None, "unreadable": []}


def test_an_unreadable_plan_is_named_and_its_running_wrappers_counted_once():
    broken = machine.PlanLanes("/other:q1", (("z", True),), readable=False)
    listed = {"x-1": _row(), "x-2": _row(), "x-3": _row(running=False), "z": _row()}
    got = machine.count([broken], listed, "")
    assert got.total == 3 and got.unreadable == ("/other:q1",)


def test_discover_lists_each_plan_of_the_conductor_wrappers_once():
    listed = {"a": _row(), "b": _row(), "c": _row(plan_name="q2"), "d": _row(plan_name="q3", cwd="/x", role="organizer")}
    assert [(str(c), p) for c, p in machine.discover(listed)] == [("/other", "q1"), ("/other", "q2")]


# --- 別のリポジトリの計画と索引 ------------------------------------------------------------------------


@pytest.fixture
def other_repo(tmp_path):
    """別のリポジトリ（origin を持つ）に計画 q1 を作り、レーン a の行を実行中にする。"""
    path = tmp_path / "other"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", "https://example.invalid/o/other"], check=True)
    root = command.store_root_of(path)
    root.mkdir(parents=True, exist_ok=True)
    durable.prepare_store(root)
    with store.open_store(store.default_url(root), root) as st, store.transaction(st) as conn:
        repo = command.repo_id(path, st.root)
        pid = plan.create_plan(conn, repo, {**doc(decl("a", [[1]]), decl("b", [[2]], touches=("src/b",))), "name": "q1"}, True)
        view, _ = lane.read_plan(conn, pid)
        sid = store.create_machine(conn, ("sprint", repo, "a-r1"), view.lanes["a"].machine_id, None, [])
        lane.start_row(conn, pid, "a", "r1", sid)
    yield path, repo, pid
    durable.release_store(root)


def test_machine_count_reads_the_plan_of_another_repository_from_its_checkout(other_repo):
    """登録簿に載る別のリポジトリの計画を、そのチェックアウトのステートマシンから読んで数える。"""
    path, repo, _ = other_repo
    listed = {launch.wrapper_name(repo, "q1", "a"): _row(running=False, cwd=str(path))}
    got = machine.machine_count(OWN, listed, "w-a")
    assert got.total == 1 and got.unreadable == ()
    # ステートマシンの DB の無いチェックアウトの計画は読めないとして、動いているラッパーを 1 本と数える
    gone = {"z": _row(cwd=str(path.parent / "nowhere"))}
    got = machine.machine_count(OWN, gone, "w-a")
    assert got.total == 1 and len(got.unreadable) == 1


def test_a_plan_whose_wrappers_all_stopped_is_found_from_the_index(other_repo):
    """回答待ちの行を持つ計画 A のラッパーが止まって登録簿から消えても、索引から A を読んで数える（受け入れ条件 8）。"""
    path, _, pid = other_repo
    assert machine.machine_count(OWN, {}, "w-a").total == 0
    machine.index_plan(machine.PlanPlace(pid, str(path), "q1"))
    machine.index_plan(machine.PlanPlace(pid, str(path), "q1"))
    assert machine.indexed() == [machine.PlanPlace(pid, str(path), "q1")]
    assert machine.machine_count(OWN, {}, "w-a").total == 1
    machine.unindex_plan(pid)
    machine.unindex_plan(pid)
    assert machine.indexed() == [] and machine.machine_count(OWN, {}, "w-a").total == 0


def test_a_plan_listed_from_two_checkouts_is_read_once(other_repo, monkeypatch):
    """別の作業ディレクトリから再開した計画が、旧い登録簿の行と索引の両方に載っても、ステートマシンは 1 回だけ読む。"""
    path, repo, pid = other_repo
    moved = path.parent / "moved"
    moved.symlink_to(path)
    reads = []
    real = machine.read_view
    monkeypatch.setattr(machine, "read_view", lambda conn, plan_id: reads.append(plan_id) or real(conn, plan_id))
    listed = {launch.wrapper_name(repo, "q1", "a"): _row(running=False, cwd=str(path))}
    plans = machine.machine_plans(OWN, listed, [machine.PlanPlace(pid, str(moved), "q1")])
    assert [p.key for p in plans] == [OWN.key, pid] and reads == [pid]
    # 索引の行が自分の計画なら、所在も解かずに飛ばす
    resolved = []
    monkeypatch.setattr(machine, "read_lanes", lambda *a: resolved.append(a))
    assert machine.machine_plans(OWN, {}, [machine.PlanPlace(OWN.key, str(moved), "p6")]) == [OWN] and resolved == []


def test_unreadable_index_files_are_skipped(tmp_path):
    root = identity.load().state_dir / machine.INDEX_PATH
    root.mkdir(parents=True)
    (root / "broken.json").write_text("{", encoding="utf-8")
    assert machine.indexed() == []


# --- 起動の排他と同時の判定 ---------------------------------------------------------------------------


def _all_listed(monkeypatch):
    monkeypatch.setattr(machine, "machine_plans", lambda own, table: [own])


def _place(plan_id="plan:own"):
    return machine.PlanPlace(plan_id, "/own", "p6")


def test_the_admission_hands_every_live_lane_to_the_hub_whatever_its_wrapper_does():
    """実行中の行を持つレーンの名前を、ラッパーの生死にかかわらず起動の手へ渡す（ハブが動いているラッパーとの和集合を数える。
    許可の後にラッパーだけが止まっても数え落とさない。#3989 の決定 3・7）。"""
    own = machine.PlanLanes("plan:own", (("w-a", False), ("w-b", True), ("w-c", True)))
    seen = []
    go = lambda held: seen.append(held) or {"ok": True, "detail": "起動した"}  # noqa: E731
    named_cap.write(identity.load().state_dir, 6)
    listed = {"w-c": _row(plan_name="p6", cwd="/own"), "org": {"name": "org", "running": True, "role": "organizer"}}
    assert machine.admitted(own, "w-a", lambda: listed, go, machine.PlanPlace("plan:own", "/own", "p6"))["ok"]
    assert machine.admitted(own, "w-a", lambda: None, go, machine.PlanPlace("plan:own", "/own", "p6"))["ok"]
    assert seen == [("w-b", "w-c"), ("w-b", "w-c")]


def _lock(record, busy=False):
    """起動の排他（`machine.admit_lock`）の偽。オーナーがいる間（`busy` なら常に）入ろうとすると待たずに `LockBusy` を上げ、
    入れたら出入りを `record` へ `lock-in`・`lock-out` として積む。"""
    held = False

    @contextlib.contextmanager
    def admit_lock(_seconds, clock=None):
        nonlocal held
        if busy or held:
            raise machine.LockBusy("偽の錠にオーナーがいる")
        held = True
        record("lock-in")
        try:
            yield
        finally:
            held = False
            record("lock-out")

    return admit_lock


PIDS = {"pa": "plan:w-a", "pb": "plan:w-b", "pc": "plan:w-c"}


def test_two_plans_admitting_at_once_count_inside_the_lock_and_do_not_pass_the_cap(monkeypatch, no_wait_no_child):
    """別の 2 つの計画の起動の許可が同時に求められても、一覧の取得・数え上げ・上限の判定・索引・起動の手を起動の排他の中で
    打つので、起動した後の稼働中の数が上限を超えない（#3989 の受け入れ条件 8・#4217）。A が排他を持つ間（A の起動の手の中）に
    B を打ち、B は数えずに lock で待つ。"""
    named_cap.write(identity.load().state_dir, 3)
    shared = {"o-a": _row()}
    calls, current, after, results = {}, [], {}, {}

    def record(event):
        calls[current[-1]].append(event)

    def recorded(event, real):
        def call(*a, **k):
            record(event)
            return real(*a, **k)

        return call

    monkeypatch.setattr(machine, "admit_lock", _lock(record))
    for event, name in (("count", "count"), ("over_cap", "over_cap"), ("index", "index_plan")):
        monkeypatch.setattr(machine, name, recorded(event, getattr(machine, name)))

    # 索引の計画を読むと Git を子のプロセスで打つので、レーンの無い読める計画にする（本数は共有の一覧のラッパーで数える）
    def read_lanes(cwd, plan, skip=()):
        if PIDS.get(plan) in skip:
            return None
        return machine.PlanLanes(PIDS.get(plan, f"{cwd}:{plan}"))

    monkeypatch.setattr(machine, "read_lanes", read_lanes)

    def attempt(label, wrapper, plan_name, inside=None):
        def listed():
            record("listed")
            return dict(shared)

        def go(_held):
            record("go")
            shared[wrapper] = _row(plan_name=plan_name, cwd="/own")
            # 起動したラッパーを足した後の本数で確かめる（3 本の状態で起動を許す誤りは 4 本で落ちる）
            assert len(shared) <= 3, sorted(shared)
            if inside is not None:
                inside()
            return {"ok": True, "detail": "起動した"}

        current.append(label)
        calls[label] = []
        try:
            pid = PIDS[plan_name]
            results[label] = machine.admitted(machine.PlanLanes(pid), wrapper, listed, go, machine.PlanPlace(pid, "/own", plan_name))
        finally:
            current.pop()
        after[label] = set(shared)

    attempt("A", "w-a", "pa", inside=lambda: attempt("B-first", "w-b", "pb"))
    attempt("B-again", "w-b", "pb")
    attempt("C", "w-c", "pc")

    # A が排他を持つ間の B は数えずに lock で待つ（一覧・数え上げ・上限の判定・索引・起動の手のどれも打たない）
    assert results["B-first"]["waiting"] == {"reason": "lock", "machine_lanes": None, "cap": None, "unreadable": []}
    assert results["B-first"]["ok"] is False and calls["B-first"] == []
    # 試行ごとの結果と、その試行を終えた直後の一覧。どの試行の後も上限 3 以下
    assert after == {"B-first": {"o-a", "w-a"}, "A": {"o-a", "w-a"}, "B-again": {"o-a", "w-a", "w-b"}, "C": {"o-a", "w-a", "w-b"}}
    assert all(len(names) <= 3 for names in after.values())
    assert results["A"]["ok"] is True and results["B-again"]["ok"] is True
    assert results["C"]["waiting"] == {"reason": machine.RULE, "machine_lanes": 3, "cap": 3, "unreadable": []}
    assert {p.plan_id for p in machine.indexed()} == {"plan:w-a", "plan:w-b"}
    # 計画ごとの呼び出しの順。数え上げ・上限の判定・索引・起動の手が 1 つでも排他の外へ出れば順が変わる
    admitted_calls = ["lock-in", "listed", "count", "over_cap", "index", "go", "lock-out"]
    assert calls["A"] == admitted_calls and calls["B-again"] == admitted_calls
    assert calls["C"] == ["lock-in", "listed", "count", "over_cap", "lock-out"]


def test_a_busy_lock_makes_the_admission_wait_without_counting(monkeypatch, no_wait_no_child):
    """起動の排他を取れなければ、上限も一覧も読まず、数えず、索引も起動の手も打たずに lock で待つ（#3989 の受け入れ条件 9・#4217）。"""
    hits = Counter()

    def counted(name, value=None):
        def call(*_a, **_k):
            hits[name] += 1
            return value

        return call

    monkeypatch.setattr(machine, "admit_lock", _lock(lambda _event: None, busy=True))
    for name in ("cap", "count", "over_cap", "index_plan"):
        monkeypatch.setattr(machine, name, counted(name))
    got = machine.admitted(machine.PlanLanes("plan:own"), "w-a", counted("listed", {}), counted("go", {"ok": True}), _place())
    assert isinstance(got, dict)
    assert {k: v for k, v in got.items() if k != "detail"} == {
        "ok": False,
        "waiting": {"reason": "lock", "machine_lanes": None, "cap": None, "unreadable": []},
    }
    assert hits == Counter()


def test_an_unreadable_list_decides_by_the_own_plan_alone(monkeypatch):
    own = machine.PlanLanes("plan:own", (("w-b", True),))
    go = lambda _held: {"ok": True, "detail": "起動した"}  # noqa: E731
    named_cap.write(identity.load().state_dir, 2)
    assert machine.admitted(own, "w-a", lambda: None, go, _place())["ok"]
    named_cap.write(identity.load().state_dir, 1)
    held = machine.admitted(own, "w-a", lambda: None, go, _place())
    assert held["waiting"]["reason"] == machine.OBSERVE and held["waiting"]["machine_lanes"] == 1


@pytest.mark.parametrize("env", [{"NDF_RELAY_MAX_NAMED": "0.5"}, {}])
def test_a_broken_cap_is_a_final_failure_and_not_an_exception(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    if not env:
        (identity.load().state_dir / "relay" / "hub").mkdir(parents=True, exist_ok=True)
        named_cap.path(identity.load().state_dir).write_text("[]", encoding="utf-8")
    got = machine.admitted(machine.PlanLanes("plan:own"), "w-a", lambda: {}, lambda _held: {"ok": True, "detail": ""}, _place())
    assert got["ok"] is False and got["final"] is True and "waiting" not in got


def test_a_new_cap_takes_effect_on_the_next_admission_in_the_same_process(monkeypatch):
    """マシンの設定を書くと、同じプロセスの次の判定から新しい値を使う。下げても動いているラッパーには何もしない（受け入れ条件 10）。"""
    _all_listed(monkeypatch)
    listed = {"o-a": _row(), "o-b": _row()}
    go = lambda _held: {"ok": True, "detail": "起動した"}  # noqa: E731
    named_cap.write(identity.load().state_dir, 2)
    assert machine.admitted(machine.PlanLanes("plan:own"), "w-a", lambda: listed, go, _place())["waiting"]["cap"] == 2
    named_cap.write(identity.load().state_dir, 3)
    assert machine.admitted(machine.PlanLanes("plan:own"), "w-a", lambda: listed, go, _place())["ok"]
    named_cap.write(identity.load().state_dir, 1)
    assert machine.admitted(machine.PlanLanes("plan:own"), "w-a", lambda: listed, go, _place())["waiting"]["cap"] == 1
    assert set(listed) == {"o-a", "o-b"}


def test_the_hub_refusal_after_admission_comes_back_as_a_wait():
    """許可の後にハブが本数で断った結果（`refusal: max-named`）は、起動の手の待ちになる（受け入れ条件 7）。"""
    out = '{"tool": "relay", "status": "stopped", "summary": "3 本が動いており上限 2 本を超える", "cause": {"kind": "refused"}, "items": [{"refusal": "max-named", "running": 3, "cap": 2}]}'
    got = launch.read_spawn(out + "\n", "", 1)
    assert got == {
        "ok": False,
        "waiting": {"reason": "machine-lanes", "machine_lanes": 3, "cap": 2, "unreadable": []},
        "detail": "3 本が動いており上限 2 本を超える",
    }
    other = '{"tool": "relay", "status": "stopped", "summary": "バージョンが違う", "items": []}'
    assert "waiting" not in launch.read_spawn(other, "", 1)


# --- 耐久ワークフローの判断と status ------------------------------------------------------------------


def _step(pol, state, done):
    nxt = run_decide.decide(pol, state, done)
    return nxt.act, nxt.state


def test_a_launch_held_by_the_machine_cap_is_not_a_failure_and_is_tried_again():
    pol = Policy(poll=30.0, attempts=2)
    snap = run_decide.with_stalls(run_decide.snapshot("running", False, [("a", "r1")], []), [])
    act, state = _step(pol, RunState(), Done(Observe(), snap))
    held = machine.waiting(machine.RULE, machine.Count(6, (), ()), 6)
    act, state = _step(pol, state, Done(act, held))
    assert act == Sleep(30.0) and state.count("a/r1") == 0 and state.failed == () and state.launched == ()
    assert state.unreported == ()
    act, state = _step(pol, state, Done(Observe(), snap))
    assert act == Launch("a", "r1")


def test_waiting_lanes_start_in_plan_order_once_a_seat_frees(monkeypatch):
    """上限 1・稼働中 1・待ち 2 で、稼働中が終わった後の周に並びの先の 1 本だけが起動する（受け入れ条件 6）。"""
    pol = Policy(poll=30.0, attempts=2)
    listed = {"busy": _row()}
    named_cap.write(identity.load().state_dir, 1)
    _all_listed(monkeypatch)
    launched = []

    def perform(act):
        if isinstance(act, Launch):

            def go(_held):
                listed[act.lane] = _row(plan_name="p6", cwd="/own")
                launched.append(act.lane)
                return {"ok": True, "detail": "起動した"}

            return machine.admitted(machine.PlanLanes("plan:own"), act.lane, lambda: dict(listed), go, _place())
        return None

    snap = run_decide.with_stalls(run_decide.snapshot("running", False, [("a", "r1"), ("b", "r1")], []), [])
    state, act = RunState(), Observe()
    for _ in range(4):
        act, state = _step(pol, state, Done(act, snap if isinstance(act, Observe) else perform(act)))
        if isinstance(act, Sleep):
            break
    assert launched == [] and state.failed == ()
    del listed["busy"]
    act = Observe()
    for _ in range(4):
        act, state = _step(pol, state, Done(act, snap if isinstance(act, Observe) else perform(act)))
        if isinstance(act, Sleep):
            break
    assert launched == ["a"]


def _status(opened, monkeypatch, decls, listed, reads=None):
    with store.transaction(opened) as conn:
        pid = plan.create_plan(conn, REPO, doc(*decls), True)
    found = liveness.Probes(wrapper_name=lambda name: f"w-{name}", wrappers=lambda: listed, sprint=lambda sid: None)

    def read_lanes(cwd, plan_name, skip=()):
        if reads is not None:
            reads.append((cwd, plan_name))
        return machine.PlanLanes(f"{cwd}:{plan_name}", readable=False)

    monkeypatch.setattr(machine, "read_lanes", read_lanes)
    with store.transaction(opened) as conn:
        return plan.status(conn, pid, datetime.now(UTC), found)


def test_status_shows_a_lane_waiting_for_the_machine_and_the_machine_summary(opened, monkeypatch):
    """起動を待っているレーンの行に理由・数・上限が、集計にマシンの稼働中の数と上限が載る（受け入れ条件 9）。"""
    listed = {f"x-{i}": _row() for i in range(6)}
    out = _status(opened, monkeypatch, [decl("a", [[1]])], listed)
    (item,) = out["items"]
    assert item["machine_wait"] == {"reason": "machine-lanes", "machine_lanes": 6, "cap": 6, "unreadable": ["/other:q1"]}
    assert out["metrics"]["machine"] == {"lanes": 6, "cap": 6, "unreadable": ["/other:q1"], "others": True, "error": None}


def test_status_without_a_wakeable_lane_reads_no_other_plan(opened, monkeypatch):
    reads = []
    listed = {f"x-{i}": _row() for i in range(2)}
    with store.transaction(opened) as conn:
        pid = plan.create_plan(conn, REPO, doc(decl("a", [[1]])), True)
        plan.pause_plan(conn, pid, "区切り")
    found = liveness.Probes(wrapper_name=lambda name: f"w-{name}", wrappers=lambda: listed, sprint=lambda sid: None)
    monkeypatch.setattr(machine, "read_lanes", lambda *a, **k: reads.append(a))
    with store.transaction(opened) as conn:
        out = plan.status(conn, pid, datetime.now(UTC), found)
    assert reads == [] and "machine_wait" not in out["items"][0]
    assert out["metrics"]["machine"]["lanes"] == 2 and out["metrics"]["machine"]["others"] is False


def test_status_reads_the_other_plans_once_for_all_wakeable_lanes(opened, monkeypatch):
    """起動できるレーンが複数あっても、ほかの計画は status 1 回で 1 計画 1 度だけ読む（処理の回数）。"""
    reads = []
    listed = {"x-0": _row(), "y-0": _row(plan_name="q2")}
    out = _status(
        opened, monkeypatch, [decl("a", [[1]]), decl("b", [[2]], touches=("src/b",)), decl("c", [[3]], touches=("src/c",))], listed, reads
    )
    assert len(out["items"]) == 3 and len(reads) == 2


def test_status_names_the_lock_and_the_unreadable_list(opened, monkeypatch):
    with machine.admit_lock(1.0):
        out = _status(opened, monkeypatch, [decl("a", [[1]])], {})
    assert out["items"][0]["machine_wait"] == {"reason": "lock", "machine_lanes": None, "cap": None, "unreadable": []}


LOCK_WAIT = {"reason": "lock", "machine_lanes": None, "cap": None, "unreadable": []}


def _view_rows(opened, decls, pause=False):
    with store.transaction(opened) as conn:
        pid = plan.create_plan(conn, REPO, doc(*decls), True)
        if pause:
            plan.pause_plan(conn, pid, "区切り")
        return lane.read_view(conn, pid)


THREE = [decl("a", [[1]]), decl("b", [[2]], touches=("src/b",)), decl("c", [[3]], touches=("src/c",))]


def test_machine_waits_without_the_lock_makes_every_wakeable_lane_wait_for_it_without_counting_each(opened, monkeypatch):
    """起動の排他を取れなければ、起動できるレーンをどれも lock の待ちにし、レーンごとには数えない。集計は数えて上限も載せる。"""
    view, rows = _view_rows(opened, THREE)
    monkeypatch.setattr(machine, "read_lanes", lambda cwd, plan_name, skip=(): machine.PlanLanes(f"{cwd}:{plan_name}", readable=False))
    real, skips = machine.count, []
    monkeypatch.setattr(machine, "count", lambda found, table, skip: skips.append(skip) or real(found, table, skip))
    waits, summary = plan.machine_waits(view, rows, {"x-0": _row()}, False)
    assert waits == {"a": LOCK_WAIT, "b": LOCK_WAIT, "c": LOCK_WAIT}
    assert skips == [""]
    assert summary == {"lanes": 1, "cap": 6, "unreadable": ["/other:q1"], "others": True, "error": None}


def test_machine_waits_without_the_lock_and_an_unreadable_list_still_waits_for_the_lock(opened):
    """一覧を読めなくても、排他を取れなければ自分の計画で数えた待ち（observe）でなく lock の待ちにする。"""
    view, rows = _view_rows(opened, THREE[:2])
    waits, summary = plan.machine_waits(view, rows, None, False)
    assert waits == {"a": LOCK_WAIT, "b": LOCK_WAIT}
    assert summary == {"lanes": 0, "cap": 6, "unreadable": [], "others": False, "error": None}


def test_machine_waits_without_the_lock_and_without_a_wakeable_lane_has_no_wait(opened):
    view, rows = _view_rows(opened, THREE[:1], pause=True)
    waits, summary = plan.machine_waits(view, rows, {"x-0": _row()}, False)
    assert waits == {} and summary == {"lanes": 1, "cap": 6, "unreadable": [], "others": False, "error": None}


def test_machine_waits_with_a_broken_cap_reports_the_cap_before_the_lock(opened, monkeypatch):
    """上限を読めなければ、排他を取れなくても待ちを載せず、集計の cap を null・error に文を載せる。"""
    view, rows = _view_rows(opened, THREE[:1])
    path = named_cap.path(identity.load().state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("broken", encoding="utf-8")
    waits, summary = plan.machine_waits(view, rows, {}, False)
    assert waits == {}
    assert summary["cap"] is None and "マシンの設定" in summary["error"]
    assert {k: summary[k] for k in ("lanes", "unreadable", "others")} == {"lanes": 0, "unreadable": [], "others": True}


def test_machine_waits_with_the_lock_counts_each_wakeable_lane_skipping_itself(opened, monkeypatch):
    """排他を取れれば、起動できるレーンごとに自分のラッパーを除いて数え、上限に当たるレーンだけを待ちにする。"""
    view, rows = _view_rows(opened, THREE[:2])
    named_cap.write(identity.load().state_dir, 2)
    real, skips = machine.count, []
    monkeypatch.setattr(machine, "count", lambda found, table, skip: skips.append(skip) or real(found, table, skip))
    monkeypatch.setattr(machine, "machine_plans", lambda own, table: [own])
    a = launch.wrapper_name(str(view.repo), view.name, "a")
    b = launch.wrapper_name(str(view.repo), view.name, "b")
    listed = {"x-0": _row(), a: _row(plan_name=view.name, cwd=str(view.repo))}
    waits, summary = plan.machine_waits(view, rows, listed, True)
    assert skips == ["", a, b]
    assert waits == {"b": {"reason": "machine-lanes", "machine_lanes": 2, "cap": 2, "unreadable": []}}
    assert summary == {"lanes": 2, "cap": 2, "unreadable": [], "others": True, "error": None}


def test_status_with_an_unreadable_list_waits_by_the_own_plan(opened, monkeypatch):
    named_cap.write(identity.load().state_dir, 1)
    with store.transaction(opened) as conn:
        pid = plan.create_plan(conn, REPO, doc(decl("a", [[1]]), decl("b", [[2]], touches=("src/b",))), True)
        view, _ = lane.read_plan(conn, pid)
        sid = store.create_machine(conn, ("sprint", REPO, "a-r1"), view.lanes["a"].machine_id, None, [])
        lane.start_row(conn, pid, "a", "r1", sid)
    found = liveness.Probes(wrapper_name=lambda name: f"w-{name}", wrappers=lambda: None, sprint=lambda sid: None)
    with store.transaction(opened) as conn:
        out = plan.status(conn, pid, datetime.now(UTC), found)
    b = next(i for i in out["items"] if i["lane"] == "b")
    assert b["machine_wait"]["reason"] == "observe" and b["machine_wait"]["machine_lanes"] == 1


def test_status_with_a_broken_cap_still_shows_the_plan(opened, monkeypatch):
    path = named_cap.path(identity.load().state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("broken", encoding="utf-8")
    out = _status(opened, monkeypatch, [decl("a", [[1]])], {})
    assert out["status"] == "ok" and "machine_wait" not in out["items"][0]
    assert out["metrics"]["machine"]["cap"] is None and "マシンの設定" in out["metrics"]["machine"]["error"]


def test_cap_reads_the_machine_setting_before_the_env_of_the_relay_hub(monkeypatch):
    assert machine.cap({}) == 6
    assert machine.cap({"NDF_RELAY_MAX_NAMED": "9"}) == 9
    named_cap.write(identity.load().state_dir, 4)
    assert machine.cap({"NDF_RELAY_MAX_NAMED": "9"}) == 4
    named_cap.write(identity.load().state_dir, None)
    with pytest.raises(named_cap.NamedCapError):
        machine.cap({"NDF_RELAY_MAX_NAMED": "0"})
