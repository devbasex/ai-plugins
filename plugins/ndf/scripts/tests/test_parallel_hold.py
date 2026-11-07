"""答え待ちの停止の数え方・閾値・置き場（#2707 の受け入れ条件 1〜10・18・19・27・28、I1〜I3・I10）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

import pace  # noqa: E402
import parallel_hold as ph  # noqa: E402

LIMITS = ph.Limits(8, 3)
ORDER = ["la", "lb", "lc"]


def ask(i, lane="la", **extra):
    return {"id": f"q{i}", "kind": "ask", "lane": lane, **extra}


def plan_file(main: Path, kind="sprint", name="p1", lane="la"):
    d = main / ".ndf" / "parallel" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.json").write_text(json.dumps({"lanes": [{"name": lane, "input": {"kind": kind}}]}))
    return d


def own(i):
    return {"id": f"own{i}", "kind": "stall", "lane": "la"}


@pytest.mark.parametrize("n, stopped", [(8, False), (9, True)], ids=["閾値ちょうどでは止めない", "超えたら計画全体を止める"])
def test_the_plan_limit_stops_only_when_exceeded(n, stopped):
    got = ph.decide([own(i) for i in range(n)], ORDER, LIMITS)
    assert got.plan is stopped and got.count == n


def test_only_unanswered_requests_count():
    items = [own(i) for i in range(9)]
    assert ph.decide(items, ORDER, LIMITS).plan
    assert ph.decide(items[2:], ORDER, LIMITS).count == 7


def test_every_kind_in_the_list_counts_and_a_decided_request_is_counted_until_answered():
    items = [
        {"id": "a", "kind": "gate1", "lane": "la", "pr": 1, "verdict": "unknown"},
        {"id": "b", "kind": "gate2", "lane": "p1-rel1", "n": 1},
        ask(1),
        {"id": "c", "kind": "stall", "lane": "lb"},
        {"id": "d", "kind": "helper", "lane": "p1-rel1"},
        {"id": "e", "kind": "spawn", "lane": "lc"},
        {"id": "f", "kind": "observe"},
        {"id": "g", "kind": "build"},
    ]
    assert ph.decide(items, ORDER, LIMITS).count == 8


def test_a_merged_question_and_new_and_old_requests_of_one_design_count_once():
    merged = ask(1, merged=[{"id": "q2", "lane": "lb"}], also=["q2"])
    old, new = ({"id": i, "kind": "gate1", "lane": "la", "pr": 7} for i in ("old", "new"))
    other = {"id": "x", "kind": "gate1", "lane": "lb", "pr": 7}
    assert ph.decide([merged, old, new, other], ORDER, LIMITS).count == 3


def test_the_lane_limit_stops_only_that_lane():
    three = [ask(i) for i in range(3)]
    assert ph.decide(three, ORDER, LIMITS).empty
    got = ph.decide([*three, ask(4), ask(5, "lb")], ORDER, LIMITS)
    assert got.lanes == ("la",) and not got.plan and got.covers("la") and not got.covers("lb")


def test_a_merged_question_counts_for_every_sender_and_own_questions_for_no_lane():
    merged = [ask(i, merged=[{"id": f"m{i}", "lane": "lb"}]) for i in range(2)]
    design = {"id": "d", "kind": "gate1", "lane": "la", "pr": 3}
    helper = ask(9, "p1-rel1")
    got = ph.decide([*merged, design, own(1), helper, {"id": "g2", "kind": "gate2", "lane": "la"}], ORDER, LIMITS)
    assert got.by_lane == {"la": 3, "lb": 2}


def test_limits_default_without_a_declaration(tmp_path):
    assert ph.read_limits(tmp_path) == ph.Limits(8, 3)
    assert ph.limit_items(tmp_path) == []


def declare(root: Path, text):
    (root / ".ndf").mkdir(exist_ok=True)
    (root / ".ndf" / "pace.json").write_text(text if isinstance(text, str) else json.dumps(text))


def test_declared_limits_are_used(tmp_path):
    declare(tmp_path, {"parallel": {"answer_limit": {"plan": 5, "lane": 1}}})
    got = ph.read_limits(tmp_path)
    assert (got.plan, got.lane, got.warnings) == (5, 1, ())


@pytest.mark.parametrize(
    "decl, want",
    [
        ({"parallel": {"answer_limit": {"plan": 0, "lane": 2}}}, (8, 2)),
        ({"parallel": {"answer_limit": {"plan": "9", "lane": True}}}, (8, 3)),
        ({"parallel": {"answer_limit": 4}}, (8, 3)),
        ({"parallel": "x"}, (8, 3)),
        ("{壊れた", (8, 3)),
    ],
    ids=["0", "文字列と真偽", "節がオブジェクトでない", "親の節がオブジェクトでない", "読めない宣言"],
)
def test_a_bad_value_falls_back_with_a_warning(tmp_path, decl, want):
    declare(tmp_path, decl)
    got = ph.read_limits(tmp_path)
    assert (got.plan, got.lane) == want and got.warnings
    assert ph.limit_items(tmp_path)[0]["level"] == "warning"


def test_the_declaration_of_the_budget_is_not_touched_by_a_bad_parallel_section(tmp_path):
    declare(tmp_path, {"parallel": 1, "loop": {"enabled": True}})
    assert pace.read_loop(tmp_path)["enabled"] is True


def write_change(main, before, now):
    return ph.write_state(main, "p1", now.data(LIMITS), now.diff(before, LIMITS))


def test_on_and_off_write_the_file_and_the_history(tmp_path):
    plan_file(tmp_path)
    on = ph.decide([own(i) for i in range(9)], ORDER, LIMITS)
    got = write_change(tmp_path, ph.Hold(), on)
    assert got["n"] == 1 and "計画全体" in got["line"] and "9 件" in got["line"]
    assert ph.read_hold(tmp_path, "p1")["plan"] is True
    off = ph.decide([], ORDER, LIMITS)
    assert write_change(tmp_path, on, off)["n"] == 2
    assert ph.read_hold(tmp_path, "p1") is None
    assert [(r["n"], r["change"], r["scope"]) for r in ph.read_log(tmp_path, "p1")] == [(1, "on", "plan"), (2, "off", "plan")]


def test_each_lane_is_one_row_with_its_own_count(tmp_path):
    now = ph.decide([*[ask(i) for i in range(4)], *[ask(i + 10, "lb") for i in range(5)]], ORDER, ph.Limits(20, 3))
    rows = now.diff(ph.Hold(), LIMITS)
    assert [(r["lanes"], r["count"]) for r in rows] == [(["la"], 4), (["lb"], 5)]


def test_wait_changes_return_all_rows_after_the_given_number(tmp_path):
    plan_file(tmp_path)
    on, off = ph.decide([own(i) for i in range(9)], ORDER, LIMITS), ph.Hold()
    for i in range(6):
        before, after = (off, on) if i % 2 == 0 else (on, off)
        write_change(tmp_path, before, after)
    got = ph.wait_changes(tmp_path, "p1", 1, {"n": 6, "line": "最新"})
    assert [c["n"] for c in got["changes"]] == [2, 3, 4, 5, 6] and all(c["line"] for c in got["changes"])
    assert "changes_unread" not in got


def test_wait_changes_fall_back_to_the_event_when_the_history_is_unreadable(tmp_path):
    plan_file(tmp_path)
    (tmp_path / ".ndf" / "parallel" / "p1" / ph.LOG_FILE).mkdir()
    got = ph.wait_changes(tmp_path, "p1", 0, {"n": 3, "line": "最新"})
    assert got == {"changes": [{"n": 3, "line": "最新"}], "changes_unread": True}


@pytest.mark.parametrize("text", ["{壊れた", "[1]", ""], ids=["JSON でない", "オブジェクトでない", "空"])
def test_an_unreadable_hold_file_means_no_hold(tmp_path, text):
    d = plan_file(tmp_path, "loop")
    (d / ph.HOLD_FILE).write_text(text)
    assert ph.read_hold(tmp_path, "p1") is None
    assert ph.lane_hold(tmp_path, "la", "loop") is None


def test_lane_hold_reads_only_the_lane_of_the_given_kind(tmp_path):
    d = plan_file(tmp_path, "loop")
    (d / ph.HOLD_FILE).write_text(
        json.dumps({"plan": False, "lanes": ["la"], "count": 5, "by_lane": {"la": 4}, "limits": {"plan": 8, "lane": 3}})
    )
    assert ph.lane_hold(tmp_path, "la", "loop") == {"plan": "p1", "scope": "lane", "count": 4, "limit": 3}
    assert ph.lane_hold(tmp_path, "la", "sprint") is None
    assert ph.lane_hold(tmp_path, "zz", "loop") is None


def test_the_plan_scope_wins_over_the_lane_scope(tmp_path):
    d = plan_file(tmp_path, "loop")
    (d / ph.HOLD_FILE).write_text(
        json.dumps({"plan": True, "lanes": ["la"], "count": 9, "by_lane": {"la": 4}, "limits": {"plan": 8, "lane": 3}})
    )
    assert ph.lane_hold(tmp_path, "la", "loop") == {"plan": "p1", "scope": "plan", "count": 9, "limit": 8}


def test_marks_are_placed_read_dropped_and_cleared_with_the_plan(tmp_path):
    d = plan_file(tmp_path)
    (d / ph.HOLD_FILE).write_text("{}")
    first = ph.place_mark(tmp_path, "p1", "la", "boundary")
    second = ph.place_mark(tmp_path, "p1", "la", "boundary")
    assert first["at"] != second["at"]  # 同じ秒に置き直しても別の印と分かる
    ph.place_mark(tmp_path, "p1", "lb", "answered")
    assert ph.read_marks(tmp_path, "p1")["lb"]["why"] == "answered"
    assert ph.drop_mark(tmp_path, "p1", "lb") is True and ph.drop_mark(tmp_path, "p1", "lb") is False
    ph.clear_plan(tmp_path, "p1")
    assert ph.read_marks(tmp_path, "p1") == {} and not (d / ph.HOLD_FILE).exists()


def test_resume_rereads_marks_and_keeps_the_mark_of_a_refused_lane(tmp_path):
    plan_file(tmp_path)
    ph.place_mark(tmp_path, "p1", "la", "boundary")
    ph.place_mark(tmp_path, "p1", "lb", "boundary")
    ph.place_mark(tmp_path, "p1", "ld", "boundary")
    made = []

    def relay(_m, sub, n, _j):
        made.append((sub, n))
        return (0, {}) if n == "la" else (1, {"items": [{"result": "limit"}]})

    got = ph.resume_marks(tmp_path, "p1", ["la", "lb", "lc"], ["ld"], relay)
    assert got == {"restarted": ["la"], "refused": {"lb": "limit"}, "gone": ["lc", "ld"]}
    assert made == [("restart", "la"), ("restart", "lb")]  # 印の無い lc を起こさない
    assert list(ph.read_marks(tmp_path, "p1")) == ["lb"]


def test_skip_reason_round_trip_and_other_reasons(tmp_path, monkeypatch):
    d = plan_file(tmp_path)
    (d / ph.HOLD_FILE).write_text(json.dumps({"plan": True, "lanes": [], "count": 9, "limits": {"plan": 8, "lane": 3}}))
    monkeypatch.chdir(tmp_path)
    assert ph.skip_of(ph.plan_skip("la")) == {"plan": "p1", "lane": "la"}
    assert ph.plan_skip("zz") is None and ph.plan_skip(None) is None
    assert ph.skip_of("前のステージを流さなかった") is None and ph.skip_of(None) is None


def test_the_section_of_plan_md_names_the_scope_and_the_reason():
    text = "\n".join(ph.render_hold(ph.decide([*[ask(i) for i in range(4)], *[own(i) for i in range(5)]], ORDER, LIMITS).data(LIMITS)))
    assert "計画全体" in text and "| la | 4 |" in text
    assert ph.render_hold(None) == [] and ph.render_hold(ph.Hold().data(LIMITS)) == []
