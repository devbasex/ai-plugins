"""承認ゲートの結果の差し替え・承認資料・答えを読む前の対象の見直し（#3295。3364-2・3390-2・#2444・#2554）。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ndf.adapters.github import GitHubError
from ndf.contracts import result
from ndf.progress import gate, invoke, model, run

BY = "service:factory"


def _asked(st, repo, tid, observe):
    v = run.read(st, tid)
    return gate.present(st, tid, repo, run.report(v, run.decide(v)), observe)


def _with_gaps(env):
    """design-review の結果に扱われなかった指摘（欠け）を載せる `Env`。"""

    def invoker(r, cwd, work_dir, answer):
        if r.step == "design-review":
            gaps = [{"kind": "unhandled_finding", "name": "f-1", "reason": "直したとも見送ったとも決めていない"}]
            return invoke.finished(r, result.result("plan", "ok", "レビューを終えた", gaps=gaps))
        return env.invoker(r, cwd, work_dir, answer)

    return run.Env(**{**vars(env), "invoker": invoker})


def test_the_gate_result_of_progress_is_the_question_with_the_material(st, env, repo, new_table, flow):
    tid = new_table()
    flow.turn(tid, _with_gaps(env))
    out = _asked(st, repo, tid, lambda q: gate.Target(head="h1", material=None))
    assert out["status"] == "gate" and result.errors(out, 10) == []
    meta = out["metrics"]["gate"]
    assert meta["kind"] == "design" and meta["head"] == "h1" and out["metrics"]["stage"] == "設計"
    assert out["routes"]["approved"]["argv"][:2] == ["ndf", "answer"]
    text = open(out["presentation_path"], encoding="utf-8").read()
    assert "f-1（直したとも見送ったとも決めていない）" in text and meta["id"] in text
    # 書いた承認資料は書き直さない（答えた者が見た sha256 が変わらない）
    again = _asked(st, repo, tid, gate.github_observe(repo, st.root, lambda: _hub("h1")))
    assert again["metrics"]["gate"]["material_sha256"] == gate.material_sha256(Path(out["presentation_path"]))


def test_a_result_that_is_not_a_listen_gate_is_left_as_it_is(st, repo, new_table):
    tid = new_table()
    v = run.read(st, tid)
    out = run.report(v, run.decide(v))
    assert gate.present(st, tid, repo, out, gate.no_observe) is out


def _hub(head):
    return SimpleNamespace(pull=lambda n: SimpleNamespace(head_sha=head))


def _answered(st, tid, head):
    q = gate.waiting(run.read(st, tid))
    assert q is not None
    gate.answer(st, tid, gate.Answer(q.id, "approved", "見た", BY, "k-1", seen=gate.Target(head=head)))
    return q


def test_an_answer_whose_target_moved_before_it_is_read_goes_back_to_waiting(st, new_table, flow):
    tid = new_table()
    flow.turn(tid)
    old = _answered(st, tid, "h1")
    assert gate.recheck(st, tid, lambda q: gate.Target(head="h1")) is None
    assert run.decide(run.read(st, tid)).action == run.Action.CONSUME
    why = gate.recheck(st, tid, lambda q: gate.Target(head="h2"))
    assert why is not None and "h2" in why
    new = gate.waiting(run.read(st, tid))
    assert new is not None and new.id != old.id and new.stage == "設計"


def test_github_observe_reads_the_head_and_refuses_when_it_cannot(st, repo, new_table, flow):
    tid = new_table()
    flow.turn(tid)
    q = gate.waiting(run.read(st, tid))
    assert q is not None and q.pr == 7
    assert gate.github_observe(repo, st.root, lambda: _hub("abc"))(q) == gate.Target(head="abc")

    def broken():
        def pull(n):
            raise GitHubError("届かない", unreached=True)

        return SimpleNamespace(pull=pull)

    with pytest.raises(model.Refused, match="head を読めない"):
        gate.github_observe(repo, st.root, broken)(q)
