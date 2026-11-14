"""計画を進める耐久ワークフロー（#3097 の受け入れ条件 1・2・4）。

判断（`run_decide`）は DB を使わずに確かめる。写し（`read_snapshot`）は `opened`（SQLite と PostgreSQL）で、
耐久ワークフロー（`advance`）は DBOS を起こして、レーンを起こす所（`launch.spawn`）だけを差し替えて流す。
差し替えた起動は、起きた conductor の代わりにスプリントを作って行を始め、すぐ終える。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.core import durable, loop
from ndf.progress import lane, plan, store
from ndf.progress.lane import launch
from ndf.progress.plan import run, run_decide
from ndf.progress.plan.run_decide import Complete, Done, Launch, Observe, Policy, Report, RunState, Sleep
from plan_decls import REPO
from plan_decls import decl as _decl
from plan_decls import doc as _doc

POLICY = Policy(poll=30.0, attempts=2)
# 耐久ワークフローを流すテストの読み直しの間隔（起こした後の待ちを短くする）
FAST = Policy(poll=0.05, attempts=2)


def _snap(state="running", complete=False, wakeable=(), running=(), reported=(), answered=()):
    return run_decide.snapshot(state, complete, list(wakeable), list(running), list(reported), list(answered))


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


def test_a_row_with_an_answer_but_no_next_wakes_its_conductor_once_per_answer():
    """答えが載った後に next が打たれていない行は、猶予を待たずに conductor を起こす。同じ答えでは 1 回だけ（3346-5）。"""
    answered = [("a", "r1", "s1", 7)]
    act, state, _ = _step(RunState(), Done(Observe(), _snap(running=[("a", "r1", "s1")], answered=answered)))
    assert act == Launch("a", "r1", resume="s1", seq=7) and act.key == "next:s1:7"
    act, state, _ = _step(state, Done(act, {"ok": True, "detail": "起こした"}))
    assert act == Sleep(30.0)
    act, state, _ = _step(state, Done(Observe(), _snap(running=[("a", "r1", "s1")], answered=answered)))
    assert act == Sleep(30.0)
    # 次の答えが載れば、新しい鍵で起こし直す
    act, state, _ = _step(state, Done(Observe(), _snap(running=[("a", "r1", "s1")], answered=[("a", "r1", "s1", 9)])))
    assert act == Launch("a", "r1", resume="s1", seq=9)


def test_a_stopped_plan_does_not_wake_a_row_for_its_answer():
    snap = _snap(state="cancelled", running=[("a", "r1", "s1")], answered=[("a", "r1", "s1", 7)])
    act, _, _ = _step(RunState(), Done(Observe(), snap))
    assert act == Launch("a", "r1", cancel="s1")


def test_a_failed_launch_is_retried_on_the_next_poll_and_given_up_after_the_attempts():
    state = RunState()
    act, state, _ = _step(state, Done(Observe(), _snap(wakeable=[("a", "r1")])))
    act, state, _ = _step(state, Done(act, {"ok": False, "detail": "tmux が無い"}))
    assert act == Sleep(30.0) and state.count("a/r1") == 1
    act, state, _ = _step(state, Done(Observe(), _snap(wakeable=[("a", "r1")])))
    assert act == Launch("a", "r1")
    act, state, _ = _step(state, Done(act, {"ok": False, "detail": "tmux が無い"}))
    # 回数が尽きたら、起こせなかったことをオーガナイザーへ知らせる
    assert act == Report("a", "a/r1", "a/r1: tmux が無い") and state.failed == ("a/r1",) and len(state.errors) == 2
    act, state, _ = _step(state, Done(act, "request-1"))
    assert act == Sleep(30.0) and state.reported == ("a/r1",)
    # 依頼が開いている間は起こし直さない
    act, state, _ = _step(state, Done(Observe(), _snap(wakeable=[("a", "r1")], reported=["a/r1"])))
    assert act == Sleep(30.0)
    # オーガナイザーが依頼を閉じたら、回数を戻して起こし直す
    act, state, _ = _step(state, Done(Observe(), _snap(wakeable=[("a", "r1")])))
    assert act == Launch("a", "r1") and state.failed == () and state.count("a/r1") == 0 and state.reported == ()


def test_a_launch_that_cannot_succeed_is_reported_without_retrying():
    act, state, _ = _step(RunState(), Done(Observe(), _snap(wakeable=[("a", "r1"), ("b", "r1")])))
    act, state, _ = _step(state, Done(act, {"ok": False, "final": True, "detail": "loop の行"}))
    assert act == Report("a", "a/r1", "a/r1: loop の行") and state.count("a/r1") == 1
    act, state, _ = _step(state, Done(act, "request-1"))
    assert act == Launch("b", "r1")


def test_a_launch_that_cannot_succeed_stays_failed_after_the_request_is_closed():
    """打ち直しても起こせない失敗は、依頼を閉じても起こし直さない（閉じるたびに同じ依頼が開き直さない）。"""
    act, state, _ = _step(RunState(), Done(Observe(), _snap(wakeable=[("a", "r1")])))
    act, state, _ = _step(state, Done(act, {"ok": False, "final": True, "detail": "loop の行"}))
    act, state, _ = _step(state, Done(act, "request-1"))
    act, state, _ = _step(state, Done(Observe(), _snap(wakeable=[("a", "r1")])))
    assert act == Sleep(30.0) and state.failed == ("a/r1",) and state.reported == () and state.unreported == ()
    # 行が動けば（鍵が変われば）その行は起こす
    act, state, _ = _step(state, Done(Observe(), _snap(wakeable=[("a", "r2")])))
    assert act == Launch("a", "r2")


def test_a_launched_lane_that_does_not_start_its_row_in_time_counts_as_a_failure():
    """起こした conductor が期限までに行を始めなければ、起動の失敗として打ち直し、尽きたら知らせる。"""
    pol = Policy(poll=30.0, attempts=2, up=60.0)

    def step(state, done):
        nxt = run_decide.decide(pol, state, done)
        return nxt.act, nxt.state

    act, state = step(RunState(), Done(Observe(), _snap(wakeable=[("a", "r1")])))
    act, state = step(state, Done(act, {"ok": True, "detail": "起こした"}))
    act, state = step(state, Done(Observe(), _snap(wakeable=[("a", "r1")])))
    assert act == Sleep(30.0) and state.launched == ("a/r1",)
    act, state = step(state, Done(Observe(), _snap(wakeable=[("a", "r1")])))
    assert act == Launch("a", "r1") and state.count("a/r1") == 1 and state.launched == ()
    act, state = step(state, Done(act, {"ok": True, "detail": "起こした"}))
    act, state = step(state, Done(Observe(), _snap(wakeable=[("a", "r1")])))
    act, state = step(state, Done(Observe(), _snap(wakeable=[("a", "r1")])))
    assert isinstance(act, Report) and act.key == "a/r1" and "60 秒" in act.detail and state.failed == ("a/r1",)


def test_a_report_the_plan_could_not_take_leads_to_reading_the_plan_again():
    """起動の最中に計画が止められ、依頼を書けなかった（None）なら、知らせた扱いにせず読み直す。"""
    act, state, _ = _step(RunState(), Done(Observe(), _snap(wakeable=[("a", "r1")])))
    act, state, _ = _step(state, Done(act, {"ok": False, "final": True, "detail": "loop の行"}))
    assert isinstance(act, Report)
    act, state, _ = _step(state, Done(act, None))
    assert act == Observe() and state.reported == () and state.unreported == ()


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


def test_a_stopped_plan_whose_cancel_cannot_be_launched_ends_as_cancel_failed_without_a_report():
    """止めた計画の進行表へは依頼を書けないので、中止の起動が尽きたら Report を打たずに結果へ載せて終える。"""
    running = [("a", "r1", "s1"), ("b", "r1", "s2")]
    act, state, _ = _step(RunState(), Done(Observe(), _snap(state="cancelled", running=running)))
    act, state, _ = _step(state, Done(act, {"ok": False, "final": True, "detail": "止められない"}))
    # 尽きた中止があっても、ほかの実行中の行の中止は起こす
    assert act == Launch("b", "r1", cancel="s2") and state.failed == ("cancel:s1",)
    act, state, finished = _step(state, Done(act, {"ok": False, "final": True, "detail": "止められない"}))
    assert finished and state.outcome == "cancel_failed" and state.failed == ("cancel:s1", "cancel:s2")


def test_a_paused_plan_wakes_only_answered_rows_and_ends_as_paused_once_nothing_runs():
    """区切りで止めた計画は次の行を起こさず、実行中の行の答えの後の起こし直しは続け、行が無くなれば paused で終える。"""
    snap = _snap(state="paused", running=[("a", "r1", "s1")], answered=[("a", "r1", "s1", 4)])
    act, state, _ = _step(RunState(), Done(Observe(), snap))
    assert act == Launch("a", "r1", resume="s1", seq=4)
    act, state, _ = _step(state, Done(act, {"ok": True, "detail": "起こした"}))
    assert act == Sleep(30.0)
    _, state, finished = _step(state, Done(Observe(), _snap(state="paused")))
    assert finished and state.outcome == "paused"


def test_a_plan_not_yet_started_is_only_watched():
    act, _, _ = _step(RunState(), Done(Observe(), _snap(state="pending", wakeable=[("a", "r1")])))
    assert act == Sleep(30.0)


def test_policy_reads_the_poll_from_the_environment(monkeypatch):
    monkeypatch.setenv(run.POLL_ENV, "5")
    assert run.policy().poll == 5.0 and run.policy().up > 5.0
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


def test_snapshot_lists_a_running_row_whose_answer_has_no_next_and_drops_it_after_the_mark(opened):
    pid = _create(opened, _doc(_decl("a", [[1]])))
    with store.transaction(opened) as conn:
        sid = _start(conn, pid, "a", "r1")
        seq = lane.record_answer(conn, sid, {"answer": "approved"})
    assert _snapshot(opened, pid)["answered"] == [["a", "r1", sid, seq]]
    with store.transaction(opened) as conn:
        text = run.first_input(conn, pid, Launch("a", "r1", resume=sid, seq=seq))
        lane.record_resumed(conn, sid, "conductor")
    assert '"resumed"' in text and '"next"' in text and sid in text
    assert _snapshot(opened, pid)["answered"] == []


def test_first_input_refuses_a_lane_of_another_repository(opened):
    """別のリポジトリを宣言したレーンを、計画のリポジトリで起こさない。"""
    pid = _create(opened, _doc({**_decl("a", [[1]]), "repo": "https://example.invalid/o/other"}))
    with store.transaction(opened) as conn, pytest.raises(launch.Unsupported, match="other"):
        run.first_input(conn, pid, Launch("a", "r1"))


def test_first_input_treats_a_lane_naming_the_plan_repository_as_the_same_repository(opened):
    """repo に計画のリポジトリを書いたレーンは、書かないレーンと同じリポジトリとして起こす（検査と同じ規則）。"""
    pid = _create(opened, _doc({**_decl("a", [[1]]), "repo": REPO}))
    with store.transaction(opened) as conn:
        assert "progress" in run.first_input(conn, pid, Launch("a", "r1"))


def test_snapshot_lists_the_launches_with_an_open_launch_failed_request(opened):
    pid = _create(opened, _doc(_decl("a", [[1]])))
    with store.transaction(opened) as conn:
        key = plan.open_request(
            conn, pid, {"lane": "a", "kind": run.LAUNCH_FAILED, "launch": "a/r1", "detail": "x"}, "起こせない", "workflow"
        )
    assert _snapshot(opened, pid)["reported"] == ["a/r1"]
    with store.transaction(opened) as conn:
        plan.close_request(conn, pid, key, "直した")
    assert _snapshot(opened, pid)["reported"] == []


def test_a_report_is_not_written_once_the_plan_is_stopped(opened):
    pid = _create(opened, _doc(_decl("a", [[1]])))
    with store.transaction(opened) as conn:
        plan.stop_plan(conn, pid, "放棄")
        assert run.write_report(conn, pid, "a", "a/r1", "x") is None


# --- 耐久ワークフロー -------------------------------------------------------------------------------


@pytest.fixture
def where(progress_url, root, tmp_path):
    # 副命令（`plan.cli._where`）と同じく、表を作る前に置き場を用意する。用意の無い置き場に SQLite の表を作ると、
    # 耐久ワークフローを起こすときの用意が `format.json` の無い置き場として表ごと退避する
    durable.prepare_store(root)
    return run.Where(root=root, url=progress_url, repo=REPO, cwd=tmp_path)


def _no_wrapper(monkeypatch):
    """起こす前に止めるラッパーは無い（止めた名前を記録する）。"""
    stopped = []

    def stop(repo, plan_name, lane_name):
        stopped.append(lane_name)
        return {"ok": True, "detail": "動いていない"}

    monkeypatch.setattr(launch, "stop", stop)
    return stopped


def _conductor(where, outcome):
    """起きた conductor の代わり: 起こされた行を始め、`outcome` で終える。"""
    launched = []

    def spawn(repo: str, plan_name: str, lane_name: str, cwd: Path, first: str) -> dict:
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
    stopped = _no_wrapper(monkeypatch)
    monkeypatch.setattr(launch, "spawn", spawn)
    final = run.advance(where, "p6", FAST)
    assert final["outcome"] == "completed" and final["failed"] == []
    assert launched == [("a", "r1"), ("a", "r2"), ("b", "r1")]
    # 前の行を終えた conductor のラッパーが残っても起こせるよう、行を始める前にも同じ名前のラッパーを止める
    assert stopped == ["a", "a", "b"]
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
    cancelled, stopped = [], []

    def stop(repo, plan_name, lane_name):
        stopped.append(lane_name)
        return {"ok": True, "detail": "止めた"}

    def spawn(repo, plan_name, lane_name, cwd, first):
        assert "cancel" in first and sid in first and stopped == [lane_name]
        with store.open_store(where.url, where.root) as st, store.transaction(st) as conn:
            lane.end_row(conn, sid, "cancelled", "conductor")
        cancelled.append(lane_name)
        return {"ok": True, "detail": "起こした"}

    monkeypatch.setattr(launch, "stop", stop)
    monkeypatch.setattr(launch, "spawn", spawn)
    final = run.advance(where, "p6", FAST)
    assert final["outcome"] == "stopped" and cancelled == ["a"]


def test_a_rerun_after_cancel_failed_starts_a_new_workflow_and_retries_the_cancel(where, monkeypatch):
    """中止を起こせずに `cancel_failed` で終えた計画は、打ち直すと新しいワークフローで中止を試し直す。"""
    with store.open_store(where.url, where.root) as st:
        pid = _create(st, _doc(_decl("a", [[1], [2]])))
        with store.transaction(st) as conn:
            sid = _start(conn, pid, "a", "r1")
            plan.stop_plan(conn, pid, "放棄")
    _no_wrapper(monkeypatch)
    monkeypatch.setattr(launch, "spawn", lambda repo, plan_name, lane_name, cwd, first: {"ok": False, "detail": "上限"})
    assert run.advance(where, "p6", FAST)["outcome"] == "cancel_failed"

    def spawn(repo, plan_name, lane_name, cwd, first):
        with store.open_store(where.url, where.root) as st, store.transaction(st) as conn:
            lane.end_row(conn, sid, "cancelled", "conductor")
        return {"ok": True, "detail": "起こした"}

    monkeypatch.setattr(launch, "spawn", spawn)
    assert run.advance(where, "p6", FAST)["outcome"] == "stopped"
    assert run.workflow_id("p6", 2) != run.workflow_id("p6")


def test_a_paused_plan_ends_its_workflow_and_a_resumed_plan_continues_with_a_new_one(where, monkeypatch):
    """区切りで止めた計画の耐久ワークフローは paused で終わり、同じ名前で再開すると次の ID で残りの行を流す。"""
    with store.open_store(where.url, where.root) as st:
        pid = _create(st, _doc(_decl("a", [[1], [2]])))
        with store.transaction(st) as conn:
            plan.pause_plan(conn, pid, "区切りで止める")
    spawn, launched = _conductor(where, "completed")
    _no_wrapper(monkeypatch)
    monkeypatch.setattr(launch, "spawn", spawn)
    assert run.advance(where, "p6", FAST)["outcome"] == "paused" and launched == []
    with store.open_store(where.url, where.root) as st, store.transaction(st) as conn:
        plan.start_plan(conn, pid)
    assert run.advance(where, "p6", FAST)["outcome"] == "completed"
    assert launched == [("a", "r1"), ("a", "r2")]


def test_the_workflow_reports_a_lane_it_cannot_launch_to_the_plan_table(where, monkeypatch):
    """起こせないレーン（開発ループの行）は、計画の進行表の依頼として wait へ届く。"""
    loop_lane = {"name": "a", "plans": 1, "touches": ["src/a"], "after": [], "rows": [{"kind": "loop", "loop": "backlog"}]}
    with store.open_store(where.url, where.root) as st:
        pid = _create(st, _doc(loop_lane, _decl("b", [[3]], touches=("src/b",))))

    def spawn(repo, plan_name, lane_name, cwd, first):
        # b を流した後に計画を止め、ワークフローを終わらせる
        with store.open_store(where.url, where.root) as st, store.transaction(st) as conn:
            lane.end_row(conn, _start(conn, pid, lane_name, "r1"), "completed", "conductor")
            plan.stop_plan(conn, pid, "試験の終わり")
        return {"ok": True, "detail": "起こした"}

    _no_wrapper(monkeypatch)
    monkeypatch.setattr(launch, "spawn", spawn)
    final = run.advance(where, "p6", FAST)
    assert final["outcome"] == "stopped" and final["failed"] == ["a/r1"]
    with store.open_store(where.url, where.root) as st, store.transaction(st) as conn:
        view, _ = lane.read_plan(conn, pid)
    (req,) = view.open_requests()
    assert req.lane == "a" and req.body["kind"] == run.LAUNCH_FAILED and req.body["launch"] == "a/r1"


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
