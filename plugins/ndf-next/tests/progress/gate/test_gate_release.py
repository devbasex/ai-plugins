"""レーンのリリースの依頼（リリース承認ゲート）: 依頼を出し、答えを承認ゲートの答えにし、差し戻しで依頼を閉じる（#3295・#3097）。"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace

import pytest

from ndf.progress import gate, lane, model, plan, run, store
from ndf.progress.gate import answering, release

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


# --- 現状固定（I-019 の前に request_release の分岐ごとの出し先と副作用を固定する） ---------------------------

ORG_ENV = {"NDF_ORGANIZER_LANE": "a", "NDF_VERSION": "11.0.0"}
# 今の事象の表で answered を書ける書き手（BY の human:reviewer は今の表では断られ、_at_release を通れない）
WRITER = "conductor"


def _now_at_release(st, tid, flow):
    """設計承認ゲートを WRITER で承認し、リリース承認ゲートの待ちまで流して承認資料を置く。"""
    flow.turn(tid)
    q = gate.waiting(run.read(st, tid))
    gate.answer(st, tid, gate.Answer(q.id, "approved", "設計を承認", WRITER, "k-design"))
    assert flow.turn(tid)["status"] == "gate"
    v = run.read(st, tid)
    q = gate.waiting(v)
    assert q is not None and q.gate == "release"
    gate.write_material(st.root, v, q)  # 呼び出し元（gate.present）と同じく、依頼の前に承認資料を置く
    return q


def _seen(st, q):
    return gate.material_path(st.root, q).with_name(q.id + ".seen.json")


def test_current_a_design_gate_sends_no_request_and_leaves_no_seen_file(st, lane_sprint, flow):
    pid, tid = lane_sprint()
    flow.turn(tid)
    q = gate.waiting(run.read(st, tid))
    assert q is not None and q.gate == "design"
    assert gate.request_release(st, run.read(st, tid), q, {}) is None
    assert not _seen(st, q).exists() and _requests(st, pid) == []


def test_current_the_seen_file_keeps_the_target_of_the_first_request(st, lane_sprint, flow):
    _pid, tid = lane_sprint()
    q = replace(_now_at_release(st, tid, flow), target=gate.Target(head="h1", material="m1"))
    gate.request_release(st, run.read(st, tid), q, {})
    gate.request_release(st, run.read(st, tid), replace(q, target=gate.Target(head="h2", material="m2")), {})
    assert json.loads(_seen(st, q).read_text(encoding="utf-8")) == {"head": "h1", "material_sha256": "m1"}


def test_current_the_plan_request_returns_where_it_was_sent_and_holds_the_request_fields(st, lane_sprint, flow):
    pid, tid = lane_sprint()
    q = _now_at_release(st, tid, flow)
    sent = gate.request_release(st, run.read(st, tid), q, {})
    (req,) = _requests(st, pid)
    assert sent == {"kind": "plan", "id": gate.request_id(q), "plan": pid, "request": req.key}
    assert set(req.body) == {"id", "lane", "prs", "issues", "sprint_state", "at", "kind"}
    assert req.body["id"] == gate.request_id(q) and req.body["lane"] == "a" and req.body["kind"] == gate.RELEASE_KIND


def test_current_a_sprint_outside_a_lane_still_leaves_the_seen_file(st, new_table, flow):
    tid = new_table()
    q = _now_at_release(st, tid, flow)
    assert gate.request_release(st, run.read(st, tid), q, {}) is None
    assert json.loads(_seen(st, q).read_text(encoding="utf-8")) == {"head": q.target.head, "material_sha256": q.target.material}


def test_current_the_file_request_returns_its_path_and_is_the_same_when_sent_again(st, new_table, flow, tmp_path):
    tid = new_table()
    q = _now_at_release(st, tid, flow)
    env = {"NDF_ORGANIZER_DIR": str(tmp_path / "org"), **ORG_ENV}
    first = gate.request_release(st, run.read(st, tid), q, env)
    again = gate.request_release(st, run.read(st, tid), q, env)
    path = tmp_path / "org" / "requests" / f"{gate.request_id(q)}.json"
    assert first == {"kind": "file", "id": gate.request_id(q), "path": str(path)}
    assert again == first
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["id"] == gate.request_id(q) and body["sprint_state"] == tid and body["issues"] == [3092]


@pytest.mark.parametrize("given", [{}, {"NDF_VERSION": ""}])
def test_current_without_a_given_version_the_file_request_uses_the_plugin_version(st, new_table, flow, tmp_path, monkeypatch, given):
    monkeypatch.setattr(lane, "ndf_version", lambda: "9.9.9")
    tid = new_table()
    q = _now_at_release(st, tid, flow)
    env = {"NDF_ORGANIZER_DIR": str(tmp_path / "org"), "NDF_ORGANIZER_LANE": "a", **given}
    sent = gate.request_release(st, run.read(st, tid), q, env)
    assert json.loads(open(sent["path"], encoding="utf-8").read())["ndf_version"] == "9.9.9"


def test_current_an_unreadable_version_refuses_the_file_request_after_the_seen_file(st, new_table, flow, tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "ndf_version", lambda: None)
    tid = new_table()
    q = _now_at_release(st, tid, flow)
    env = {"NDF_ORGANIZER_DIR": str(tmp_path / "org"), "NDF_ORGANIZER_LANE": "a"}
    with pytest.raises(model.Refused, match="NDF の版が読めず"):
        gate.request_release(st, run.read(st, tid), q, env)
    assert _seen(st, q).exists() and not (tmp_path / "org" / "requests").exists()


def test_current_a_file_request_without_a_lane_name_is_refused_by_the_exchange(st, new_table, flow, tmp_path):
    from ndf.progress.plan import external

    tid = new_table()
    q = _now_at_release(st, tid, flow)
    env = {"NDF_ORGANIZER_DIR": str(tmp_path / "org"), "NDF_VERSION": "11.0.0"}
    with pytest.raises(external.ExchangeError, match="lane"):
        gate.request_release(st, run.read(st, tid), q, env)


# --- 現状固定: 計画の依頼を引く 3 手（I-009。release._organizer_reply・answering._reject_release） -------------


def _rejection(q):
    return gate.Answer(q.id, run.REJECTED, "出さない", WRITER, "k-reject")


def test_current_the_organizer_reply_on_the_plan_is_none_until_the_release_is_recorded(st, lane_sprint, flow):
    pid, tid = lane_sprint()
    q = _now_at_release(st, tid, flow)
    gate.request_release(st, run.read(st, tid), q, {})
    assert release._organizer_reply(st, q, {}) is None
    (req,) = _requests(st, pid)
    with store.transaction(st) as conn:
        plan.record_release(
            conn, pid, [req.key], plan.ReleaseAnswer(plan.RELEASED, "出した", version="1.2.3", release="https://github.com/o/r/pull/9")
        )
    (req,) = _requests(st, pid)
    assert req.release is not None and release._organizer_reply(st, q, {}) == dict(req.release)


def test_current_outside_a_lane_there_is_no_organizer_reply_and_no_request_to_close(st, new_table, flow):
    tid = new_table()
    q = _now_at_release(st, tid, flow)
    assert release._organizer_reply(st, q, {}) is None
    with store.transaction(st) as conn:
        assert answering._reject_release(conn, q, _rejection(q)) is None


def test_current_a_rejection_closes_the_open_plan_request_once(st, lane_sprint, flow):
    pid, tid = lane_sprint()
    q = _now_at_release(st, tid, flow)
    with store.transaction(st) as conn:
        assert answering._reject_release(conn, q, _rejection(q)) is None  # 依頼がまだ無い
    gate.request_release(st, run.read(st, tid), q, {})
    with store.transaction(st) as conn:
        key = answering._reject_release(conn, q, _rejection(q))
    (req,) = _requests(st, pid)
    assert key == req.key and req.closed is not None
    with store.transaction(st) as conn:
        assert answering._reject_release(conn, q, _rejection(q)) is None  # 閉じた依頼は閉じ直さない


# --- #3572: 人の GitHub でのマージを承認ゲートの答えにする ------------------------------------------------

MERGED_HEAD = "a" * 40


def _runner(report: dict, calls: list):
    """副命令 `release-steps answer --route github-merge` の代わり（打った argv を `calls` へ足し、`report` を出す）。"""

    def run_it(argv, cwd, env):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(report) + "\n", "")

    return run_it


MERGED_REPORT = {
    "tool": "release-steps",
    "status": "ok",
    "summary": "人のマージを承認の証跡にした",
    "items": [
        {"kind": "value", "name": "state", "value": "approved"},
        {"kind": "value", "name": "by", "value": "takemi"},
        {"kind": "value", "name": "head", "value": MERGED_HEAD},
    ],
    "metrics": {},
    "outputs": {"version": "11.0.0"},
}


def test_a_human_merge_while_the_release_gate_waits_becomes_the_approval(st, lane_sprint, flow, tmp_path):
    """受け入れ条件 7・E6: 答え待ちのまま人が本番の PR をマージしたら、問い直さずに承認の答えを書く（答えた者は
    service:github-merge、理由にマージした人と先端）。答えた後は待ちが無く、副命令を打ち直さない。"""
    _pid, tid = lane_sprint()
    q = _at_release(st, tid, flow)
    calls: list = []
    out = gate.take_reply(st, tid, {}, runner=_runner(MERGED_REPORT, calls), cwd=tmp_path)
    assert out is not None and out["status"] == "ok"
    assert calls[0][-4:] == ["--pr", str(q.pr), "--route", "github-merge"] and "answer" in calls[0]
    (ev,) = [e for e in run.read(st, tid).events if e.draft.kind == "answered" and e.draft.payload["gate_id"] == q.id]
    assert ev.draft.payload["by"] == release.MERGE_ACTOR and ev.draft.payload["answer"] == "approved"
    assert "takemi" in ev.draft.reason and MERGED_HEAD[:12] in ev.draft.reason
    assert run.read(st, tid).context()["version"] == "11.0.0"
    assert gate.take_reply(st, tid, {}, runner=_runner(MERGED_REPORT, calls), cwd=tmp_path) is None and len(calls) == 1


def test_an_unmerged_production_pr_keeps_the_release_gate_waiting(st, lane_sprint, flow, tmp_path):
    _pid, tid = lane_sprint()
    _at_release(st, tid, flow)
    report = {k: v for k, v in MERGED_REPORT.items() if k != "outputs"}
    report = {**report, "items": [], "status": "stopped", "cause": {"kind": "not_applicable", "detail": "まだ"}}
    assert gate.take_reply(st, tid, {}, runner=_runner(report, []), cwd=tmp_path) is None
    assert gate.waiting(run.read(st, tid)) is not None


def test_an_unmerged_pr_read_by_the_recheck_does_not_start_the_merge_check(st, lane_sprint, flow, tmp_path):
    """待ちの読み直しで本番の PR が未マージと分かれば、人のマージを読む副命令を起動しない（待ちの度に CLI を起動しない）。"""
    _pid, tid = lane_sprint()
    _at_release(st, tid, flow)
    calls: list = []
    unmerged = lambda _q: gate.Target(head=MERGED_HEAD, merged=False)  # noqa: E731
    assert gate.take_reply(st, tid, {}, unmerged, runner=_runner(MERGED_REPORT, calls), cwd=tmp_path) is None
    assert calls == [] and gate.waiting(run.read(st, tid)) is not None


def test_a_broken_reply_of_the_merge_check_is_refused_and_the_gate_keeps_waiting(st, lane_sprint, flow, tmp_path):
    _pid, tid = lane_sprint()
    _at_release(st, tid, flow)
    refused = {k: v for k, v in MERGED_REPORT.items() if k != "outputs"}
    refused = {**refused, "items": [], "status": "stopped", "cause": {"kind": "refused", "detail": "届かない"}}

    def unreadable(argv, cwd, env):
        return subprocess.CompletedProcess(argv, 2, "not json\n", "boom\n")

    for runner in (_runner(refused, []), unreadable):
        with pytest.raises(model.Refused):
            gate.take_reply(st, tid, {}, runner=runner, cwd=tmp_path)
    assert gate.waiting(run.read(st, tid)) is not None


def test_the_organizers_answer_comes_before_the_merge_check(st, lane_sprint, flow, tmp_path):
    """オーガナイザーの答えがあれば副命令を打たない。GitHub を読まない呼び出し（runner も observe も無い）も打たない。"""
    pid, tid = lane_sprint()
    q = _at_release(st, tid, flow)
    sent = gate.request_release(st, run.read(st, tid), q, {})
    calls: list = []
    with store.transaction(st) as conn:
        plan.record_release(conn, pid, [sent["request"]], plan.ReleaseAnswer(plan.RELEASED, "出した", "11.0.0", "https://x/pull/9"))
    assert gate.take_reply(st, tid, {}, runner=_runner(MERGED_REPORT, calls), cwd=tmp_path) is not None
    assert calls == []
