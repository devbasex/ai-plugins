"""レーンのリリースのリクエスト（リリース承認ゲート）: リクエストを出し、回答を承認ゲートの回答にし、変更要求でリクエストを閉じる（#3295・#3097）。"""

from __future__ import annotations

import argparse
import json
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
    out = gate.answer(st, tid, gate.Answer(q.id, "rejected", "バージョンが違う", BY, "k-release"))
    (req,) = _requests(st, pid)
    assert req.closed == "バージョンが違う" and req.release["result"] == plan.REJECTED
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
    """まとめたリリースのバージョンとリリース記録の PR は、回答と同じ書き込みで文脈へ入り、閉じが PR を探さずに読める（#2783）。"""
    pid, tid = lane_sprint()
    q = _at_release(st, tid, flow)
    sent = gate.request_release(st, run.read(st, tid), q, {})
    with store.transaction(st) as conn:
        plan.record_release(conn, pid, [sent["request"]], plan.ReleaseAnswer(plan.RELEASED, "出した", "11.0.0", "https://x/pull/9"))
    gate.take_reply(st, tid, {})
    v = run.read(st, tid)
    assert v.context()["product_version"] == "11.0.0" and v.context()["release_pr"] == "https://x/pull/9"
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
    with pytest.raises(model.Refused, match="outputs に無いキー"):
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
    (tmp_path / "org" / "answers" / f"{gate.request_id(q)}.json").write_text(json.dumps({"result": "released", "version": "11.0.0"}))
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
    gate.write_material(st.root, v, q)  # 呼び出し元（gate.present）と同じく、リクエストの前に承認資料を置く
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
    assert json.loads(_seen(st, q).read_text(encoding="utf-8")) == {
        "head": "h1",
        "material_sha256": "m1",
        "body_sha256": None,
        "title_sha256": None,
    }


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
    assert json.loads(_seen(st, q).read_text(encoding="utf-8")) == q.target.as_dict()


def test_current_the_file_request_returns_its_path_and_is_the_same_when_sent_again(st, new_table, flow, tmp_path):
    tid = new_table()
    q = _now_at_release(st, tid, flow)
    env = {"NDF_ORGANIZER_DIR": str(tmp_path / "org"), **ORG_ENV}
    first = gate.request_release(st, run.read(st, tid), q, env)
    path = tmp_path / "org" / "requests" / f"{gate.request_id(q)}.json"
    # 打ち直しが秒をまたいでも、書いた時刻だけの違いは同じリクエストとみなす
    path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), "at": "2000-01-01T00:00:00+00:00"}), encoding="utf-8")
    again = gate.request_release(st, run.read(st, tid), q, env)
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
    with pytest.raises(model.Refused, match="NDF のバージョンが読めず"):
        gate.request_release(st, run.read(st, tid), q, env)
    assert _seen(st, q).exists() and not (tmp_path / "org" / "requests").exists()


def test_current_a_file_request_without_a_lane_name_is_refused_by_the_exchange(st, new_table, flow, tmp_path):
    from ndf.progress.plan import external

    tid = new_table()
    q = _now_at_release(st, tid, flow)
    env = {"NDF_ORGANIZER_DIR": str(tmp_path / "org"), "NDF_VERSION": "11.0.0"}
    with pytest.raises(external.ExchangeError, match="lane"):
        gate.request_release(st, run.read(st, tid), q, env)


# --- 現状固定: 計画のリクエストを引く 3 手（I-009。release._organizer_reply・answering._reject_release） -------------


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
        assert answering._reject_release(conn, q, _rejection(q)) is None  # リクエストがまだ無い
    gate.request_release(st, run.read(st, tid), q, {})
    with store.transaction(st) as conn:
        key = answering._reject_release(conn, q, _rejection(q))
    (req,) = _requests(st, pid)
    assert key == req.key and req.closed is not None
    with store.transaction(st) as conn:
        assert answering._reject_release(conn, q, _rejection(q)) is None  # 閉じたリクエストは閉じ直さない


# --- #4513: バージョン番号を持たないリポジトリで、オーガナイザーの承認と一緒に届け先を記録し、デプロイの工程が worker を起動せずに終える ---


from ndf.contracts import result, worker_kinds  # noqa: E402
from ndf.core import branch_strategy  # noqa: E402
from ndf.progress import command, invoke  # noqa: E402
from ndf.progress.gate import entry  # noqa: E402
from ndf.progress.plan import cli as plan_cli  # noqa: E402
from result_json import last_result  # noqa: E402

from .conftest import lanes as gate_lanes  # noqa: E402

DIST = "main abc1234"
ROW = {"target": "本番", "kind": "manual", "trigger": "手で出す", "channel": "production", "versioned": False}


def _strategy(kind: str, rows=(ROW,)) -> branch_strategy.Strategy:
    return branch_strategy.Strategy({"release": {"version_store": {"kind": kind}, "deliveries": list(rows)}})


def _organizer_released(st, lane_sprint, flow, monkeypatch, strategy, version=DIST):
    """オーガナイザーが `version` を記録して承認するまで流す。リリースしたリポジトリの設定は `strategy`（例外なら読めない）。

    届け先と保存先の種類はオーガナイザーが記録の時点に決める。レーンが回答を受け取るときは設定を読まない（#4513）。
    """
    pid, tid = lane_sprint()
    q = _at_release(st, tid, flow)
    sent = gate.request_release(st, run.read(st, tid), q, {})
    store_kind, row = plan.released_where(None if isinstance(strategy, Exception) else strategy)
    answer = plan.ReleaseAnswer(plan.RELEASED, "出した", version, "https://x/pull/9", store_kind, row)
    with store.transaction(st) as conn:
        plan.record_release(conn, pid, [sent["request"]], answer)
    with monkeypatch.context() as m:
        m.setattr(branch_strategy, "load", lambda _root: pytest.fail("回答を受け取る時点の設定を読まない"))
        assert gate.take_reply(st, tid, {})["status"] == "ok"
    return tid


@pytest.mark.parametrize(
    ("found", "want"),
    [
        (_strategy("none"), ("none", ROW)),
        (_strategy("plugin_json"), ("versioned", None)),
        (command.FactUnknown("読めない"), ("unknown", None)),
    ],
)
def test_the_organizer_records_the_delivery_from_the_directory_it_released_from(monkeypatch, tmp_path, found, want):
    # ndf lanes release は打った作業ディレクトリの設定から決める（Git の保存先の位置から設定の場所を逆算しない）
    seen = []

    def strategy(cwd):
        seen.append(cwd)
        if isinstance(found, Exception):
            raise found
        return found

    monkeypatch.setattr(command, "strategy", strategy)
    a = argparse.Namespace(result=plan.RELEASED)
    plan_cli._read_delivered(a, tmp_path)
    assert a.delivered == want and seen == [tmp_path]


def test_the_organizers_approval_records_the_delivery_and_the_store_kind_without_a_version_number(st, lane_sprint, flow, monkeypatch):
    tid = _organizer_released(st, lane_sprint, flow, monkeypatch, _strategy("none"))
    v = run.read(st, tid)
    out = v.events[-1].draft.payload
    assert out["outputs"]["product_version"] == DIST and out["outputs"]["delivery"] == ROW and out["release_store"] == "none"
    assert run.released_answer(run.StageAnswers(v, "デプロイ")) == worker_kinds.Recorded(DIST, ROW, "none")


@pytest.mark.parametrize(
    ("strategy", "store_kind"),
    [
        (_strategy("plugin_json"), "versioned"),
        (_strategy("none", rows=()), "none"),
        (_strategy("none", rows=(ROW, {**ROW, "target": "別"})), "none"),
        (_strategy("none", rows=({**ROW, "target": ""},)), "none"),
        (branch_strategy.StrategyError("読めない"), "unknown"),
    ],
)
def test_the_delivery_is_not_recorded_when_it_is_not_one_row(st, lane_sprint, flow, monkeypatch, strategy, store_kind):
    # バージョン番号を持つ・本番の行が 0 か 2 つ・形に合わない・設定を読めない、のどれでも回答は書かれ、届け先だけが無い
    tid = _organizer_released(st, lane_sprint, flow, monkeypatch, strategy, "11.0.0" if store_kind == "versioned" else DIST)
    out = run.read(st, tid).events[-1].draft.payload
    assert "delivery" not in out["outputs"] and out["release_store"] == store_kind


def _deploy_env(flow_env_values, capsys, launched, ran):
    """デプロイの工程のステップ release を、記録済みの値を書いた values.json と ndf answer work で流す偽の作業（worker は起動しない）。"""

    def invoker(r, cwd, work_dir, answer):
        if r.step != "release":
            return gate_lanes.ok_result(r, flow_env_values)
        work_dir.mkdir(parents=True, exist_ok=True)
        ran.append(r.released())
        worker_kinds.write_values(work_dir, "release", {"repo": str(cwd)}, r.inputs, True, r.released())
        code = entry._cmd_work(["--state-dir", str(work_dir), "release"], lambda exe, argv: launched.append(argv))
        out = last_result(capsys.readouterr().out)
        assert code == 0 and out["status"] == "ok", out
        return invoke.finished(r, result.result("plan", "ok", "release を終えた", outputs=out["outputs"]))

    return invoker


def test_the_deploy_stage_ends_with_the_recorded_value_and_launches_no_worker(st, lane_sprint, flow, monkeypatch, capsys, env):
    # #4513 の受け入れ条件 1・2: 判断の求めで止まらずに工程が終わり、product_version が記録した値のまま。worker を起動しない
    tid = _organizer_released(st, lane_sprint, flow, monkeypatch, _strategy("none"))
    monkeypatch.setattr(entry, "_strategy_at", lambda _w: pytest.fail("承認の時点の種類があるので作業の時点の設定を読まない"))
    launched: list = []
    ran: list = []
    values = gate_lanes.values(flow.repo, "sprint/s1")
    deploy = run.Env(**{**env.__dict__, "invoker": _deploy_env(values, capsys, launched, ran)})
    for _ in range(4):
        flow.turn(tid, deploy)
    v = run.read(st, tid)
    assert launched == [] and ran == [worker_kinds.Recorded(DIST, ROW, "none")]
    assert v.context()["product_version"] == DIST and v.context()["delivery"] == ROW
    assert any(e.draft.kind == "finished" and e.draft.row_key == "デプロイ" for e in v.events)


def test_the_released_reader_is_called_once_per_step(st, lane_sprint, flow, monkeypatch):
    # 記録済みの値を求める走査は 1 回のプランの呼び出しで 1 回だけ（prepare と plan_path の両方が呼んでも）
    tid = _organizer_released(st, lane_sprint, flow, monkeypatch, _strategy("none"))
    calls = []
    real = run.released_answer
    monkeypatch.setattr(run, "released_answer", lambda answers: calls.append(1) or real(answers))
    reader = run._released_reader(run.StageAnswers(run.read(st, tid), "デプロイ"))
    assert reader() == reader() == worker_kinds.Recorded(DIST, ROW, "none") and calls == [1]


def test_an_approval_without_the_value_fails_with_the_names_and_is_not_relaunched(st, new_table, flow, env):
    # #4513 の受け入れ条件 6・7: 承認の後に値が足りなければ、名前と worker の理由を示して失敗し、next で打ち直さない
    tid = new_table()
    q = _at_release(st, tid, flow)
    gate.answer(st, tid, gate.Answer(q.id, "approved", "出してよい", BY, "k-release"))
    calls: list = []
    waited = "ステップ release が承認ゲートで止まった: 作業 release の worker が判断を求めた: バージョンを返せない"
    values = gate_lanes.values(flow.repo, "sprint/s1")

    def invoker(r, cwd, work_dir, answer):
        if r.step != "release":
            return gate_lanes.ok_result(r, values)
        calls.append(r.step)
        return invoke.finished(r, result.result("plan", "ok", "最後の承認ゲートが承認された", metrics={"waited": waited}))

    deploy = run.Env(**{**env.__dict__, "invoker": invoker})
    for _ in range(3):
        flow.turn(tid, deploy)
    (failed,) = [e for e in run.read(st, tid).events if e.draft.kind == "failed"]
    detail = failed.draft.payload["cause"]["detail"]
    assert "足りない出力: product_version" in detail and "バージョンを返せない" in detail and "ndf progress retry" in detail
    flow.turn(tid, deploy)
    assert calls == ["release"]


@pytest.mark.parametrize(
    ("reply", "want"),
    [
        ({"store": "none", "delivery": ROW}, ("none", ROW)),
        ({"store": "none", "delivery": {**ROW, "channel": "base"}}, ("none", None)),
        ({"store": "versioned", "delivery": ROW}, ("versioned", None)),
        ({"store": "どれでもない", "delivery": ROW}, (None, None)),
        ({"delivery": ROW}, (None, None)),
    ],
)
def test_the_recorded_delivery_is_kept_only_when_it_reaches_production(reply, want):
    # 外のオーガナイザーがベースブランチ向けの行を書いても、リリースの実績にしない
    assert release._recorded_where({"result": "released", **reply}) == want
