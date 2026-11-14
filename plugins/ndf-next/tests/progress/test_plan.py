"""計画の進行表と副命令 `ndf lanes`（#3097 の受け入れ条件 1・4・5）。"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ndf import cli, identity
from ndf.contracts import causes
from ndf.core import durable
from ndf.progress import lane, model, plan, store
from ndf.progress.plan import cli as plan_cli
from ndf.progress.plan import run as plan_run

REPO = "https://example.invalid/o/r"


def _decl(name, rows, touches=("src/a",), after=()):
    return {
        "name": name,
        "plans": 1,
        "touches": list(touches),
        "after": list(after),
        "rows": [{"kind": "sprint", "issues": i} for i in rows],
    }


def _doc(*lanes, **extra):
    return {"version": 1, "name": "p6", "rules": ["develop へ出す"], "lanes": list(lanes), **extra}


def _two(st):
    with store.transaction(st) as conn:
        return plan.create_plan(conn, REPO, _doc(_decl("a", [[1], [2]]), _decl("b", [[3]], touches=("src/b",))), True)


def _view(st, pid):
    with store.transaction(st) as conn:
        view, _ = lane.read_plan(conn, pid)
        return view, lane.folded(lane.read_lanes(conn, view))


def _refused(st, fn):
    with pytest.raises(model.Refused) as got, store.transaction(st) as conn:
        fn(conn)
    return got.value.reason


def _run_sprint(st, pid, lane_name, key, name, outcome="completed"):
    with store.transaction(st) as conn:
        view, _ = lane.read_plan(conn, pid)
        sid = store.create_table(conn, ("sprint", REPO, name), view.lanes[lane_name].table_id, None, [])
        lane.start_row(conn, pid, lane_name, key, sid)
        if outcome is not None:
            lane.end_row(conn, sid, outcome, "workflow")
    return sid


# --- レーンをまたぐ変更（オーガナイザー） --------------------------------------------------------------


def test_lane_added_to_a_running_plan_answers_the_request(opened):
    pid = _two(opened)
    with store.transaction(opened) as conn:
        req = plan.open_request(conn, pid, {"lane": "a", "want": "レーンを足す"}, "課題が増えた", "workflow")
        plan.add_lane(conn, pid, _decl("c", [[7]], touches=("docs/",)), req)
    view, rows = _view(opened, pid)
    assert list(view.lanes) == ["a", "b", "c"] and [r.key for r in rows["c"]] == ["r1"]
    assert view.open_requests() == [] and view.requests[req].closed


def test_lane_that_breaks_a_rule_is_not_added(opened):
    pid = _two(opened)
    assert "I19" in _refused(opened, lambda conn: plan.add_lane(conn, pid, _decl("c", [[3]], touches=("docs/",))))
    view, _ = _view(opened, pid)
    assert list(view.lanes) == ["a", "b"]


def test_changed_lane_is_checked_against_the_other_lanes(opened):
    pid = _two(opened)
    reason = _refused(opened, lambda conn: plan.change_lane(conn, pid, "b", {"touches": ["src/a/x.py"]}, "広げる"))
    assert "I13" in reason
    with store.transaction(opened) as conn:
        plan.change_lane(conn, pid, "b", {"touches": ["src/a/x.py"], "after": ["a"]}, "a の後に回す")
    view, _ = _view(opened, pid)
    assert view.lanes["b"].touches == ("src/a/x.py",) and view.lanes["b"].after == ("a",)


def test_lane_limit_does_not_count_lanes_that_ended(opened):
    pid = _two(opened)
    assert "I20" in _refused(opened, lambda conn: plan.change_lane(conn, pid, lane.PLAN_ROW, {"max_lanes": 1}, "減らす"))
    _run_sprint(opened, pid, "b", "r1", "s-b1")
    with store.transaction(opened) as conn:
        plan.change_lane(conn, pid, lane.PLAN_ROW, {"max_lanes": 1}, "b が終わった")
    view, _ = _view(opened, pid)
    assert view.max_lanes == 1


def test_row_moved_to_another_lane(opened):
    pid = _two(opened)
    with store.transaction(opened) as conn:
        key = plan.move_row(conn, pid, ("a", "r2"), "b", "b で流す")
    _, rows = _view(opened, pid)
    assert [r.state for r in rows["a"]] == ["pending", "cancelled"]
    assert [(r.key, r.input["issues"]) for r in rows["b"]] == [("r1", [3]), (key, [2])]


# --- 中止と完了 ------------------------------------------------------------------------------------


def test_stop_cancels_pending_rows_and_hands_running_sprints_to_their_conductors(opened):
    pid = _two(opened)
    sid = _run_sprint(opened, pid, "a", "r1", "s-a1", outcome=None)
    with store.transaction(opened) as conn:
        running = plan.stop_plan(conn, pid, "計画を放棄する")
    view, rows = _view(opened, pid)
    assert running == [sid] and view.state == "cancelled"
    assert [r.state for r in rows["a"]] == ["running", "cancelled"] and rows["b"][0].state == "cancelled"
    # 実行中の行は、レーンの conductor がスプリントを中止したときに終わる
    with store.transaction(opened) as conn:
        assert lane.end_row(conn, sid, "cancelled", "conductor") is None
    _, rows = _view(opened, pid)
    assert rows["a"][0].state == "cancelled"
    assert "中止" in _refused(opened, lambda conn: plan.add_lane(conn, pid, _decl("c", [[9]], touches=("docs/",))))


def test_plan_completes_only_when_every_row_ended_and_no_request_is_open(opened):
    pid = _two(opened)
    with store.transaction(opened) as conn:
        assert plan.complete_plan(conn, pid) is False
        req = plan.open_request(conn, pid, {"lane": "a"}, "順番を変えたい", "organizer")
    for lane_name, key, name in (("a", "r1", "s1"), ("a", "r2", "s2"), ("b", "r1", "s3")):
        _run_sprint(opened, pid, lane_name, key, name)
    with store.transaction(opened) as conn:
        assert plan.complete_plan(conn, pid) is False
        plan.close_request(conn, pid, req, "採らない")
        assert plan.complete_plan(conn, pid) is True
    view, _ = _view(opened, pid)
    assert view.state == "completed"


def test_status_is_built_from_the_two_tables(opened):
    pid = _two(opened)
    _run_sprint(opened, pid, "a", "r1", "s1")
    with store.transaction(opened) as conn:
        out = plan.status(conn, pid, datetime.now(UTC))
    assert out["status"] == "ok" and out["metrics"]["state"] == "running"
    assert [(i["lane"], i["state"]) for i in out["items"]] == [("a", "pending"), ("b", "pending")]
    assert [r["label"] for r in out["items"][0]["rows"]] == ["完了", "未着手"]
    assert out["metrics"]["wakeable"] == [["a", "r2"], ["b", "r1"]] and out["metrics"]["complete"] is False


# --- 副命令 ----------------------------------------------------------------------------------------


@pytest.fixture
def detached(monkeypatch):
    """`start` が切り離して起こす `lanes run` の呼び出し（起こさずに記録する）。"""
    calls = []
    monkeypatch.setattr(plan_run, "detach", lambda name, cwd: calls.append((name, cwd)) or 4242)
    return calls


@pytest.fixture
def repo(tmp_path, monkeypatch, detached):
    path = tmp_path / "work"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    monkeypatch.chdir(path)
    yield path
    durable.release_store(durable.store_root(identity.load().name, path))


def _cli(capsys, *argv: str) -> tuple[int, dict]:
    code = plan_cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _plan_file(path: Path, doc: dict) -> str:
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def test_subcommand_is_in_the_table():
    assert cli.COMMANDS[plan.TOOL] == plan_cli.__name__


def test_cli_creates_starts_and_reports_the_plan(repo, capsys, tmp_path, detached):
    path = _plan_file(tmp_path / "plan.json", _doc(_decl("a", [[1, 2]])))
    code, out = _cli(capsys, "init", path)
    assert code == 0 and out["metrics"]["state"] == "pending"
    code, out = _cli(capsys, "init", path)
    assert out["cause"]["kind"] == "refused" and out["metrics"]["current_id"]
    assert detached == []
    code, out = _cli(capsys, "start", path)
    assert code == 0 and out["metrics"]["state"] == "running"
    assert detached == [("p6", repo)] and out["metrics"]["run_pid"] == 4242
    code, out = _cli(capsys, "status", "p6")
    assert code == 0 and out["items"][0]["rows"][0]["input"]["issues"] == [1, 2]


def test_cli_row_carry_keeps_the_dropped_issue_in_the_same_lane(repo, capsys, tmp_path):
    _cli(capsys, "start", _plan_file(tmp_path / "plan.json", _doc(_decl("a", [[1, 2]]))))
    code, out = _cli(capsys, "row", "carry", "p6", "a", "r1", "--issues", "2", "--reason", "設計承認ゲートで外した")
    assert code == 0 and out["metrics"]["row"] == "r2"
    _, out = _cli(capsys, "status", "p6")
    assert [r["input"]["issues"] for r in out["items"][0]["rows"]] == [[1, 2], [2]]


def test_cli_stop_lists_the_cancel_for_running_sprints(repo, capsys, tmp_path):
    _cli(capsys, "start", _plan_file(tmp_path / "plan.json", _doc(_decl("a", [[1]]))))
    code, out = _cli(capsys, "stop", "p6", "--reason", "放棄")
    assert code == 0 and out["metrics"]["running"] == [] and out["items"] == []
    _, out = _cli(capsys, "status", "p6")
    assert out["metrics"]["state"] == "cancelled" and out["items"][0]["state"] == "cancelled"


def test_cli_refuses_a_plan_file_of_the_wrong_shape(repo, capsys, tmp_path):
    path = _plan_file(tmp_path / "plan.json", {"version": 1, "name": "P 6", "rules": [], "lanes": []})
    code, out = _cli(capsys, "init", path)
    assert out["cause"]["kind"] == "invalid" and code == causes.exit_code("invalid")


def test_cli_status_of_an_unknown_plan_is_refused(repo, capsys):
    code, out = _cli(capsys, "status", "nothing")
    assert out["cause"]["kind"] == "refused" and code != 0
