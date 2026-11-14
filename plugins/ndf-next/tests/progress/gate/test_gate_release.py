"""レーンのリリースの依頼（リリース承認ゲート）: 依頼を出し、答えを承認ゲートの答えにし、差し戻しで依頼を閉じる（#3295・#3097）。"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from ndf.progress import gate, lane, model, plan, run, store

BY = "human:reviewer"


def _at_release(st, tid, flow):
    """設計承認ゲートを承認し、リリース承認ゲートの待ちまで流す。"""
    flow.turn(tid)
    q = gate.waiting(run.read(st, tid))
    gate.answer(st, tid, gate.Answer(q.id, "approved", "設計を承認", BY, "k-design"))
    assert flow.turn(tid)["status"] == "gate"
    q = gate.waiting(run.read(st, tid))
    assert q is not None and q.gate == "release"
    return q


def _requests(st, pid):
    with store.transaction(st) as conn:
        view, _ = lane.read_plan(conn, pid)
    return [r for r in view.requests.values() if r.body.get("kind") == gate.RELEASE_KIND]


def test_the_release_gate_of_a_lane_opens_one_request_on_the_plan(st, lane_sprint, flow):
    pid, tid = lane_sprint()
    q = _at_release(st, tid, flow)
    first = gate.request_release(st, run.read(st, tid), q, {})
    again = gate.request_release(st, run.read(st, tid), q, {})
    assert first == again and first["kind"] == "plan" and first["id"] == gate.request_id(q)
    (req,) = _requests(st, pid)
    assert req.lane == "a" and req.body["sprint_state"] == tid and req.body["prs"] == [8] and req.body["issues"] == [3092]


def test_a_rejection_at_the_release_gate_closes_the_request_with_the_rejection(st, lane_sprint, flow):
    pid, tid = lane_sprint()
    q = _at_release(st, tid, flow)
    gate.request_release(st, run.read(st, tid), q, {})
    out = gate.answer(st, tid, gate.Answer(q.id, "rejected", "版が違う", BY, "k-release"))
    (req,) = _requests(st, pid)
    assert req.closed == "版が違う" and req.release["result"] == plan.REJECTED
    assert out["status"] == "ok"
    ev = [e for e in run.read(st, tid).events if e.draft.kind == "answered"][-1]
    assert ev.draft.payload["release_request"] == req.key


def test_the_organizers_release_on_the_plan_becomes_the_gate_answer(st, lane_sprint, flow):
    pid, tid = lane_sprint()
    q = _at_release(st, tid, flow)
    sent = gate.request_release(st, run.read(st, tid), q, {})
    assert gate.take_reply(st, tid, {}) is None
    with store.transaction(st) as conn:
        plan.record_release(conn, pid, [sent["request"]], plan.ReleaseAnswer(plan.RELEASED, "出した", "11.0.0", "https://x/pull/9"))
    out = gate.take_reply(st, tid, {})
    assert out is not None and out["status"] == "ok"
    ev = [e for e in run.read(st, tid).events if e.draft.kind == "answered"][-1]
    assert ev.draft.actor == "organizer" and ev.draft.payload["answer"] == "approved" and ev.draft.payload["gate_id"] == q.id
    assert gate.take_reply(st, tid, {}) is None


def test_the_organizers_release_writes_the_version_and_the_record_pr_into_the_sprint_context(st, lane_sprint, flow):
    """まとめたリリースの版とリリース記録の PR は、答えと同じ書き込みで文脈へ入り、閉じが PR を探さずに読める（#2783）。"""
    pid, tid = lane_sprint()
    q = _at_release(st, tid, flow)
    sent = gate.request_release(st, run.read(st, tid), q, {})
    with store.transaction(st) as conn:
        plan.record_release(conn, pid, [sent["request"]], plan.ReleaseAnswer(plan.RELEASED, "出した", "11.0.0", "https://x/pull/9"))
    gate.take_reply(st, tid, {})
    v = run.read(st, tid)
    assert v.context()["version"] == "11.0.0" and v.context()["release_pr"] == "https://x/pull/9"
    kinds = [e.draft.kind for e in v.events][-2:]
    assert kinds == ["answered", "outputs_recorded"]


def test_a_rejection_from_the_organizer_writes_no_release_into_the_context(st, new_table, flow, tmp_path):
    tid = new_table()
    q = _at_release(st, tid, flow)
    env = {"NDF_ORGANIZER_DIR": str(tmp_path / "org"), "NDF_ORGANIZER_LANE": "a", "NDF_VERSION": "11.0.0"}
    gate.request_release(st, run.read(st, tid), q, env)
    (tmp_path / "org" / "answers").mkdir()
    reply = {"result": "rejected", "version": "11.0.0", "release": "https://x/pull/9"}
    (tmp_path / "org" / "answers" / f"{gate.request_id(q)}.json").write_text(json.dumps(reply))
    gate.take_reply(st, tid, env)
    assert "release_pr" not in run.read(st, tid).context()


def test_an_answer_carrying_a_key_the_stage_does_not_output_is_refused(st, new_table, flow):
    tid = new_table()
    q = _at_release(st, tid, flow)
    with pytest.raises(model.Refused, match="outputs に無い鍵"):
        gate.answer(st, tid, gate.Answer(q.id, "approved", "出した", BY, "k-x", outputs={"pr": 3}))
    assert run.decide(run.read(st, tid)).action == run.Action.GATE


def test_under_an_external_organizer_the_request_and_the_answer_are_files(st, new_table, flow, tmp_path):
    tid = new_table()
    q = _at_release(st, tid, flow)
    env = {"NDF_ORGANIZER_DIR": str(tmp_path / "org"), "NDF_ORGANIZER_LANE": "a", "NDF_VERSION": "11.0.0"}
    sent = gate.request_release(st, run.read(st, tid), q, env)
    body = json.loads((tmp_path / "org" / "requests" / f"{gate.request_id(q)}.json").read_text())
    assert sent["kind"] == "file" and body["lane"] == "a" and body["kind"] == "request" and body["ndf_version"] == "11.0.0"
    answers = tmp_path / "org" / "answers"
    answers.mkdir()
    (answers / f"{gate.request_id(q)}.json").write_text(json.dumps({"result": "rejected", "reason": "まとめから外す"}))
    out = gate.take_reply(st, tid, env)
    assert out is not None
    (got,) = [a for a in gate.answers(run.read(st, tid)) if a["gate"] == "release"]
    assert got["answer"] == "rejected" and got["reason"] == "まとめから外す" and got["by"] == "organizer"


def test_a_reply_to_a_request_whose_pr_head_moved_is_refused(st, new_table, flow, tmp_path):
    tid = new_table()
    q = replace(_at_release(st, tid, flow), target=gate.Target(head="old"))
    env = {"NDF_ORGANIZER_DIR": str(tmp_path / "org"), "NDF_ORGANIZER_LANE": "a", "NDF_VERSION": "11.0.0"}
    gate.request_release(st, run.read(st, tid), q, env)
    (tmp_path / "org" / "answers").mkdir()
    (tmp_path / "org" / "answers" / f"{gate.request_id(q)}.json").write_text(json.dumps({"result": "released"}))
    with pytest.raises(model.Refused, match="対象が変わった"):
        gate.take_reply(st, tid, env, observe=lambda _q: gate.Target(head="new"))
    assert run.decide(run.read(st, tid)).action == run.Action.GATE


def test_a_sprint_outside_a_lane_and_organizer_sends_no_request(st, new_table, flow):
    tid = new_table()
    q = _at_release(st, tid, flow)
    assert gate.request_release(st, run.read(st, tid), q, {}) is None
