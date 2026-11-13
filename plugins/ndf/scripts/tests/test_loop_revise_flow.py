"""開発ループの出し直しと承認の届け方（#2510）: 入力の書き出し・履歴・自動の出し直し・問い直し・前のループの待ち。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import parallel_lane  # noqa: E402
import test_sprint_loop as base  # noqa: E402
from test_sprint_loop import (  # noqa: E402
    built_lap,
    design_verdict,
    design_wait,
    gate,
    rec,
    set_state,
    sl,
    sl_gate,
    sl_select,
    start,
)
import gate1_requests as g1  # noqa: E402
import revise_note as rn  # noqa: E402
import sprint_loop_record as sl_record  # noqa: E402

env, mvv = base.env, base.mvv  # フィクスチャを受け継ぐ
SHA_A, SHA_B = "a" * 40, "b" * 40
TABLE = "# 設計\n\n## 出し直しの指摘への対応\n\n| 項目 | 指摘 | 対応 | 箇所 |\n| --- | --- | --- | --- |\n| l1:1-1 | 絞る | 直した | 対象範囲 |\n"


def to_waiting(env, n=11, pr=501):
    """課題を人の判断待ちの行へ入れる（設計承認ゲートの課題 n の子の行で止まった進行表。判定は設計 PR pr を持つ）。"""
    lap = built_lap(env)
    design_verdict(env, lap, pr)
    gate(env, design_wait(n))
    return lap


def note_file(env, text, name="n.md"):
    p = env.root / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def revise(env, text, n=11):
    return sl.cmd_answer(env, "l1", n, False, None, revise=True, note=note_file(env, text))


def closed(env):
    set_state(env, sl.current_lap(rec(env)))
    sl.cmd_lap(env, "l1", False, None, [])


def serve_doc(env, doc):
    """設計 PR の先頭の設計文書と本文の差し替え。"""
    base, box = env.run, {"body": "## Summary\n", "doc": doc}

    def run(cmd, cwd=None, stdin=None):
        if cmd[:2] == ["gh", "api"]:
            env.calls.append(cmd)
            return (0, box["doc"], "") if box["doc"] is not None else (1, "", "無い")
        if cmd[:3] == ["gh", "pr", "view"] and "body" in cmd:
            return 0, box["body"], ""
        if cmd[:3] == ["gh", "pr", "edit"] and "--body-file" in cmd:
            box["body"] = stdin
            return 0, "", ""
        return base(cmd, cwd=cwd, stdin=stdin)

    env.run = run
    return box


def test_the_next_sprint_passes_the_input_of_every_round(env):
    to_waiting(env)
    revise(env, "- 第一の指摘")
    closed(env)
    sl_select.cmd_select(env, "l1")
    base.sprint(env, "l1", "standard", "1.0.0-dev.2", None, [])
    new = next(c for c in reversed(env.calls) if "new" in c and str(sl_record.PROGRESS) in c)  # progress.py new（#3092）
    at = new.index("--revise-note") + 1
    n, path = new[at].split("=", 1)
    data = rn.read_input(path)
    assert n == "11" and data["rounds"][0]["note"] == "- 第一の指摘" and data["rounds"][0]["items"][0]["id"] == "l1:1-1"


def test_a_sprint_without_notes_has_no_argument(env):
    built_lap(env)
    assert not any("--revise-note" in c for c in env.calls)


def test_a_revise_is_kept_in_the_history_after_the_waiting_section_is_rewritten(env):
    to_waiting(env)
    revise(env, "- 第一の指摘")
    r = rec(env)
    w = next(x for x in r["waiting"] if x["answer"] == "revise")
    body = env.bodies[11]
    assert "第 1 回" in body and "> - 第一の指摘" in body and "節「出し直しの指摘の履歴」の開発ループ l1 の第 1 回" in body
    next_row = {**w, "lap": 2, "answer": None, "note": None}
    next_row.pop("revise_count")
    sl_gate.mark_issue(env, r, next_row)
    assert "> - 第一の指摘" in env.bodies[11] and "答え: まだ" in env.bodies[11]


def waiting_again(env, doc, note="- 絞る"):
    """出し直しの回を持つ課題のループの次の 1 回を承認ゲートで終え、人の判断待ちへ移す処理を通す。"""
    to_waiting(env)
    revise(env, note)
    closed(env)
    box = serve_doc(env, doc)
    sl_select.cmd_select(env, "l1")
    base.sprint(env, "l1", "standard", "1.0.0-dev.2", None, [])
    design_verdict(env, sl.current_lap(rec(env)), 501)
    return box, design_wait(11)


def test_a_table_gap_returns_to_auto_revise_without_asking(env, monkeypatch):
    box, item = waiting_again(env, "# 設計\n", "- 絞る\n- 分ける")
    sent = []
    monkeypatch.setattr(sl_gate, "lane_gate1", lambda *a, **k: sent.append(a) or None)
    gate(env, item)
    w = rec(env)["waiting"][-1]
    assert (w["answer"], w["by"], w["note"], w["revise_count"]) == ("revise", "auto", "", 2)
    assert w["missing"] == ["l1:1-1", "l1:1-2"] and sent == []
    assert "届いていない項目: l1:1-1、l1:1-2" in env.bodies[11] and "・自動）" in env.bodies[11]
    assert 11 in sl_select.first_issues(rec(env))


def test_after_an_auto_revise_a_gap_goes_to_the_human_with_the_missing_items(env):
    box, item = waiting_again(env, "# 設計\n")
    gate(env, item)
    closed_lap = sl.current_lap(rec(env))
    if closed_lap:
        closed(env)
    sl_select.cmd_select(env, "l1")
    base.sprint(env, "l1", "standard", "1.0.0-dev.3", None, [])
    design_verdict(env, sl.current_lap(rec(env)), 501)
    gate(env, item)
    w = rec(env)["waiting"][-1]
    assert not w.get("answer") and "届いていない項目: l1:1-1" in w["reason"]


def test_a_complete_table_is_copied_to_the_pr_and_asks_the_human(env):
    box, item = waiting_again(env, TABLE)
    gate(env, item)
    w = rec(env)["waiting"][-1]
    assert not w.get("answer") and "| l1:1-1 |" in box["body"]


def test_an_unreadable_doc_asks_the_human_with_the_reason(env):
    box, item = waiting_again(env, None)
    gate(env, item)
    w = rec(env)["waiting"][-1]
    assert not w.get("answer") and "表を読めなかった" in w["reason"]


def asked_row(env, monkeypatch, sent):
    """レーンの依頼を送った行（印 SHA_A）を作り、ループの 1 回を締める。"""
    to_waiting(env)
    r = rec(env)
    r["waiting"][0].update(request="la-g501-a", plan="p1", pr=501, stamp=SHA_A, report="r.md", state="s.json")
    sl.write_record(env, r)
    closed(env)
    env.answer_box = {}
    env.answers = lambda plan, ids: {i: env.answer_box.get(i, {"answer": None, "defer": None}) for i in ids}
    monkeypatch.setattr(
        sl_gate, "lane_gate1", lambda e, w, report, state: sent.append(w["number"]) or {"request": "la-g501-b", "stamp": SHA_B}
    )
    monkeypatch.setattr(parallel_lane, "lane_of", lambda main: ("p1", type("L", (), {"name": "la"})))
    return env


def approved(env, rid="la-g501-a"):
    env.answer_box[rid] = {"answer": {"result": "approved"}, "defer": None}


@pytest.fixture
def told(monkeypatch):
    got = []
    monkeypatch.setattr(parallel_lane, "send_unmatched", lambda main, plan, lane, rid, why: got.append((rid, why)))
    return got


def test_a_merge_only_move_carries_the_answer_without_asking_again(env, monkeypatch, told):
    sent = []
    asked_row(env, monkeypatch, sent)
    env.heads[501] = SHA_B
    monkeypatch.setattr(g1, "merge_only", lambda *a: True)
    approved(env)
    sl_select.cmd_select(env, "l1")
    w = rec(env)["waiting"][0]
    assert w["answer"] == "approved" and w["stamp"] == SHA_B and sent == [] and told == [] and "requests_before" not in w


@pytest.mark.parametrize("verdict", [False, None])
def test_another_move_asks_again_and_tells_once(env, monkeypatch, told, verdict):
    sent = []
    asked_row(env, monkeypatch, sent)
    env.heads[501] = SHA_B
    monkeypatch.setattr(g1, "merge_only", lambda *a: verdict)
    approved(env)
    sl_select.cmd_select(env, "l1")
    sl_select.cmd_select(env, "l1") if not sl.current_lap(rec(env)) else None
    w = rec(env)["waiting"][0]
    assert not w.get("answer") and w["request"] == "la-g501-b" and sent == [11]
    assert w["requests_before"] == [{"id": "la-g501-a", "stamp": SHA_A, "settled": True}] and w["restamped"] == SHA_A
    assert [t[0] for t in told] == ["la-g501-a"]


def before_row(env, stamp=SHA_A):
    r = rec(env)
    r["waiting"][0].update(request="la-g501-b", stamp=SHA_B, requests_before=[{"id": "la-g501-a", "stamp": stamp, "settled": False}])
    sl.write_record(env, r)


def test_an_answer_to_the_previous_request_lands_after_a_merge_only_move(env, monkeypatch, told):
    asked_row(env, monkeypatch, [])
    before_row(env)
    env.heads[501] = SHA_B
    monkeypatch.setattr(g1, "merge_only", lambda *a: True)
    approved(env)
    sl_select.cmd_select(env, "l1")
    w = rec(env)["waiting"][0]
    assert w["answer"] == "approved" and w["requests_before"][0]["settled"] is True and told == []


def test_an_answer_to_the_previous_request_after_another_move_is_told_once(env, monkeypatch, told):
    asked_row(env, monkeypatch, [])
    before_row(env)
    env.heads[501] = SHA_B
    monkeypatch.setattr(g1, "merge_only", lambda *a: False)
    approved(env)
    sl_select.cmd_select(env, "l1")
    w = rec(env)["waiting"][0]
    assert not w.get("answer") and w["requests_before"][0]["settled"] is True and [t[0] for t in told] == ["la-g501-a"]
    assert g1.answer_before(env, w, env.answer_box) is None and len(told) == 1  # 判定済みは読み直さない


def test_the_settled_previous_request_is_not_read(env):
    from sprint_loop_gate2 import pending_requests

    r = {"laps": [], "waiting": [{"request": "new", "plan": "p1", "requests_before": [{"id": "a", "settled": True}, {"id": "b"}]}]}
    assert pending_requests(r) == {"p1": ["new", "b"]}


def test_a_single_loop_approval_carries_over_a_merge_only_move(env, monkeypatch):
    to_waiting(env)
    r = rec(env)
    r["waiting"][0].update(pr=501, stamp=SHA_A)
    sl.write_record(env, r)
    env.heads[501] = SHA_B
    monkeypatch.setattr(g1, "merge_only", lambda *a: True)
    out = sl.cmd_answer(env, "l1", 11, True, None)
    w = rec(env)["waiting"][0]
    assert out["metrics"]["status"] == "answered" and w["answer"] == "approved" and w["stamp"] == SHA_B and env.merges[-1] == (501, SHA_B)


def test_a_head_moved_merge_carries_over_a_merge_only_move(env, monkeypatch):
    start(env)
    w = {"number": 11, "pr": 501, "answer": "approved", "stamp": SHA_A, "merge": {"result": "head_moved", "stamp": SHA_A}}
    r = rec(env)
    r["waiting"] = [w]
    env.heads[501] = SHA_B
    monkeypatch.setattr(g1, "merge_only", lambda *a: True)
    sl_select.settle_approved_designs(env, r)
    assert w["stamp"] == SHA_B and env.merges == [(501, SHA_B)] and w["answer"] == "approved"


def lane(monkeypatch, env, rows):
    monkeypatch.setattr(g1, "lane_plan", lambda e: "p1")
    monkeypatch.setattr(g1, "prior_rows", lambda main, plan, home=None: (rows, []))


def test_a_prior_wait_drops_the_issue_with_the_loop_name_and_frees_its_slot(env, monkeypatch):
    start(env)
    sent = []
    lane(monkeypatch, env, [("lroot2", {"number": 11, "pr": 412, "lap": 3})])
    monkeypatch.setattr(parallel_lane, "send_prior", lambda main, plan, msg: sent.append(msg["id"]))
    out = sl_select.cmd_select(env, "l1")
    why = {x["number"]: x["reason"] for x in rec(env)["laps"][-1]["selection"]}
    assert why[11] == "人の判断待ち（開発ループ lroot2）" and sent == ["lroot2-w11-l3"] and out["metrics"]["selected"] == [12, 13]


def test_a_loop_outside_a_plan_does_not_read_prior_waits(env, monkeypatch):
    start(env)
    monkeypatch.setattr(g1, "prior_rows", lambda *a: pytest.fail("レーンでないループは読まない"))
    assert sl_select.cmd_select(env, "l1")["metrics"]["selected"] == [11, 12]


def test_a_request_that_could_not_be_sent_is_sent_at_the_boundary(env, monkeypatch):
    to_waiting(env)
    r = rec(env)
    r["waiting"][0].update(pr=501, report="r.md", state="s.json")
    sl.write_record(env, r)
    closed(env)
    lane(monkeypatch, env, [])
    monkeypatch.setattr(sl_select, "lane_gate1", lambda e, w, report, state: {"request": "la-g501-x", "plan": "p1"})
    sl_select.cmd_select(env, "l1")
    assert rec(env)["waiting"][0]["request"] == "la-g501-x"


def test_a_skipped_loop_appears_in_the_selection_items(env, monkeypatch):
    start(env)
    monkeypatch.setattr(g1, "lane_plan", lambda e: "p1")
    monkeypatch.setattr(g1, "prior_requests", lambda *a: ([], [{"loop": "bad", "reason": "壊れている"}]))
    monkeypatch.setattr(g1, "prior_rows", lambda *a: ([], []))
    out = sl_select.cmd_select(env, "l1")
    assert {"skipped_loop": "bad", "reason": "壊れている"} in out["items"]
