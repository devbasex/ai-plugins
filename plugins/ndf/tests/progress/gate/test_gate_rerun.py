"""承認ゲートで終わった後（#2730）: 承認の記録の名前は 1 つの表から引き、承認ゲートの結果は後段へ流さず、回答の後は再生せず進む。"""

from __future__ import annotations


from ndf.contracts import result
from ndf.progress import gate, model, run


BY = "human:reviewer"


def test_a_gate_result_stops_the_pipeline_with_its_own_exit_code(st, new_table, release_question):
    tid = new_table()
    q = release_question(tid)
    out = gate.gate_result(q)
    # 承認ゲートで終わった結果は ok の 0 でなく承認ゲートの終了コードを返すので、`&&` の後段へ流れない
    assert result.code_for(out) == 10 and result.code_for(out) != result.code_for(result.result(gate.TOOL, result.OK, "済んだ"))


def test_the_answer_is_recorded_under_the_gate_name_of_the_table(st, new_table, release_question):
    tid = new_table()
    q = release_question(tid)
    gate.answer(st, tid, gate.Answer(q.id, "approved", "出してよい", BY, "k-release", seen=gate.Target(head="h1")))
    last = gate.answers(run.read(st, tid))[-1]
    assert last["gate"] == "release" and last["gate"] in model.table()["gates"] and last["gate_id"] == q.id


def test_after_the_answer_the_table_moves_on_instead_of_replaying_the_gate(st, new_table, flow, release_question):
    tid = new_table()
    q = release_question(tid)
    gate.answer(st, tid, gate.Answer(q.id, "approved", "出してよい", BY, "k-release", seen=gate.Target(head="h1")))
    flow.turn(tid)
    after = gate.waiting(run.read(st, tid))
    assert after is None or after.id != q.id
    assert run.decide(run.read(st, tid)).action != run.Action.CONSUME
