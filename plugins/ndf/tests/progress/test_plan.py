"""計画のステートマシンとサブコマンド `ndf lanes`（#3097 の受け入れ条件 1・4・5）。"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from ndf import cli, contracts, identity
from ndf.adapters.git import GitError
from ndf.contracts import causes
from ndf.core import durable
from ndf.progress import command, definition, lane, model, plan, store
from ndf.progress.lane import fold
from ndf.progress.lane import launch
from ndf.progress.plan import cli as plan_cli
from ndf.progress.plan import run as plan_run
from plan_decls import REPO, doc
from plan_decls import decl as _decl
from result_json import last_result


def _doc(*lanes, **extra):
    return doc(*lanes, rules=["develop へ出す"], **extra)


def _two(st):
    with store.transaction(st) as conn:
        return plan.create_plan(conn, REPO, _doc(_decl("a", [[1], [2]]), _decl("b", [[3]], touches=("src/b",))), True)


def _view(st, pid):
    with store.transaction(st) as conn:
        return lane.read_view(conn, pid)


def _run_sprint(st, pid, lane_name, key, name, outcome="completed"):
    with store.transaction(st) as conn:
        view, _ = lane.read_plan(conn, pid)
        sid = store.create_machine(conn, ("sprint", REPO, name), view.lanes[lane_name].machine_id, None, [])
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


QUESTION = {"tool_use_id": "toolu_1", "session_id": "s1", "questions": [{"question": "設計を承認するか"}], "cwd": "/w", "at": "t"}


def test_lane_question_opens_one_request_until_the_organizer_closes_it(opened, refused):
    # レーンの質問は計画のリクエストになり、同じ質問は閉じるまで二重に載らない（#3035 の受け入れ条件 3）
    pid = _two(opened)
    wrapper = launch.wrapper_name(REPO, "p6", "b")
    with store.transaction(opened) as conn:
        key, new = plan.ask_request(conn, pid, wrapper, QUESTION)
        again, again_new = plan.ask_request(conn, pid, wrapper, QUESTION)
        other, other_new = plan.ask_request(conn, pid, wrapper, {**QUESTION, "tool_use_id": "toolu_2"})
    assert (new, again, again_new, other_new) == (True, key, False, True) and other != key
    view, _ = _view(opened, pid)
    body = view.requests[key].body
    assert view.requests[key].lane == "b" and body["kind"] == plan.QUESTION_KIND and body["questions"] == QUESTION["questions"]
    with store.transaction(opened) as conn:
        plan.close_request(conn, pid, key, "承認する")
        reopened, reopened_new = plan.ask_request(conn, pid, wrapper, QUESTION)
    assert reopened_new is True and reopened not in (key, other)
    reason = refused(opened, lambda conn: plan.ask_request(conn, pid, launch.wrapper_name(REPO, "p7", "b"), QUESTION))
    assert "レーンのものでない" in reason


def test_lane_that_breaks_a_rule_is_not_added(opened, refused):
    pid = _two(opened)
    assert "I19" in refused(opened, lambda conn: plan.add_lane(conn, pid, _decl("c", [[3]], touches=("docs/",))))
    view, _ = _view(opened, pid)
    assert list(view.lanes) == ["a", "b"]


def test_changed_lane_is_checked_against_the_other_lanes(opened, refused):
    pid = _two(opened)
    reason = refused(opened, lambda conn: plan.change_lane(conn, pid, "b", {"touches": ["src/a/x.py"]}, "広げる"))
    assert "I13" in reason
    with store.transaction(opened) as conn:
        plan.change_lane(conn, pid, "b", {"touches": ["src/a/x.py"], "after": ["a"]}, "a の後に回す")
    view, _ = _view(opened, pid)
    assert view.lanes["b"].touches == ("src/a/x.py",) and view.lanes["b"].after == ("a",)


def test_recorded_limits_of_older_versions_are_read_as_no_change(opened):
    # #3989 の受け入れ条件 3: 11.0.13 までの記録（作成・追加・変更・上限の変更の事象の max_lanes・max_plans・plans）を読める
    pid = _two(opened)
    before = _view(opened, pid)
    with store.transaction(opened) as conn:
        events = store.read_events(conn, pid)
        created = events[0].draft.payload
        old = [
            model.EventDraft("lane_changed", "organizer", lane.PLAN_ROW, reason="上限", payload={"max_lanes": 1, "max_plans": 2}),
            model.EventDraft("lane_changed", "organizer", "b", reason="本数", payload={"plans": 3}),
        ]
        plan._append(conn, pid, old)
    view, rows = _view(opened, pid)
    assert (view, rows) == before
    legacy = {**created, "max_lanes": 6, "max_plans": 6}
    assert fold._plan_base(pid, legacy, "running").name == view.name
    spec = fold._spec("c", {"machine_id": "t-c", "kind": "sprint", "plans": 2, "touches": ["src/c"], "after": []})
    assert spec.touches == ("src/c",) and not hasattr(spec, "plans")


@pytest.mark.parametrize("key", ["max_lanes", "max_plans", "plans"])
def test_cli_refuses_a_plan_file_that_names_a_removed_limit(repo, capsys, tmp_path, key):
    # #3989 の受け入れ条件 2: 3 つの値のどれかを書いた計画の設定を init・start が名指して断る
    one = _decl("a", [[1]])
    body = _doc({**one, "plans": 1}) if key == "plans" else _doc(one, **{key: 6})
    path = _plan_file(tmp_path / "plan.json", body)
    for cmd in ("init", "start"):
        code, out = _cli(capsys, cmd, path)
        assert out["cause"]["kind"] == "invalid" and code == causes.exit_code("invalid") and key in out["summary"]
    lane_path = _plan_file(tmp_path / "lane.json", {**_decl("b", [[2]], touches=("src/b",)), "plans": 1})
    _cli(capsys, "start", _plan_file(tmp_path / "ok.json", _doc(one)))
    code, out = _cli(capsys, "lane", "add", "p6", lane_path)
    assert out["cause"]["kind"] == "invalid" and "plans" in out["summary"]


def test_row_moved_to_another_lane(opened):
    pid = _two(opened)
    with store.transaction(opened) as conn:
        key = plan.move_row(conn, pid, ("a", "r2"), "b", "b で流す")
    _, rows = _view(opened, pid)
    assert [r.state for r in rows["a"]] == ["pending", "cancelled"]
    assert [(r.key, r.input["issues"]) for r in rows["b"]] == [("r1", [3]), (key, [2])]


# --- 中止と完了 ------------------------------------------------------------------------------------


def test_stop_cancels_pending_rows_and_hands_running_sprints_to_their_conductors(opened, refused):
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
    assert "中止" in refused(opened, lambda conn: plan.add_lane(conn, pid, _decl("c", [[9]], touches=("docs/",))))


def test_pause_keeps_the_rows_and_start_resumes_the_same_plan_from_where_it_stopped(opened, refused):
    """区切りで止めた計画は行を中止にせず、次の行を始めない。同じ名前の計画を始め直すと止めた所から続く。"""
    pid = _two(opened)
    sid = _run_sprint(opened, pid, "a", "r1", "s-a1", outcome=None)
    with store.transaction(opened) as conn:
        assert plan.pause_plan(conn, pid, "区切りで止める") == [sid]
    view, rows = _view(opened, pid)
    assert view.state == "paused" and [r.state for r in rows["a"]] == ["running", "pending"]
    assert lane.wakeable_lanes(view, rows) == []
    # 実行中の行は終わりまで流れる。次の行は止めている間は始められない
    with store.transaction(opened) as conn:
        lane.end_row(conn, sid, "completed", "conductor")
    assert "停止" in refused(opened, lambda conn: lane.start_row(conn, pid, "b", "r1", "s-b1"))
    assert "停止" in refused(opened, lambda conn: plan.pause_plan(conn, pid, "二度目"))
    with store.transaction(opened) as conn:
        plan.start_plan(conn, pid)
    view, rows = _view(opened, pid)
    assert view.state == "running" and lane.wakeable_lanes(view, rows) == [("a", "r2"), ("b", "r1")]


def test_a_paused_plan_can_still_be_stopped_now(opened, refused):
    pid = _two(opened)
    with store.transaction(opened) as conn:
        plan.pause_plan(conn, pid, "区切りで止める")
        plan.stop_plan(conn, pid, "放棄する")
    view, rows = _view(opened, pid)
    assert view.state == "cancelled" and rows["b"][0].state == "cancelled"
    assert "中止" in refused(opened, lambda conn: plan.start_plan(conn, pid))


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


def test_a_batched_release_closes_every_request_and_keeps_its_answer(opened):
    """まとめたリリースの回答は、まとめたリクエストをすべて閉じ、各リクエストに回答（バージョン・リリース記録の PR）を残す（#2783・#3128・#3136）。"""
    pid = _two(opened)
    with store.transaction(opened) as conn:
        ra = plan.open_request(conn, pid, {"lane": "a", "kind": "release"}, "リリースしたい", "workflow")
        rb = plan.open_request(conn, pid, {"lane": "b", "kind": "release"}, "リリースしたい", "workflow")
        plan.record_release(conn, pid, [ra, rb], plan.ReleaseAnswer(plan.REJECTED, "範囲を直してから"))
        rc = plan.open_request(conn, pid, {"lane": "a", "kind": "release"}, "出し直す", "workflow")
        plan.record_release(conn, pid, [rc], plan.ReleaseAnswer(plan.RELEASED, "出した", "10.28.0", "https://github.com/o/r/pull/900"))
        out = plan.status(conn, pid, datetime.now(UTC))
    view, _ = _view(opened, pid)
    assert view.open_requests() == []
    assert view.requests[ra].release == {"result": "rejected", "version": None, "release": None, "batch": [ra, rb]}
    assert view.requests[rc].release["release"] == "https://github.com/o/r/pull/900"
    assert out["metrics"]["releases"] == {"released": 1, "rejected": 2}


def test_a_lane_reads_the_answer_of_its_closed_request_from_status(opened):
    """閉じたリクエストの回答（バージョン・リリース記録の PR）を、レーンが status の closed_requests から読める（#3128）。"""
    pid = _two(opened)
    with store.transaction(opened) as conn:
        ra = plan.open_request(conn, pid, {"lane": "a", "kind": "release"}, "リリースしたい", "workflow")
        rb = plan.open_request(conn, pid, {"lane": "b", "kind": "release"}, "リリースしたい", "workflow")
        record = "https://github.com/o/r/pull/900"
        plan.record_release(conn, pid, [ra], plan.ReleaseAnswer(plan.RELEASED, "出した", "10.28.0", record))
        out = plan.status(conn, pid, datetime.now(UTC))
    assert out["metrics"]["open_requests"] == [{"request": rb, "lane": "b", "reason": "リリースしたい"}]
    (closed,) = out["metrics"]["closed_requests"]
    assert closed["request"] == ra and closed["lane"] == "a" and closed["closed"] == "出した"
    assert closed["release"] == {"result": "released", "version": "10.28.0", "release": record, "batch": [ra]}


@pytest.mark.parametrize(
    ("keys", "outcome", "version", "release"),
    [
        ([], "rejected", None, None),
        (["request-1", "request-1"], "rejected", None, None),
        (["request-1"], "failed", None, None),
        (["request-1"], "released", None, None),
        (["request-9"], "rejected", None, None),
    ],
)
def test_a_batched_release_refuses_a_bad_answer(opened, keys, outcome, version, release):
    pid = _two(opened)
    with store.transaction(opened) as conn:
        plan.open_request(conn, pid, {"lane": "a", "kind": "release"}, "リリースしたい", "workflow")
    with store.transaction(opened) as conn, pytest.raises(model.Refused):
        plan.record_release(conn, pid, keys, plan.ReleaseAnswer(outcome, "理由", version, release))


def test_status_is_built_from_the_two_tables(opened):
    pid = _two(opened)
    _run_sprint(opened, pid, "a", "r1", "s1")
    with store.transaction(opened) as conn:
        out = plan.status(conn, pid, datetime.now(UTC))
    assert out["status"] == "ok" and out["metrics"]["state"] == "running"
    assert [(i["lane"], i["state"]) for i in out["items"]] == [("a", "pending"), ("b", "pending")]
    assert [r["label"] for r in out["items"][0]["rows"]] == ["完了", "未着手"]
    assert out["metrics"]["wakeable"] == [["a", "r2"], ["b", "r1"]] and out["metrics"]["complete"] is False


# --- サブコマンド ----------------------------------------------------------------------------------------


@pytest.fixture
def detached(monkeypatch):
    """`start` が切り離して起動する `lanes run` の呼び出し（起動せずに記録する）。"""
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
    return code, last_result(capsys.readouterr().out)


def _plan_file(path: Path, doc: dict) -> str:
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def test_subcommand_is_in_the_table():
    assert cli.COMMANDS[plan.TOOL] == plan_cli.__name__


def test_cli_refuses_a_plan_file_of_the_wrong_shape(repo, capsys, tmp_path):
    path = _plan_file(tmp_path / "plan.json", {"version": 1, "name": "P 6", "rules": [], "lanes": []})
    code, out = _cli(capsys, "init", path)
    assert out["cause"]["kind"] == "invalid" and code == causes.exit_code("invalid")


def test_cli_status_of_an_unknown_plan_is_refused(repo, capsys):
    code, out = _cli(capsys, "status", "nothing")
    assert out["cause"]["kind"] == "refused" and code != 0


# --- 現状固定（I-005: 停止の変換 `_stopped` を `ndf progress` の側と一つにする前） -------------------------------

_CPE = subprocess.CalledProcessError(128, ["git", "rev-parse"])
_GIT_OUTSIDE = f"git の中で打つ（チェックアウトの保存先を求められない）: {_CPE}"


class _ReasonedStop(durable.StoreStopped):
    reason = "locked"


@pytest.mark.parametrize(
    ("exc", "kind", "detail", "items", "metrics"),
    [
        (model.Refused("断った"), "refused", "断った", [], {}),
        (model.Refused("既にある", current_id="m-1"), "refused", "既にある", [], {"current_id": "m-1"}),
        (lane.FormatMismatch("形が違う"), "format_mismatch", "形が違う", [], {}),
        (
            durable.StoreStopped("保存先が壊れた", (Path("/s/a"), Path("/s/b"))),
            "store_broken",
            "保存先が壊れた",
            [{"path": "/s/a"}, {"path": "/s/b"}],
            {},
        ),
        (_ReasonedStop("使えない", (Path("/s/a"),)), "store_broken", "使えない", [{"path": "/s/a", "reason": "locked"}], {}),
        (store.StoreUnreachable("繋げない"), "store_unreachable", "繋げない", [], {}),
        # 本文（SQL など）は出さず、種類の名前だけを出す
        (
            sa.exc.OperationalError("SELECT secret", {}, Exception("x")),
            "store_unreachable",
            "ステートマシンの DB を使えない: OperationalError",
            [],
            {},
        ),
        # NOTE: 現状固定。IdentityError の kind（ここでは legacy_running）を見ずに config_broken にする（`ndf progress` も同じ）
        (identity.IdentityError("名前が無い", kind="legacy_running"), "config_broken", "名前が無い", [], {}),
        (_CPE, "precondition", _GIT_OUTSIDE, [], {}),
        (contracts.ContractError("形が違う"), "invalid", "形が違う", [], {}),
        # NOTE: 現状固定。`ndf progress` は errors を items に載せるが、`ndf lanes` は載せない
        (definition.WorkflowDefError(["a", "b"]), "invalid", "工程の定義が検査を通らない: a; b", [], {}),
        (GitError("git が失敗"), "invalid", "git が失敗", [], {}),
    ],
)
@pytest.mark.parametrize("argv", [("status", "p6"), ("stop", "p6", "--reason", "r")])
def test_cli_turns_each_stoppable_error_into_a_stopped_result(monkeypatch, capsys, argv, exc, kind, detail, items, metrics):
    def boom(cwd):
        raise exc

    monkeypatch.setattr(command, "store_root_of", boom)
    code, out = _cli(capsys, *argv)
    assert code == causes.exit_code(kind)
    assert out == {
        "tool": plan.TOOL,
        "status": "stopped",
        "summary": detail,
        "items": items,
        "metrics": metrics,
        "cause": {"kind": kind, "detail": detail},
    }


def test_cli_lets_an_error_outside_the_stoppable_kinds_through(monkeypatch, capsys):
    def boom(cwd):
        raise ValueError("想定の外")

    monkeypatch.setattr(command, "store_root_of", boom)
    with pytest.raises(ValueError, match="想定の外"):
        plan_cli.main(["status", "p6"])


# --- 現状固定（I-006: 保存先を求める `_where` を `ndf progress` の側と一つにする前） ----------------------------


@pytest.fixture
def advanced(monkeypatch):
    """`lanes run` が耐久ワークフローへ渡す保存先（`run.Where`）を記録し、進めずに断る。"""
    seen = []

    def advance(where, name, policy):
        seen.append(where)
        raise model.Refused("進めない")

    monkeypatch.setattr(plan_run, "advance", advance)
    return seen


def test_cli_run_hands_the_checkout_store_and_the_repo_path(repo, capsys, advanced):
    code, out = _cli(capsys, "run", "p6")
    assert out["cause"]["kind"] == "refused" and code == causes.exit_code("refused")
    root = durable.store_root(identity.load().name, repo)
    assert root.is_dir() and root.parents[2] == repo
    assert advanced == [plan_run.Where(root=root, url=f"{durable.SQLITE_PREFIX}{root / store.DB_NAME}", repo=str(repo), cwd=repo)]


def test_cli_run_names_the_repo_by_its_remote_without_userinfo(repo, capsys, advanced):
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://u:tok@example.com/o/r.git"], check=True)
    _cli(capsys, "run", "p6")
    assert [w.repo for w in advanced] == ["https://example.com/o/r.git"]


def test_cli_run_uses_the_given_progress_db(repo, capsys, advanced, monkeypatch, tmp_path):
    url = f"{durable.SQLITE_PREFIX}{tmp_path / 'other.sqlite'}"
    monkeypatch.setenv("NDF_PROGRESS_DB", url)
    _cli(capsys, "run", "p6")
    assert [(w.url, w.root) for w in advanced] == [(url, durable.store_root(identity.load().name, repo))]


@pytest.mark.parametrize(
    ("url", "detail"),
    [
        ("sqlite://", "ステートマシンの DB の SQLite の URL にファイルのパスが無い: sqlite://"),
        (
            "sqlite:///x.db?timeout=3",
            "ステートマシンの DB の SQLite の URL にクエリは書けない（ファイルのパスだけを書く）: sqlite:///x.db?timeout=3",
        ),
    ],
)
@pytest.mark.parametrize("argv", [("run", "p6"), ("status", "p6"), ("stop", "p6", "--reason", "r")])
def test_cli_refuses_an_unsupported_db_url_before_preparing_the_store(repo, capsys, advanced, monkeypatch, argv, url, detail):
    monkeypatch.setenv("NDF_PROGRESS_DB", url)
    code, out = _cli(capsys, *argv)
    assert code == causes.exit_code("invalid") and out["cause"] == {"kind": "invalid", "detail": detail}
    assert not durable.store_root(identity.load().name, repo).exists() and advanced == []


# --- 現状固定（I-012: `_cmd_row` の振り分けを一つの表にする前） -----------------------------------------------


def _ok_row(out: dict) -> tuple[str, dict]:
    assert out["status"] == "ok" and out["items"] == []
    return out["summary"], out["metrics"]


def _sprint_of_lane(repo: Path, lane_name: str, name: str) -> str:
    """CLI と同じ保存先に、レーンのステートマシンを親に持つスプリントのステートマシンを作る。"""
    root = durable.store_root(identity.load().name, repo)
    rid = command.repo_id(repo, root)
    with store.open_store(store.default_url(root), root) as st, store.transaction(st) as conn:
        view, _ = lane.read_plan(conn, plan.plan_id(rid, "p6"))
        return store.create_machine(conn, ("sprint", rid, name), view.lanes[lane_name].machine_id, None, [])


def test_cli_row_start_resumed_end_by_sprint(repo, capsys, tmp_path):
    _cli(capsys, "start", _plan_file(tmp_path / "plan.json", _doc(_decl("a", [[1], [2]]))))
    sid = _sprint_of_lane(repo, "a", "s1")
    code, out = _cli(capsys, "row", "start", "p6", "a", "r2", "--sprint", sid)
    assert code == causes.exit_code("refused")
    assert out["cause"] == {"kind": "refused", "detail": "行 a/r2 はレーンの次に始める行でない（次の行: r1）"}
    code, out = _cli(capsys, "row", "start", "p6", "a", "r1", "--sprint", sid)
    assert code == 0 and _ok_row(out) == ("レーン a の行 r1 を実行中にした", {"row": "r1", "sprint_id": sid})
    assert "warnings" not in out
    _, out = _cli(capsys, "answered", sid, "--answer", "はい")
    assert out["metrics"] == {"seq": 4}
    code, out = _cli(capsys, "row", "resumed", sid)
    assert code == 0 and _ok_row(out) == (f"スプリント {sid} の回答の後の next を記録した", {"seq": 5})
    code, out = _cli(capsys, "row", "end", sid, "--outcome", "completed")
    next_row = {"lane": "a", "row": "r2", "input": {"kind": "sprint", "issues": [2]}, "waiting_after": []}
    assert code == 0 and _ok_row(out) == (f"スプリント {sid} の行を完了にした", {"next_row": next_row})


def test_cli_row_by_sprint_does_not_read_the_plan(repo, capsys, tmp_path):
    # NOTE: 現状固定。end と resumed は計画を引かず、知らないスプリントでも ok（行が無ければ null）を返す
    _cli(capsys, "start", _plan_file(tmp_path / "plan.json", _doc(_decl("a", [[1]]))))
    code, out = _cli(capsys, "row", "end", "nothing", "--outcome", "completed")
    assert code == 0 and _ok_row(out) == ("スプリント nothing の行を完了にした", {"next_row": None})
    code, out = _cli(capsys, "row", "resumed", "nothing")
    assert code == 0 and _ok_row(out) == ("スプリント nothing の回答の後の next を記録した", {"seq": None})


@pytest.mark.parametrize(
    "argv",
    [
        ("add", "nothing", "a", "--issues", "1", "--reason", "x"),
        ("move", "nothing", "a", "r1", "--index", "0", "--reason", "x"),
        ("cancel", "nothing", "a", "r1", "--reason", "x"),
        ("carry", "nothing", "a", "r1", "--issues", "1", "--reason", "x"),
        ("start", "nothing", "a", "r1", "--sprint", "s"),
    ],
)
def test_cli_row_by_plan_refuses_an_unknown_plan(repo, capsys, argv):
    code, out = _cli(capsys, "row", *argv)
    assert code == causes.exit_code("refused") and out["cause"]["kind"] == "refused"
    assert out["cause"]["detail"].startswith("計画のステートマシン ") and out["cause"]["detail"].endswith(":nothing は無い")
