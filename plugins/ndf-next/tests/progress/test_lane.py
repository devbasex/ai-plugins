"""レーンの進行表（#3097 の受け入れ条件 1・2・3・5、P3-1、3346-5）。

DB を使うテストは `opened`（SQLite と PostgreSQL の両方）で流す。計画は `plan.create_plan` で作る。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ndf.progress import lane, model, plan, store

REPO = "https://example.invalid/o/r"


def _decl(name, rows, touches=("src/a",), after=(), plans=1):
    return {
        "name": name,
        "plans": plans,
        "touches": list(touches),
        "after": list(after),
        "rows": [{"kind": "sprint", "issues": list(i)} for i in rows],
    }


def _doc(*lanes, **extra):
    return {"version": 1, "name": "p6", "rules": [], "lanes": list(lanes), **extra}


def _create(st, doc, start=True):
    with store.transaction(st) as conn:
        return plan.create_plan(conn, REPO, doc, start)


def _rows(st, pid):
    with store.transaction(st) as conn:
        view, _ = lane.read_plan(conn, pid)
        return view, lane.folded(lane.read_lanes(conn, view))


def _sprint(st, pid, lane_name, name):
    """レーンの進行表を親に持つスプリントの進行表（工程の行は持たない）。"""
    with store.transaction(st) as conn:
        view, _ = lane.read_plan(conn, pid)
        return store.create_table(conn, ("sprint", REPO, name), view.lanes[lane_name].table_id, None, [])


def _start(st, pid, lane_name, key, name):
    sid = _sprint(st, pid, lane_name, name)
    with store.transaction(st) as conn:
        warnings = lane.start_row(conn, pid, lane_name, key, sid)
    return sid, warnings


def _refused(st, fn):
    with pytest.raises(model.Refused) as got, store.transaction(st) as conn:
        fn(conn)
    return got.value.reason


# --- 作成と計画の決まり --------------------------------------------------------------------------


def test_plan_and_lane_tables_hold_lanes_and_the_first_order(opened):
    pid = _create(opened, _doc(_decl("a", [[1], [2]]), _decl("b", [[3]], touches=("src/b",), after=("a/r1",))))
    view, rows = _rows(opened, pid)
    assert view.state == "running" and list(view.lanes) == ["a", "b"]
    assert [(r.key, r.state, sorted(r.issues())) for r in rows["a"]] == [("r1", "pending", [1]), ("r2", "pending", [2])]
    assert view.lanes["b"].after == ("a/r1",)
    assert lane.wakeable_lanes(view, rows) == [("a", "r1")]


@pytest.mark.parametrize(
    ("doc", "rule"),
    [
        (_doc(_decl("a", [[1]]), _decl("b", [[1]], touches=("src/b",))), "I19"),
        (_doc(_decl("a", [[1]]), _decl("b", [[2]], touches=("src/b",)), max_lanes=1), "I20"),
        (_doc(_decl("a", [[1]], after=("b",)), _decl("b", [[2]], touches=("src/b",), after=("a",))), "I21"),
        (_doc(_decl("a", [[1]], plans=3), max_plans=2), "I2"),
        # b は a の r1 の後に動くが、a の r2 と同時に動き得て、触る場所が重なる
        (_doc(_decl("a", [[1], [2]]), _decl("b", [[3]], after=("a/r1",))), "I13"),
    ],
)
def test_creation_that_breaks_a_cross_lane_rule_leaves_nothing(opened, doc, rule):
    reason = _refused(opened, lambda conn: plan.create_plan(conn, REPO, doc, True))
    assert rule in reason
    with store.transaction(opened) as conn:
        assert store.read_table(conn, plan.plan_id(REPO, "p6")) is None


def test_rows_ordered_by_after_may_touch_the_same_place(opened):
    # b は a の全行の後に動くので、同じ場所を触ってよい（I13 は同時に動き得る組だけを見る）
    pid = _create(opened, _doc(_decl("a", [[1], [2]]), _decl("b", [[3]], after=("a",))))
    view, rows = _rows(opened, pid)
    assert lane.plan_errors(view, rows) == []


# --- スプリントの開始と終わり（受け入れ条件 2） ---------------------------------------------------


def test_sprint_end_completes_the_row_and_returns_the_next_row_of_the_same_lane(opened):
    pid = _create(opened, _doc(_decl("a", [[1], [2]]), _decl("b", [[3]], touches=("src/b",), after=("a/r1",))))
    sid, _ = _start(opened, pid, "a", "r1", "s1")
    view, rows = _rows(opened, pid)
    assert rows["a"][0].state == "running" and rows["a"][0].sprint_id == sid
    assert lane.wakeable_lanes(view, rows) == []

    with store.transaction(opened) as conn:
        nxt = lane.end_row(conn, sid, "completed", "workflow")
    assert nxt == {"lane": "a", "row": "r2", "input": {"kind": "sprint", "issues": [2]}, "waiting_after": []}
    view, rows = _rows(opened, pid)
    assert rows["a"][0].state == "completed"
    assert lane.wakeable_lanes(view, rows) == [("a", "r2"), ("b", "r1")]

    # 耐久ステップの流し直しで 2 度呼ばれても、row_ended は 1 度だけ残り、同じ結果を返す
    with store.transaction(opened) as conn:
        again = lane.end_row(conn, sid, "completed", "workflow")
        ended = [e for e in store.read_events(conn, view.lanes["a"].table_id) if e.draft.kind == "row_ended"]
    assert again == nxt and len(ended) == 1


def test_sprint_outside_a_lane_has_no_row_to_end(opened):
    with store.transaction(opened) as conn:
        sid = store.create_table(conn, ("sprint", REPO, "alone"), None, None, [])
        assert lane.end_row(conn, sid, "completed", "workflow") is None


def test_only_the_next_row_with_its_after_met_can_start(opened):
    pid = _create(opened, _doc(_decl("a", [[1], [2]]), _decl("b", [[3]], touches=("src/b",), after=("a/r1",))))
    sid = _sprint(opened, pid, "a", "s2")
    assert "次に始める行でない" in _refused(opened, lambda conn: lane.start_row(conn, pid, "a", "r2", sid))
    assert "after" in _refused(opened, lambda conn: lane.start_row(conn, pid, "b", "r1", sid))


def test_rows_cannot_start_before_the_plan_starts(opened):
    pid = _create(opened, _doc(_decl("a", [[1]])), start=False)
    sid = _sprint(opened, pid, "a", "s1")
    assert "未着手" in _refused(opened, lambda conn: lane.start_row(conn, pid, "a", "r1", sid))


# --- レーンの中の組み換え（受け入れ条件 1・3） --------------------------------------------------------


def test_dropped_issues_go_to_the_next_row_of_the_same_lane(opened):
    pid = _create(opened, _doc(_decl("a", [[10, 11], [12]])))
    _start(opened, pid, "a", "r1", "s1")
    with store.transaction(opened) as conn:
        key = lane.carry_issues(conn, pid, "a", "r1", [11], "設計承認ゲートで外した")
    view, rows = _rows(opened, pid)
    assert [r.key for r in rows["a"]] == ["r1", key, "r2"]
    assert rows["a"][1].input["issues"] == [11] and rows["a"][1].state == "pending"
    assert list(view.lanes) == ["a"]  # after 付きの新しいレーンを作らない


def test_rows_are_not_placed_before_a_running_row(opened):
    pid = _create(opened, _doc(_decl("a", [[1], [2]])))
    _start(opened, pid, "a", "r1", "s1")
    change = lane.RowChange("conductor", "前へ", 0)
    reason = _refused(opened, lambda conn: lane.add_row(conn, pid, "a", {"kind": "sprint", "issues": [5]}, change))
    assert "I12a" in reason
    reason = _refused(opened, lambda conn: lane.move_row(conn, pid, "a", "r2", change))
    assert "I12a" in reason


def test_running_rows_cannot_be_cancelled_by_the_order(opened):
    pid = _create(opened, _doc(_decl("a", [[1]])))
    _start(opened, pid, "a", "r1", "s1")
    reason = _refused(opened, lambda conn: lane.cancel_row(conn, pid, "a", "r1", lane.RowChange("conductor", "やめる")))
    assert "実行中" in reason


def test_a_row_another_lane_waits_for_is_kept_in_place(opened):
    pid = _create(opened, _doc(_decl("a", [[1], [2], [3]]), _decl("b", [[4]], touches=("src/b",), after=("a/r2",))))
    change = lane.RowChange("conductor", "後ろへ", 2)
    assert "I18" in _refused(opened, lambda conn: lane.move_row(conn, pid, "a", "r2", change))
    assert "I18" in _refused(opened, lambda conn: lane.cancel_row(conn, pid, "a", "r2", lane.RowChange("conductor", "やめる")))
    with store.transaction(opened) as conn:
        lane.cancel_row(conn, pid, "a", "r3", lane.RowChange("conductor", "要らなくなった"))
    _, rows = _rows(opened, pid)
    assert [r.state for r in rows["a"]] == ["pending", "pending", "cancelled"]


def test_adding_an_issue_that_another_lane_has_is_refused(opened):
    pid = _create(opened, _doc(_decl("a", [[1]]), _decl("b", [[2]], touches=("src/b",))))
    inp = {"kind": "sprint", "issues": [2]}
    reason = _refused(opened, lambda conn: lane.add_row(conn, pid, "a", inp, lane.RowChange("conductor", "足す")))
    assert "I19" in reason


# --- 書き手（受け入れ条件 5） ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("level", "kind", "actor"),
    [
        ("plan", "lane_added", "conductor"),
        ("plan", "plan_cancelled", "durable"),
        ("plan", "plan_completed", "organizer"),
        ("lane", "row_started", "organizer"),
        ("lane", "row_answered", "conductor"),
        ("lane", "row_ended", "organizer"),
    ],
)
def test_each_table_is_written_only_by_its_actors(level, kind, actor):
    with pytest.raises(model.Refused):
        model.check_writer(level, kind, actor)


# --- 形の番号（P3-1） ------------------------------------------------------------------------------


def test_lane_start_stops_when_the_recorded_format_differs(opened, monkeypatch):
    pid = _create(opened, _doc(_decl("a", [[1]])))
    sid = _sprint(opened, pid, "a", "s1")
    mine = lane.current_formats()
    monkeypatch.setattr(lane, "current_formats", lambda: {**mine, "format": mine["format"] + 1})
    with pytest.raises(lane.FormatMismatch), store.transaction(opened) as conn:
        lane.start_row(conn, pid, "a", "r1", sid)
    _, rows = _rows(opened, pid)
    assert rows["a"][0].state == "pending"


def test_only_the_ndf_version_differs_so_the_lane_starts_with_a_warning(opened, monkeypatch):
    pid = _create(opened, _doc(_decl("a", [[1]])))
    monkeypatch.setattr(lane, "ndf_version", lambda: "0.0.0-other")
    sid, warnings = _start(opened, pid, "a", "r1", "s1")
    assert len(warnings) == 1 and "0.0.0-other" in warnings[0]
    with store.transaction(opened) as conn:
        view, _ = lane.read_plan(conn, pid)
        started = [e for e in store.read_events(conn, view.lanes["a"].table_id) if e.draft.kind == "row_started"]
    assert started[0].draft.payload["ndf_version_mismatch"]["self"] == "0.0.0-other"
    assert started[0].draft.payload["sprint_id"] == sid


# --- 答えの後の next（3346-5） -----------------------------------------------------------------------


def test_answer_without_a_following_next_is_read_as_a_stall(opened):
    pid = _create(opened, _doc(_decl("a", [[1]])))
    sid, _ = _start(opened, pid, "a", "r1", "s1")
    with store.transaction(opened) as conn:
        lane.record_answer(conn, sid, {"answer": "approved"})
    _, rows = _rows(opened, pid)
    assert rows["a"][0].awaiting_next

    later = datetime.now(UTC) + timedelta(seconds=plan.ANSWER_NEXT_GRACE_SECONDS + 60)
    with store.transaction(opened) as conn:
        out = plan.status(conn, pid, later)
    waiting = out["items"][0]["rows"][0]["awaiting_next"]
    assert waiting["stalled"] and waiting["actor"] == "conductor"
    assert waiting["next"]["argv"][1:] == ["progress", "next", sid]
    assert out["metrics"]["stalled"] == 1 and len(out["warnings"]) == 1

    with store.transaction(opened) as conn:
        lane.record_resumed(conn, sid, "conductor")
        out = plan.status(conn, pid, later)
    assert "awaiting_next" not in out["items"][0]["rows"][0] and "warnings" not in out


def test_answer_just_recorded_is_not_yet_a_stall(opened):
    pid = _create(opened, _doc(_decl("a", [[1]])))
    sid, _ = _start(opened, pid, "a", "r1", "s1")
    with store.transaction(opened) as conn:
        lane.record_answer(conn, sid, {"answer": "approved"})
        out = plan.status(conn, pid, datetime.now(UTC))
    assert out["items"][0]["rows"][0]["awaiting_next"]["stalled"] is False and "warnings" not in out


# --- 純粋な判定 ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("x", "y", "hit"),
    [("src/a", "src/a/b.py", True), ("src/a/", "src/a", True), ("src/*.py", "src/x.py", True), ("src/a", "src/ab", False)],
)
def test_touch_hit(x, y, hit):
    assert lane.touch_hit(x, y) is hit
