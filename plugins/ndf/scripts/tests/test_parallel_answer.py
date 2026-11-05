"""`parallel.py answer` の承認した設計のマージと、答えの後に残る依頼の一覧（#2507）。耐久の記録と gh は差し替える。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import design_pr_merge  # noqa: E402
import parallel_answer as pa  # noqa: E402
import parallel_lane as pl  # noqa: E402


def gate1(rid, lane="la", pr=501, stamp="a" * 40, at="2026-11-05T00:00:00Z", verdict="unknown", **kw):
    return {"kind": "gate1", "id": rid, "lane": lane, "pr": pr, "stamp": stamp, "at": at, "verdict": verdict, "boundary": [], **kw}


def test_after_items_drop_the_answered_request_and_mark_resends_and_settled(tmp_path):
    state = tmp_path / "s.json"
    state.write_text("{}")
    answered = gate1("la-g501-a", sprint_state=str(state))
    items = [
        answered,
        gate1("la-g501-old", stamp="0" * 40, at="2026-11-04T00:00:00Z"),  # 同じ設計 PR の依頼: 答えで要らなくなった
        gate1("lb-g601-old", lane="lb", pr=601, at="2026-11-04T00:00:00Z", sprint_state=str(state)),
        gate1("lb-g601-new", lane="lb", pr=601, at="2026-11-05T01:00:00Z", sprint_state=str(state)),
        {"kind": "ask", "id": "lc-q1", "lane": "lc"},
    ]
    after = {q["id"]: q for q in pa.after_items(items, answered)}
    assert "la-g501-a" not in after
    assert [after[i]["drop"] for i in ("la-g501-old", "lb-g601-old", "lb-g601-new", "lc-q1")] == ["settled", "resend", None, None]
    assert after["lb-g601-new"]["mvv"]["eligible"] is True and after["lc-q1"]["mvv"]["eligible"] is False


@pytest.mark.parametrize(
    "q, eligible",
    [
        (dict(verdict="unknown"), True),
        (dict(verdict="unreadable"), True),
        (dict(verdict="not_follow"), False),
        (dict(verdict="machine"), False),
        (dict(verdict="unknown", boundary=["C1 に当たる"]), False),
        (dict(verdict="unknown", sprint_state="/無い/s.json"), False),
        (dict(verdict="unknown", kind="gate2"), False),
        (dict(verdict="unknown", returned=True), False),
    ],
)
def test_mvv_mark_only_for_undecided_design_approvals_without_redlines(tmp_path, q, eligible):
    state = tmp_path / "s.json"
    state.write_text("{}")
    item = {**gate1("x", sprint_state=str(state)), **q}
    assert pa.mvv_mark(item)["eligible"] is eligible


@pytest.fixture
def cli(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("parallel_cli_answer", SCRIPTS / "parallel.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    box = {"items": [gate1("la-g501-a")], "sent": [], "merges": [], "merge": {"result": "merged", "reason": ""}}
    monkeypatch.setattr(mod, "main_of", lambda: tmp_path)
    monkeypatch.setattr(pl, "run_status", lambda main, name: next(iter(pl.RUNNING)))
    monkeypatch.setattr(pa, "questions", lambda main, name: (7, box["items"]))
    monkeypatch.setattr(pa, "owner_restart", lambda *a, **k: None)
    monkeypatch.setattr(pl, "send_answer", lambda main, name, rid, result, **kw: box["sent"].append((rid, result, kw)))
    monkeypatch.setattr(
        design_pr_merge, "merge_approved", lambda root, pr, stamp, run=None: box["merges"].append((pr, stamp)) or {**box["merge"], "pr": pr}
    )
    box["mod"] = mod
    return box


def answer(cli, capsys, *argv):
    with pytest.raises(SystemExit) as e:
        cli["mod"].main(["answer", "p1", *argv])
    return e.value.code or 0, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_approved_design_is_merged_before_the_answer_is_sent(cli, capsys):
    code, out = answer(cli, capsys, "la-g501-a", "--approved")
    rid, result, kw = cli["sent"][0]
    assert code == 0 and cli["merges"] == [(501, "a" * 40)] and out["metrics"]["merge"]["result"] == "merged"
    assert (rid, result, kw["merge"]["result"], kw["by"]) == ("la-g501-a", "approved", "merged", "user")
    assert out["metrics"]["seq"] == 7 and out["metrics"]["after"] == []


def test_a_failed_merge_still_sends_the_answer_and_exits_0(cli, capsys):
    cli["merge"] = {"result": "conflict", "reason": "宛先と衝突している"}
    code, out = answer(cli, capsys, "la-g501-a", "--approved", "--by", "mvv")
    assert code == 0 and cli["sent"][0][2]["merge"]["result"] == "conflict" and cli["sent"][0][2]["by"] == "mvv"
    assert "宛先と衝突している" in out["summary"]


@pytest.mark.parametrize("argv", [["--rejected"], ["--choice", "A"]])
def test_other_answers_do_not_merge(cli, capsys, argv):
    code, out = answer(cli, capsys, "la-g501-a", *argv)
    assert code == 0 and cli["merges"] == [] and "merge" not in out["metrics"] and cli["sent"][0][2]["merge"] is None


def test_revise_does_not_merge(cli, capsys, tmp_path):
    note = tmp_path / "n.md"
    note.write_text("直す")
    answer(cli, capsys, "la-g501-a", "--revise", "--note", str(note))
    assert cli["merges"] == []


def test_by_mvv_needs_approved(cli, capsys):
    code, _ = answer(cli, capsys, "la-g501-a", "--rejected", "--by", "mvv")
    assert code == 1 and cli["sent"] == []


def test_after_items_also_drop_answers_sent_before_the_workflow_took_them():
    a, b = gate1("la-g501-a"), gate1("lb-g601-b", lane="lb", pr=601)
    items = [a, b, gate1("la-g501-old", at="2026-11-04T00:00:00Z"), {"kind": "ask", "id": "lc-q1", "lane": "lc"}]
    after = {q["id"]: q for q in pa.after_items(items, b, earlier=[a])}
    assert set(after) == {"la-g501-old", "lc-q1"} and after["la-g501-old"]["drop"] == "settled"


def test_answers_in_a_row_leave_the_earlier_one_out_of_after(cli, capsys):
    cli["items"] = [gate1("la-g501-a"), gate1("lb-g601-b", lane="lb", pr=601)]
    answer(cli, capsys, "la-g501-a", "--rejected")
    _, out = answer(cli, capsys, "lb-g601-b", "--rejected")  # 受け手はまだ a を一覧から外していない
    assert out["metrics"]["after"] == []
    cli["items"] = [gate1("lc-g701-c", lane="lc", pr=701)]  # 受け手が a・b を処理した
    _, out = answer(cli, capsys, "lc-g701-c", "--rejected")
    assert pa.sent_answers(cli["mod"].main_of(), "p1", cli["items"]) == [{"id": "lc-g701-c", "kind": "gate1", "lane": "lc", "pr": 701}]
