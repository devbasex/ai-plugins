"""承認ゲートの質問・回答ごとのルート・回答の証拠（#3295。3364-2・3390-2・決定 99・決定 17）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from ndf import cli, identity
from ndf.contracts import result
from ndf.progress import gate, invoke, lane, model, run, store


BY, KEY = "service:factory", "k-1"


def _waiting(st, tid, **kw):
    q = gate.waiting(run.read(st, tid), **kw)
    assert q is not None
    return q


def _give(st, tid, q, value="approved", **kw):
    kw.setdefault("key", KEY)
    a = gate.Answer(q.id, value, "人が回答した", BY, kw.pop("key"), **kw)
    return gate.answer(st, tid, a)


def test_the_gate_result_carries_the_kind_the_pr_url_and_a_route_per_answer(st, new_table, flow):
    tid = new_table()
    assert flow.turn(tid)["status"] == "gate"
    q = _waiting(st, tid, repo="o/r", target=gate.Target(head="abc"))
    out = gate.gate_result(q)
    assert result.errors(out, 10) == [] and result.code_for(out) == 10
    meta = out["metrics"]["gate"]
    assert meta["kind"] == "design" and meta["pr"] == 7 and meta["pr_url"] == "https://github.com/o/r/pull/7"
    assert set(out["routes"]) == {"approved", "rejected", "request_changes"}
    argv = out["routes"]["approved"]["argv"]
    assert argv[:3] == ["ndf", "answer", tid] and argv[argv.index("--gate") + 1] == q.id and "abc" in argv
    assert "drop" in out["routes"]["approved"]["ask"] and "notes" in out["routes"]["request_changes"]["ask"]


def test_the_answer_keeps_who_answered_the_key_and_what_was_seen_and_is_idempotent(st, new_table, flow):
    tid = new_table()
    flow.turn(tid)
    q = _waiting(st, tid)
    first = _give(st, tid, q, seen=gate.Target(head="abc"))
    assert first["status"] == "ok" and first["next"]["argv"] == ["ndf", "progress", "next", tid]
    ev = [e for e in run.read(st, tid).events if e.draft.kind == "answered"][-1]
    p = ev.draft.payload
    # 事象の書き手は回答を書いた conductor で、回答した者は payload の by に残る（run.answer_draft）
    assert (
        ev.draft.actor == run.CONDUCTOR
        and p["by"] == BY
        and p["key"] == KEY
        and p["gate_id"] == q.id
        and p["seen"]["head"] == "abc"
        and p["gate"] == "design"
    )
    again = _give(st, tid, q)
    assert again["metrics"]["replayed"] is True and again["metrics"]["seqs"] == first["metrics"]["seqs"]
    with pytest.raises(model.Refused, match="別の回答"):
        _give(st, tid, q, "rejected")


def test_answers_before_the_gate_or_to_another_question_are_refused(st, new_table, flow):
    tid = new_table()
    with pytest.raises(model.Refused, match="待っていない"):
        gate.answer(st, tid, gate.Answer("x", "approved", "早い", BY, KEY))
    flow.turn(tid)
    with pytest.raises(model.Refused, match="今の質問"):
        gate.answer(st, tid, gate.Answer("0" * 16, "approved", "違う質問", BY, KEY))
    with pytest.raises(model.Refused, match="形が違う"):
        gate.answer(st, tid, gate.Answer(_waiting(st, tid).id, "approved", "誰か", "someone", KEY))


def test_an_answer_without_the_question_id_is_refused_at_the_gate(st, new_table, flow):
    tid = new_table()
    flow.turn(tid)
    q = _waiting(st, tid)
    with pytest.raises(model.Refused, match="ndf answer"):
        run.answer(st, tid, q.stage, "approved", "質問の id が無い", None)
    assert gate.waiting(run.read(st, tid)) == q


def test_an_answer_to_a_changed_target_is_refused_and_the_row_keeps_waiting(st, new_table, flow):
    tid = new_table()
    flow.turn(tid)
    q = _waiting(st, tid)
    a = gate.Answer(q.id, "approved", "古い head を見た", BY, KEY, seen=gate.Target(head="old"))
    with pytest.raises(model.Refused, match="対象が変わった"):
        gate.answer(st, tid, a, observe=lambda _q: gate.Target(head="new"))
    assert run.decide(run.read(st, tid)).action == run.Action.GATE


def test_a_revised_stage_asks_a_new_question_and_the_rejections_are_counted(st, new_table, flow, tmp_path):
    tid = new_table()
    flow.turn(tid)
    old = _waiting(st, tid)
    notes = tmp_path / "notes.md"
    notes.write_text("セクション 2 の根拠が足りない")
    _give(st, tid, old, "request_changes", notes=notes)
    assert flow.turn(tid)["status"] == "gate"
    new = _waiting(st, tid)
    assert new.id != old.id
    with pytest.raises(model.Refused, match="今の質問"):
        _give(st, tid, old, key="k-2")
    assert gate.rejections(run.read(st, tid).events) == {"design": 1}


def test_drop_is_only_for_the_design_gate_inside_a_lane(st, new_table, flow):
    tid = new_table("--issues", "3092", "5")
    flow.turn(tid)
    with pytest.raises(model.Refused, match="レーンの行が無く"):
        _give(st, tid, _waiting(st, tid), drop=(3092,))
    with pytest.raises(model.Refused, match="設計承認ゲートの承認だけ"):
        _give(st, tid, _waiting(st, tid), "rejected", drop=(3092,))
    with pytest.raises(model.Refused, match="このスプリントの課題"):
        _give(st, tid, _waiting(st, tid), drop=(9,))
    with pytest.raises(model.Refused, match="すべて外す"):
        _give(st, tid, _waiting(st, tid), drop=(3092, 5))


def _lane_rows(st, pid):
    with store.transaction(st) as conn:
        return lane.read_view(conn, pid)[1]["a"]


def test_dropped_issues_go_to_the_next_row_of_the_same_lane(st, lane_sprint, flow):
    # 決定 99: 設計承認ゲートで外した課題は同じレーンの順番表の次の行へ入り、レーンは増えない
    pid, tid = lane_sprint("--issues", "3092", "5")
    flow.turn(tid)
    out = _give(st, tid, _waiting(st, tid), drop=(3092,))
    rows = _lane_rows(st, pid)
    assert [r.key for r in rows][1] == out["metrics"]["carried_row"] and sorted(rows[1].issues()) == [3092]
    assert len(rows) == 3
    # 外した課題は今のスプリントの課題から除かれ、後の工程は残った課題だけを読む
    assert run.read(st, tid).context()["issues"] == [5]


def test_an_answer_sent_back_to_waiting_restores_the_dropped_issues(st, lane_sprint, flow):
    # 回答した後に対象が変わって待ちへ戻すと、外した課題はスプリントへ戻り、入れた行は取り消され、回答し直しで同じ課題を外せる
    pid, tid = lane_sprint("--issues", "3092", "5")
    flow.turn(tid)
    out = _give(st, tid, _waiting(st, tid), drop=(3092,), seen=gate.Target(head="h1"))
    assert gate.recheck(st, tid, lambda q, **_: gate.Target(head="h2")) is not None
    assert run.read(st, tid).context()["issues"] == [3092, 5]
    flow.turn(tid)  # 未了に戻した読み手役のステップを打ち直してから待つ（#4446）
    carried = next(r for r in _lane_rows(st, pid) if r.key == out["metrics"]["carried_row"])
    assert carried.state == lane.CANCELLED
    again = _give(st, tid, _waiting(st, tid), drop=(3092,), key="k-2", seen=gate.Target(head="h2"))
    assert run.read(st, tid).context()["issues"] == [5] and again["metrics"]["carried_row"] != carried.key


def _readings(st, tid) -> int:
    """設計の工程で読み手役のステップが済んだ回数（未了に戻した後に打ち直すと増える）。"""
    events = run.read(st, tid).events
    return sum(
        1
        for e in events
        if e.draft.kind == "step_recorded" and e.draft.payload["step"] == "design-reading" and e.draft.payload["status"] == "finished"
    )


def test_reread_asks_again_with_a_new_question_after_the_reading_step(st, env, new_table, flow):
    # #4446 の待ちの出し直し: 新しい質問の id で待ちを書き直し、読み手役のステップを打ち直す。待っていなければ断り、
    # 読み手役が文書を直して push したら（review_again）待ちの知らせを返さずに設計のレビューから流し直す
    tid = new_table()
    assert gate.reread(st, tid, flow.repo, env)["cause"]["kind"] == "precondition"
    flow.turn(tid)
    before = _waiting(st, tid)
    out = gate.reread(st, tid, flow.repo, env)
    after = _waiting(st, tid)
    assert out["status"] == "gate" and after.id != before.id and _readings(st, tid) == 2
    pushed = "c" * 40
    again = {"commit": pushed, "docs": ["issues/issue-3092-design.md"]}
    fixed = result.result("script", "ok", "文書を直した", outputs={"review_again": again})
    review = run.Env(
        **{
            **vars(env),
            "invoker": lambda r, cwd, wd, a: invoke.finished(r, fixed) if r.step == "design-reading" else env.invoker(r, cwd, wd, a),
        }
    )
    out = gate.reread(st, tid, flow.repo, review)
    assert out["status"] == "ok" and out["next"]["argv"][2] == "wait" and gate.waiting(run.read(st, tid)) is None
    assert run.decide(run.read(st, tid)).step.id == "design-review"


def test_the_sprint_completion_ends_the_lane_row(st, env, lane_sprint, flow):
    # 決定 17: スプリントの完了と同じトランザクションでレーンの行を終える
    pid, tid = lane_sprint()
    with_lane = gate.lane_env(env)
    flow.turn(tid, with_lane)
    _give(st, tid, _waiting(st, tid))
    assert flow.turn(tid, with_lane)["metrics"]["stage"] == "デプロイ"
    _give(st, tid, _waiting(st, tid), key="k-2")
    assert flow.turn(tid, with_lane)["metrics"]["state"] == "completed"
    assert _lane_rows(st, pid)[0].state == lane.COMPLETED


def test_ask_waits_for_the_plan_gate_and_the_entry_is_on_the_command_table(capsys):
    out = gate.ask("design")
    assert result.errors(out, 10) == [] and out["routes"] == {"approved": None, "rejected": None}
    assert cli.COMMANDS["answer"] == "ndf.progress.gate" and cli.COMMANDS["progress"] == "ndf.progress.gate.progress_cli"
    assert gate.TOOL == "answer" and cli.tool_of(cli.COMMANDS["progress"]) == "progress"
    assert gate.main(["ask", "--gate", "release"]) == 10
    assert json.loads(capsys.readouterr().out)["metrics"]["gate"]["kind"] == "release"


def test_answers_are_copied_to_the_gate_rejections_file_for_the_service(st, new_table, flow, tmp_path, monkeypatch):
    # #3136: SSOT は事象 answered で、サービスが読む gate-rejections.jsonl へ写す
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv"))
    tid = new_table()
    flow.turn(tid)
    q = _waiting(st, tid)
    _give(st, tid, q, "rejected")
    rows = [json.loads(line) for line in (tmp_path / "mvv" / "gate-rejections.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["gate"] == "design" and rows[0]["outcome"] == "rejected" and rows[0]["gate_id"] == q.id
    assert rows[0]["sprint"] == tid and rows[0]["issues"] == [3092] and rows[0]["by"] == BY and rows[0]["prs"] == [7]


def test_an_unwritable_gate_rejections_file_is_a_warning_and_the_answer_stays(st, new_table, flow, tmp_path, monkeypatch):
    (tmp_path / "file").write_text("")
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "file"))
    tid = new_table()
    flow.turn(tid)
    out = _give(st, tid, _waiting(st, tid))
    assert out["status"] == "ok" and any("gate-rejections" in w for w in out["warnings"])
    assert run.decide(run.read(st, tid)).action == run.Action.CONSUME


def test_the_conductor_reads_the_choice_the_notes_who_and_when(st, new_table, flow, tmp_path):
    # #2934・#3174: 回答の選択肢と指摘を分け、回答した者と時刻を持つ
    tid = new_table()
    flow.turn(tid)
    notes = tmp_path / "notes.md"
    notes.write_text("セクション 2 の根拠が足りない")
    _give(st, tid, _waiting(st, tid), "request_changes", notes=notes)
    (got,) = gate.answers(run.read(st, tid))
    assert got["answer"] == "request_changes" and got["notes"] == "セクション 2 の根拠が足りない" and got["reason"] == "人が回答した"
    assert got["by"] == BY and got["at"] and got["gate"] == "design" and got["stage"] == "設計"


# --- 現状固定: rejections_path の保存先（I-001。mvv-state の解決を mvv.record_path と共有する前） ------------------


def _unresolvable_identity():
    raise identity.IdentityError("状態の保存先を解けない")


@pytest.mark.parametrize("env", ["set", None, ""])
def test_current_rejections_path_uses_the_environment_or_falls_back_to_the_state_dir(tmp_path, monkeypatch, env):
    if env != "set":
        if env is None:
            monkeypatch.delenv("NDF_MVV_STATE_DIR", raising=False)
        else:
            monkeypatch.setenv("NDF_MVV_STATE_DIR", env)
        assert gate.rejections_path() == identity.load().state_dir / "gate-rejections.jsonl"
        return
    # 環境変数があれば identity を読まずに使う
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "m"))
    monkeypatch.setattr(identity, "load", _unresolvable_identity)
    assert gate.rejections_path() == tmp_path / "m" / "gate-rejections.jsonl"
    monkeypatch.setenv("NDF_MVV_STATE_DIR", "rel")  # 相対のパスは解決せずそのまま使う
    assert str(gate.rejections_path()) == "rel/gate-rejections.jsonl"
    assert not (tmp_path / "m").exists()  # パスを返すだけで、ディレクトリは作らない


def test_current_rejections_path_raises_the_identity_error_and_the_copy_turns_it_into_a_warning(st, new_table, flow, monkeypatch):
    tid = new_table()
    flow.turn(tid)
    v = run.read(st, tid)
    q = _waiting(st, tid)
    monkeypatch.delenv("NDF_MVV_STATE_DIR", raising=False)
    a = gate.Answer(q.id, "approved", "人が回答した", BY, KEY)
    with monkeypatch.context() as m:  # フィクスチャの後始末も identity を読むので、差し替えはここだけにする
        m.setattr(identity, "load", _unresolvable_identity)
        with pytest.raises(identity.IdentityError, match="状態の保存先を解けない"):
            gate.rejections_path()
        (warning,) = gate.record_answer(v, q, a, gate.rejections_path)
    assert "gate-rejections" in warning and "状態の保存先を解けない" in warning


# --- 現状固定: gate.rejections（I-014。承認しなかった承認ゲートの回答を区分ごとに数える） -------------------


def _event(seq, event_kind, **payload):
    at = datetime(2026, 11, 14, tzinfo=UTC)
    return model.Event(seq, at, model.EventDraft(event_kind, "conductor", "設計", payload=payload))


def test_current_rejections_count_the_unapproved_listen_answers_per_gate_in_first_seen_order():
    events = [
        _event(1, run.ANSWERED, answer=run.REQUEST_CHANGES, kind=invoke.LISTEN, gate="release"),
        _event(2, run.ANSWERED, answer=run.REJECTED, kind=invoke.LISTEN),
        _event(3, run.ANSWERED, answer=run.REQUEST_CHANGES, kind=invoke.LISTEN, gate="release"),
        _event(4, run.ANSWERED, answer=run.APPROVED, kind=invoke.LISTEN, gate="design"),
        _event(5, run.ANSWERED, answer=run.REJECTED, kind=invoke.LISTEN, gate=None),
        _event(6, run.ANSWERED, answer=run.REJECTED, kind=invoke.LISTEN, gate="design"),
        _event(7, run.ANSWERED, answer=run.REQUEST_CHANGES, kind="plan", gate="release"),  # プランの中の承認ゲートの回答は数えない
        _event(8, run.STARTED, answer=run.REJECTED, kind=invoke.LISTEN, gate="design"),  # 回答の事象でなければ数えない
    ]
    counted = gate.rejections(events)
    assert counted == {"release": 2, "plan": 2, "design": 1}
    assert list(counted) == ["release", "plan", "design"] and type(counted) is dict
    assert gate.rejections([]) == {}


# --- 現状固定: gate.main の ndf answer のサブコマンド（I-017。ask・list・head の振り分け） ------------------------


def _last_json(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_current_main_routes_ask_list_and_head_to_their_own_parsers(repo, st, new_table, capsys):
    assert gate.main(["ask", "--gate", "release"]) == 10
    assert _last_json(capsys)["status"] == "gate"
    assert gate.main(["ask", "--gate", "nope"]) == 2
    assert "ndf answer ask: error" in capsys.readouterr().err
    tid = new_table()
    assert gate.main(["list", tid]) == 0
    listed = _last_json(capsys)
    assert listed["items"] == [] and listed["metrics"] == {"machine_id": tid}
    assert gate.main(["head", tid]) != 0
    assert _last_json(capsys)["status"] == "stopped"
    assert gate.main(["head"]) == 2
    assert "ndf answer head: error" in capsys.readouterr().err
