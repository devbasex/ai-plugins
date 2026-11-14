"""承認ゲートの問い・答えごとの行き先・答えの証拠（#3295。3364-2・3390-2・決定 99・決定 17）。"""

from __future__ import annotations

import json

import pytest

from ndf import cli
from ndf.contracts import result
from ndf.progress import gate, lane, model, run, store

BY, KEY = "service:factory", "k-1"


def _waiting(st, tid, **kw):
    q = gate.waiting(run.read(st, tid), **kw)
    assert q is not None
    return q


def _give(st, tid, q, value="approved", **kw):
    kw.setdefault("key", KEY)
    a = gate.Answer(q.id, value, "人が答えた", BY, kw.pop("key"), **kw)
    return gate.answer(st, tid, a)


def test_the_gate_result_carries_the_kind_the_pr_url_and_a_route_per_answer(st, new_table, flow):
    tid = new_table()
    assert flow.turn(tid)["status"] == "gate"
    q = _waiting(st, tid, repo="o/r", target=gate.Target(head="abc"))
    out = gate.gate_result(q)
    assert result.errors(out, 10) == [] and result.code_for(out) == 10
    meta = out["metrics"]["gate"]
    assert meta["kind"] == "design" and meta["pr"] == 7 and meta["pr_url"] == "https://github.com/o/r/pull/7"
    assert set(out["routes"]) == {"approved", "rejected", "revise"}
    argv = out["routes"]["approved"]["argv"]
    assert argv[:3] == ["ndf", "answer", tid] and argv[argv.index("--gate") + 1] == q.id and "abc" in argv
    assert "drop" in out["routes"]["approved"]["ask"] and "notes" in out["routes"]["revise"]["ask"]


def test_the_answer_keeps_who_answered_the_key_and_what_was_seen_and_is_idempotent(st, new_table, flow):
    tid = new_table()
    flow.turn(tid)
    q = _waiting(st, tid)
    first = _give(st, tid, q, seen=gate.Target(head="abc"))
    assert first["status"] == "ok" and first["next"]["argv"] == ["ndf", "progress", "next", tid]
    ev = [e for e in run.read(st, tid).events if e.draft.kind == "answered"][-1]
    p = ev.draft.payload
    assert ev.draft.actor == BY and p["key"] == KEY and p["gate_id"] == q.id and p["seen"]["head"] == "abc" and p["gate"] == "design"
    again = _give(st, tid, q)
    assert again["metrics"]["replayed"] is True and again["metrics"]["seqs"] == first["metrics"]["seqs"]
    with pytest.raises(model.Refused, match="別の答え"):
        _give(st, tid, q, "rejected")


def test_answers_before_the_gate_or_to_another_question_are_refused(st, new_table, flow):
    tid = new_table()
    with pytest.raises(model.Refused, match="待っていない"):
        gate.answer(st, tid, gate.Answer("x", "approved", "早い", BY, KEY))
    flow.turn(tid)
    with pytest.raises(model.Refused, match="今の問い"):
        gate.answer(st, tid, gate.Answer("0" * 16, "approved", "違う問い", BY, KEY))
    with pytest.raises(model.Refused, match="形が違う"):
        gate.answer(st, tid, gate.Answer(_waiting(st, tid).id, "approved", "誰か", "someone", KEY))


def test_an_answer_without_the_question_id_is_refused_at_the_gate(st, new_table, flow):
    tid = new_table()
    flow.turn(tid)
    q = _waiting(st, tid)
    with pytest.raises(model.Refused, match="ndf answer"):
        run.answer(st, tid, q.stage, "approved", "問いの id が無い", None)
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
    notes.write_text("節 2 の根拠が足りない")
    _give(st, tid, old, "revise", notes=notes)
    assert flow.turn(tid)["status"] == "gate"
    new = _waiting(st, tid)
    assert new.id != old.id
    with pytest.raises(model.Refused, match="今の問い"):
        _give(st, tid, old, key="k-2")
    assert gate.rejections(run.read(st, tid).events) == {"design": 1}


def test_drop_is_only_for_the_design_gate_inside_a_lane(st, new_table, flow):
    tid = new_table()
    flow.turn(tid)
    with pytest.raises(model.Refused, match="レーンの行が無く"):
        _give(st, tid, _waiting(st, tid), drop=(3092,))
    with pytest.raises(model.Refused, match="設計承認ゲートの承認だけ"):
        _give(st, tid, _waiting(st, tid), "rejected", drop=(3092,))


def _lane_rows(st, pid):
    with store.transaction(st) as conn:
        return lane.read_view(conn, pid)[1]["a"]


def test_dropped_issues_go_to_the_next_row_of_the_same_lane(st, lane_sprint, flow):
    # 決定 99: 設計承認ゲートで外した課題は同じレーンの順番表の次の行へ入り、レーンは増えない
    pid, tid = lane_sprint()
    flow.turn(tid)
    out = _give(st, tid, _waiting(st, tid), drop=(3092,))
    rows = _lane_rows(st, pid)
    assert [r.key for r in rows][1] == out["metrics"]["carried_row"] and sorted(rows[1].issues()) == [3092]
    assert len(rows) == 3


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
    assert result.errors(out, 10) == [] and set(out["routes"]) == {"approved", "rejected"}
    assert cli.COMMANDS["answer"] == cli.COMMANDS["progress"] == "ndf.progress.gate"
    assert gate.TOOL == "answer" and {"next", "wait", "work", "new", "show"} <= gate.progress_verbs()
    assert gate.main(["ask", "--gate", "release"]) == 10
    assert json.loads(capsys.readouterr().out)["metrics"]["gate"]["kind"] == "release"


def test_answers_are_copied_to_the_gate_rejections_file_for_the_service(st, new_table, flow, tmp_path, monkeypatch):
    # #3136: 正本は事象 answered で、サービスが読む gate-rejections.jsonl へ写す
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
    # #2934・#3174: 答えの選択肢と指摘を分け、答えた者と時刻を持つ
    tid = new_table()
    flow.turn(tid)
    notes = tmp_path / "notes.md"
    notes.write_text("節 2 の根拠が足りない")
    _give(st, tid, _waiting(st, tid), "revise", notes=notes)
    (got,) = gate.answers(run.read(st, tid))
    assert got["answer"] == "revise" and got["notes"] == "節 2 の根拠が足りない" and got["reason"] == "人が答えた"
    assert got["by"] == BY and got["at"] and got["gate"] == "design" and got["stage"] == "設計"
