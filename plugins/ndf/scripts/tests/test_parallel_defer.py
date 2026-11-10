"""溜めた依頼（#2913 の受け入れ条件 4・5・7 と I5・I6）: 業務時間外の `parallel.py wait` は設計の承認と問いを返さずに溜め、
業務時間に入った後の最初の `wait` が答えていない依頼の全件を 1 回でまとめて返す。時刻は判定の関数へ渡して固定する。"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_parallel_hold_flow as tph  # noqa: E402
import test_parallel_run as tpr  # noqa: E402

import durable  # noqa: E402
import gh_call  # noqa: E402
import pace  # noqa: E402
import parallel_answer as pa  # noqa: E402
import parallel_defer as pdf  # noqa: E402
import parallel_lane as pl  # noqa: E402
import parallel_route as prt  # noqa: E402

env = tpr.env
HOURS = {"timezone": "Asia/Tokyo", "days": ["mon", "tue", "wed", "thu", "fri"], "start": "09:00", "end": "18:00"}
NIGHT = dt.datetime(2026, 11, 10, 13, 0, tzinfo=dt.timezone.utc)  # 火 22:00 JST
MORNING = dt.datetime(2026, 11, 11, 0, 5, tzinfo=dt.timezone.utc)  # 水 09:05 JST


def gate1(i, verdict="approved", **kw):
    return {"id": i, "kind": "gate1", "lane": "la", "issue": 7, "pr": 70, "verdict": verdict, **kw}


ASK = {"id": "a1", "kind": "ask", "lane": "lb", "text": "どちら？"}
BUILD = {"id": "b1", "kind": "build", "lane": None, "text": "組めない"}


@pytest.fixture
def plan(env, monkeypatch, capsys):
    main = env["main"]
    (main / ".ndf" / "pace.json").write_text(json.dumps({"version": 1, "business_hours": HOURS}))
    cli = tph.load_cli()
    monkeypatch.setattr(cli, "main_of", lambda: main)
    monkeypatch.setattr(pl, "record_exists", lambda *_a: True)
    box = {"now": NIGHT, "items": [], "seq": 1, "waited": []}
    monkeypatch.setattr(pdf, "hours_of", lambda m, now=None: pace.hours_now(m, box["now"]))
    monkeypatch.setattr(pa, "questions", lambda *_a: (box["seq"], list(box["items"])))

    def wait_from(*a, **_k):
        box["waited"].append(a[5])
        return durable.Outcome("event", {"key": "questions", "seq": box["seq"], "items": list(box["items"])})

    monkeypatch.setattr(durable, "wait_from", wait_from)
    monkeypatch.setattr(gh_call, "RUNNER", lambda args, stdin=None, **_k: gh_call.GhResult(0, f"{args[0]}-{args[2]}\n", ""))

    def call(*argv):
        return tph.wait(cli, capsys, *argv)

    return main, box, call


def control(main):
    return json.loads(pdf.control_path(main, "p1").read_text())


def test_outside_hours_lane_requests_are_kept_and_the_morning_wait_returns_them_at_once(plan):
    main, box, call = plan
    box["items"] = [gate1("g1"), ASK, BUILD]
    code, out = call()
    assert code == 0 and [q["id"] for q in out["items"]] == ["b1"]  # 耐久ワークフローの問いは今問う
    assert out["metrics"]["deferred"] == {"count": 2, "until": "2026-11-11T00:00:00Z"}
    box["items"], box["seq"] = [gate1("g1"), ASK], 2
    code, out = call("--after", "1")
    assert code == 3 and "2 件溜めた" in out["summary"] and out["metrics"]["next"].endswith("--after 2")
    assert control(main)["ids"] == ["a1", "g1"]
    box["now"] = MORNING
    code, out = call("--after", "2")
    assert code == 0 and out["metrics"]["batch"] is True and [q["id"] for q in out["items"]] == ["g1", "a1"]
    g = out["items"][0]
    assert g["title"] == "issue-7" and g["url"] == "pr-70" and g["verdict"] == "approved" and g["mvv"]["eligible"] is False
    assert control(main)["ids"] == [] and control(main)["shown"] == ["a1", "g1"]


def test_shown_requests_are_not_kept_again_at_night_but_new_ones_are(plan):
    """I5: まとめて示した依頼に答えないまま時間外に入り、--after 0 で読み直しても溜め直さない。後から届いた依頼は溜まる。"""
    main, box, call = plan
    box["items"] = [gate1("g1"), ASK]
    call()
    box["now"] = MORNING
    call()
    box["now"] = NIGHT + dt.timedelta(days=1)
    box["items"] = [gate1("g1"), ASK, gate1("g2")]
    code, out = call("--after", "0")
    assert code == 3 and control(main)["ids"] == ["g2"] and set(control(main)["shown"]) == {"a1", "g1"}


def test_a_batch_left_unasked_is_returned_again_by_wait_after_zero(plan):
    """I5: ids を空にしても依頼の一覧からは外さない。業務時間内の --after 0 が今と同じく返す。"""
    main, box, call = plan
    box["items"] = [gate1("g1")]
    call()
    box["now"] = MORNING
    assert call()[1]["metrics"]["batch"] is True
    code, out = call("--after", "0")
    assert code == 0 and [q["id"] for q in out["items"]] == ["g1"] and "batch" not in out["metrics"]


def test_answered_requests_leave_the_control(plan):
    main, box, call = plan
    box["items"] = [gate1("g1"), ASK]
    call()
    box["items"], box["seq"] = [ASK], 2
    call("--after", "1")
    assert control(main)["ids"] == ["a1"]


def test_an_unreadable_control_keeps_the_requests_and_the_morning_batch_shows_them(plan):
    main, box, call = plan
    pdf.control_path(main, "p1").write_text("{壊れた")
    box["items"] = [gate1("g1")]
    assert call()[0] == 3
    box["now"] = MORNING
    code, out = call()
    assert code == 0 and out["metrics"]["batch"] is True and [q["id"] for q in out["items"]] == ["g1"]


def test_an_mvv_eligible_design_is_returned_once_at_night_and_joins_the_morning_batch(plan, tmp_path):
    """I6: MVV 判定を試せる設計の承認は時間外に 1 回だけ返り、答えないまま業務時間に入るとまとめに入る。"""
    main, box, call = plan
    state = tmp_path / "state.json"
    state.write_text("{}")
    box["items"] = [gate1("g3", verdict="unknown", sprint_state=str(state))]
    code, out = call()
    assert code == 0 and [q["id"] for q in out["items"]] == ["g3"] and control(main)["offered"] == ["g3"]
    assert call("--after", "1")[0] == 3
    box["now"] = MORNING
    code, out = call("--after", "1")
    assert out["metrics"]["batch"] is True and out["items"][0]["mvv"]["eligible"] is True


def test_without_a_declaration_wait_is_unchanged_and_writes_no_control(plan):
    """受け入れ条件 5: 宣言が無ければ時間外の時刻でも全件を返し、控えを書かない。"""
    main, box, call = plan
    (main / ".ndf" / "pace.json").write_text(json.dumps({"version": 1}))
    box["items"] = [gate1("g1"), ASK]
    code, out = call()
    assert code == 0 and [q["id"] for q in out["items"]] == ["g1", "a1"] and "deferred" not in out["metrics"]
    assert not pdf.control_path(main, "p1").exists()
    assert box["waited"] == [3600]


def test_the_night_wait_is_cut_at_the_start_and_then_returns_the_batch(plan, monkeypatch):
    main, box, call = plan
    box["items"] = [gate1("g1")]
    call()
    states = iter([NIGHT, NIGHT, MORNING])
    monkeypatch.setattr(pdf, "hours_of", lambda m, now=None: pace.hours_now(m, next(states)))
    monkeypatch.setattr(durable, "wait_from", lambda *_a, **_k: durable.Outcome("timeout"))
    code, out = call("--after", "1")
    assert code == 0 and out["metrics"]["batch"] is True


def test_the_timeout_is_cut_at_the_next_start_only_outside_hours():
    decl, _ = pace.parse_hours(HOURS)
    night, morning = pace.hours_state(decl, NIGHT), pace.hours_state(decl, MORNING)
    assert pdf.clip_timeout(86400, night, NIGHT) == 11 * 3600
    assert pdf.clip_timeout(60, night, NIGHT) == 60 and pdf.clip_timeout(86400, morning, MORNING) == 86400
    assert pdf.clip_timeout(86400, night, dt.datetime(2026, 11, 11, 0, 0, 0, 1, tzinfo=dt.timezone.utc)) == 1.0


def test_check_and_start_warn_about_a_broken_declaration(env):
    """受け入れ条件 4: check・start の警告に business-hours の行が載る。"""
    main = env["main"]
    (main / ".ndf" / "pace.json").write_text(json.dumps({"version": 1, "business_hours": {**HOURS, "end": "08:00"}}))
    assert [i["rule"] for i in prt.start_warnings(main)] == ["business-hours"]
    assert "business-hours" in [i["rule"] for i in prt.check_items(main)]
