"""進行表のモデル（lib/progress_model.py。#3092 の I4〜I6・I12a〜I13・I16・I18〜I23・決定 16・19・20）。DBOS を起動しない。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import progress_model as pm  # noqa: E402
import workflow_def as wd  # noqa: E402

D = pm.EventDraft
SNAP = wd.load_definition("standard")
HEIGHTS = wd.mode_heights()


def ev(seq, kind, row=None, rev=1, actor="workflow", **kw):
    return pm.Event(kind=kind, actor=actor, row_key=row, rev=rev, table_id="t", seq=seq, at=f"t{seq}", **kw)


def rows_of(*pairs):
    return {k: pm.RowState(s) for k, s in pairs}


# --- fold・I23 ---


def test_fold_start_finish_and_rev_filter():
    events = [ev(1, "created", "設計"), ev(2, "started", "設計"), ev(3, "finished", "設計"),
              ev(4, "created", "設計", rev=2), ev(5, "answered", "設計", rev=2, payload={"answer": "approved"})]
    assert pm.fold(events, 1)["設計"].state == pm.FINISHED
    r2 = pm.fold(events, 2)["設計"]
    assert r2.state == pm.PENDING and r2.payload == {"answer": "approved"}  # state が空の事象は状態を変えない


def test_fold_table_cancel_closes_open_rows():
    events = [ev(1, "created", "a"), ev(2, "created", "b"), ev(3, "finished", "a"), ev(4, "cancelled", None, actor="conductor")]
    # finished の前に started が無いが、畳み込みは遷移の判定をしない
    rows = pm.fold(events, 1)
    assert rows["a"].state == pm.FINISHED and rows["b"].state == pm.CANCELLED
    assert pm.table_state(rows) == pm.CANCELLED


# --- 書き手の表（I6） ---


def test_writer_table_allows_and_refuses():
    pm.check_writer("sprint", "started", "workflow")
    pm.check_writer("sprint", "answered", "organizer")
    for kind in ("created", "adopted", "row_started", "lane_added", "request_opened", "plan_started"):
        level = {"row_started": "lane", "lane_added": "plan", "request_opened": "plan", "plan_started": "plan"}.get(kind, "sprint")
        pm.check_writer(level, kind, "adopt")
    for level, kind, actor in [("sprint", "started", "organizer"), ("sprint", "cancelled", "organizer"),
                               ("sprint", "started", "adopt"), ("sprint", "completed", "durable"),
                               ("sprint", "finished", "conductor"), ("plan", "plan_completed", "organizer")]:
        with pytest.raises(pm.Refused):
            pm.check_writer(level, kind, actor)


# --- 遷移（I4・I5・状態の図） ---


def test_transitions():
    rows = rows_of(("設計", pm.RUNNING), ("計画", pm.FAILED), ("実装", pm.PENDING), (pm.TABLE_KEY, pm.RUNNING))
    with pytest.raises(pm.Refused, match="I4"):
        pm.check_event("sprint", rows, D("disabled", "conductor", "設計", 1, reason="x"))
    pm.check_event("sprint", rows, D("manual_finished", "conductor", "計画", 1, reason="手で"))
    pm.check_event("sprint", rows, D("retry", "conductor", "計画", 1, reason="もう一度"))
    with pytest.raises(pm.Refused):
        pm.check_event("sprint", rows, D("retry", "conductor", "実装", 1, reason="x"))
    with pytest.raises(pm.Refused, match="理由"):
        pm.check_event("sprint", rows, D("disabled", "conductor", "実装", 1))
    with pytest.raises(pm.Refused, match="rev"):
        pm.check_event("sprint", rows, D("started", "workflow", "実装"))
    done = rows_of(("設計", pm.FINISHED), (pm.TABLE_KEY, pm.RUNNING))
    with pytest.raises(pm.Refused):
        pm.check_event("sprint", done, D("started", "workflow", "設計", 1))
    over = rows_of((pm.TABLE_KEY, pm.COMPLETED))
    with pytest.raises(pm.Refused, match="完了"):
        pm.check_event("sprint", over, D("disabled", "conductor", "設計", 1, reason="x"))


def test_mode_only_goes_up():
    rows = rows_of((pm.TABLE_KEY, pm.RUNNING))
    pm.check_event("sprint", rows, D("mode_raised", "conductor", None, 2, reason="r", payload={"from": "light", "to": "standard"}), HEIGHTS)
    with pytest.raises(pm.Refused, match="I5"):
        pm.check_event("sprint", rows, D("mode_raised", "conductor", None, 2, reason="r", payload={"from": "standard", "to": "light"}), HEIGHTS)


# --- next_action ---

EN = {s.name: wd.Enablement(True) for s in SNAP.stages}


def test_next_action_table():
    names = SNAP.stage_names
    assert pm.next_action(SNAP, {}, EN, True).kind == "stop_plan_cancelled"
    assert pm.next_action(SNAP, {}, EN, False) == pm.Action("run", names[0])
    rows = rows_of((names[0], pm.FINISHED), (names[1], pm.FAILED), (names[2], pm.PENDING))
    assert pm.next_action(SNAP, rows, EN, False).kind == "stop_failed"  # 失敗の後ろを流さない
    rows = rows_of((names[0], pm.FINISHED), (names[1], pm.RUNNING))
    assert pm.next_action(SNAP, rows, EN, False) == pm.Action("resume", names[1])
    en = {**EN, names[1]: wd.Enablement(False, "宣言", "project")}
    assert pm.next_action(SNAP, rows_of((names[0], pm.FINISHED)), en, False) == pm.Action("skip", names[1], "宣言")
    rows = {n: pm.RowState(pm.FINISHED) for n in names}
    assert pm.next_action(SNAP, rows, EN, False).kind == "complete"
    rows = {n: pm.RowState(pm.FINISHED) for n in names[: names.index("実装")]}
    a = pm.next_action(SNAP, rows, EN, False, pace="fast", issues=(1, 2))
    assert a.kind == "run_children" and a.children == ("実装#1", "実装#2")


# --- carry_rows・adopt_rows ---


def test_carry_rows():
    names = SNAP.stage_names
    old = rows_of((names[0], pm.FINISHED), (names[1], pm.DISABLED), (names[2], pm.FAILED))
    drafts = pm.carry_rows(old, SNAP, names[3], 2)
    kinds = [(d.row_key, d.kind) for d in drafts[:4]]
    assert kinds == [(names[0], "carried"), (names[1], "carried"), (names[2], "created"), (names[3], "created")]
    assert drafts[1].resolved_state() == pm.DISABLED and all(d.rev == 2 for d in drafts)
    assert all(d.kind == "created" for d in pm.carry_rows(old, SNAP, names[0], 2))


MAP = {"設計": ["設計", "ドキュメント再構成", "ドキュメントレビュー"], "実装": ["実装"], "検査": ["構造改善", "実装レビュー", "完了判定"],
       "実装レビュー": ["実装レビュー"], "開発版": ["Pull Request", "後片付け", "デプロイ"], "本番": ["デプロイ"]}


def test_adopt_rows_full_partial_none():
    drafts = pm.adopt_rows(SNAP, {"設計", "開発版"}, MAP)
    by = {d.row_key: d for d in drafts if d.kind == "adopted"}
    assert by["ドキュメントレビュー"].resolved_state() == pm.FINISHED  # 1 つのステージが 3 つの工程に当たる
    assert by["デプロイ"].resolved_state() == pm.FAILED and "本番" in by["デプロイ"].reason
    assert "実装" not in by
    with pytest.raises(ValueError, match="対応の無い"):
        pm.adopt_rows(SNAP, {"謎"}, MAP)


# --- 計画とレーン（I12a・I13・I16・I18〜I21） ---


def lanes_plan():
    plan = {"lanes": {"a": {"touches": []}, "b": {"touches": []}}, "max_lanes": 2, "requests": {}}
    lanes = {
        "a": [{"row": 1, "issues": [1], "touches": ["x/"], "after": [], "state": pm.RUNNING},
              {"row": 2, "issues": [2], "touches": ["y/"], "after": [], "state": pm.PENDING}],
        "b": [{"row": 1, "issues": [3], "touches": ["x/"], "after": ["a#1"], "state": pm.PENDING}],
    }
    return plan, lanes


def test_lane_change_overlaps_and_order():
    plan, lanes = lanes_plan()
    # after: a#1 の b#1 と a#1 は重なってよい（今の状態は通る）
    ok = D("row_added", "conductor", "3", payload={"lane": "a", "row": 3, "issues": [4], "touches": ["z/"]})
    assert pm.check_lane_change(plan, lanes, ok) == []
    bad = D("row_added", "conductor", "3", payload={"lane": "a", "row": 3, "issues": [4], "touches": ["x/"]})
    # a#3 は a#1 の後ろ・b#1 は a#1 の後ろで、a#3 と b#1 は同時に動き得る
    assert any("I13" in e for e in pm.check_lane_change(plan, lanes, bad))
    dup = D("row_added", "conductor", "3", payload={"lane": "a", "row": 3, "issues": [3], "touches": []})
    assert any("I19" in e for e in pm.check_lane_change(plan, lanes, dup))
    assert any("I12a" in e for e in pm.check_lane_change(plan, lanes, D("row_moved", "conductor", "1", payload={"lane": "a", "row": 1, "to": 1})))
    assert any("I18" in e for e in pm.check_lane_change(plan, {**lanes, "a": [{**lanes["a"][0], "state": pm.PENDING}, lanes["a"][1]]},
                                                         D("row_cancelled", "conductor", "1", payload={"lane": "a", "row": 1})))
    assert any("I20" in e for e in pm.check_lane_change(plan, lanes, D("lane_added", "organizer", payload={"lane": "c", "touches": []})))
    # a#2 が b#1 を待つ形へ、b#1 の前に a#2 を待つ行を足すと輪になる
    cyc = D("row_added", "conductor", "2", payload={"lane": "b", "row": 2, "issues": [9], "touches": [], "after": ["a#2"], "position": 0})
    lanes2 = {**lanes, "a": [lanes["a"][0], {**lanes["a"][1], "after": ["b#1"]}]}
    assert any("I21" in e for e in pm.check_lane_change(plan, lanes2, cyc))


def test_next_row_and_wakeable_and_complete():
    plan, lanes = lanes_plan()
    assert pm.next_row("a", plan, lanes) == {"lane": "a", "row": 2, "issues": [2], "args": []}
    assert pm.next_row("b", plan, lanes) == "waiting_after"
    assert pm.wakeable_lanes(plan, lanes) == []
    lanes["a"][0]["state"] = pm.COMPLETED
    assert pm.wakeable_lanes(plan, lanes) == [("a", 2), ("b", 1)]
    lanes["b"][0]["state"] = pm.COMPLETED
    lanes["a"][1]["state"] = pm.CANCELLED
    assert pm.next_row("a", plan, lanes) is None
    assert pm.plan_complete(lanes, [])
    assert not pm.plan_complete(lanes, [{"open": True}])
    assert pm.plan_complete(lanes, [{"open": False}])


def test_lane_rows_and_plan_view_from_events():
    events = [ev(1, "created", None, rev=None, actor="organizer"),
              ev(2, "row_added", "1", rev=None, actor="organizer", payload={"row": 1, "issues": [1]}),
              ev(3, "row_added", "2", rev=None, actor="conductor", payload={"row": 2, "issues": [2], "position": 0}),
              ev(4, "row_started", "1", rev=None, actor="conductor", payload={"sprint": "sprint:x:s"})]
    rows = pm.lane_rows(events)
    assert [r["row"] for r in rows] == [2, 1] and rows[1]["state"] == pm.RUNNING and rows[1]["sprint"] == "sprint:x:s"
    pv = pm.plan_view([ev(1, "created", None, rev=None, payload={"max_lanes": 3}), ev(2, "lane_added", None, rev=None, payload={"lane": "a"}),
                       ev(3, "plan_started", None, rev=None), ev(4, "request_opened", None, rev=None, payload={"x": 1}),
                       ev(5, "request_closed", None, rev=None, payload={"request": "4"})])
    assert pv["state"] == pm.RUNNING and pv["max_lanes"] == 3 and not pv["requests"]["4"]["open"]


def test_table_id_is_stable():
    assert pm.table_id("sprint", "r", "s") == pm.table_id("sprint", "r", "s")
    assert pm.table_id("sprint", "r", "s") != pm.table_id("plan", "r", "s")


def test_model_does_not_import_dbos_or_db():
    """決定 20: 判断の関数は DBOS も durable も DB も import しない。"""
    code = f"import sys; sys.path.insert(0, {str(LIB)!r}); import progress_model, reshuffle; " \
           "bad = [m for m in ('dbos', 'durable', 'sqlalchemy', 'progress_db') if m in sys.modules]; print(bad)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]"
