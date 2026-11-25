"""承認ゲートの結果の差し替え・承認資料・回答を読む前の対象の見直し（#3295。3364-2・3390-2・#2444・#2554）。"""

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
    """design-review の結果に扱われなかった指摘（欠け）を載せる `Env`。ok の結果は欠けを outputs.gaps に持つ（結果のコントラクト）。"""

    def invoker(r, cwd, work_dir, answer):
        if r.step == "design-review":
            gaps = [{"kind": "unhandled_finding", "name": "f-1", "reason": "直したとも見送ったとも決めていない"}]
            return invoke.finished(r, result.result("plan", "ok", "レビューを終えた", outputs={"gaps": gaps}))
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
    # 書いた承認資料は書き直さない（回答した者が見た sha256 が変わらない）
    again = _asked(st, repo, tid, gate.github_observe(repo, st.root, lambda: _hub("h1")))
    assert again["metrics"]["gate"]["material_sha256"] == gate.material_sha256(Path(out["presentation_path"]))


def test_a_result_that_is_not_a_listen_gate_is_left_as_it_is(st, repo, new_table):
    tid = new_table()
    v = run.read(st, tid)
    out = run.report(v, run.decide(v))
    assert gate.present(st, tid, repo, out, gate.no_observe) is out


def _hub(head):
    return SimpleNamespace(pull=lambda n: SimpleNamespace(head_sha=head, body="本文", title="題"))


def _readings(st, tid) -> int:
    """設計の工程で読み手役のステップが済んだ回数（未了に戻した後に打ち直すと増える）。"""
    events = run.read(st, tid).events
    done = [e for e in events if e.draft.kind == "step_recorded" and e.draft.payload["status"] == "finished"]
    return sum(1 for e in done if e.draft.payload["step"] == "design-reading")


@pytest.mark.parametrize(
    "now",
    [
        gate.Target("h2", None, "b1", "t1"),  # head が動いた
        gate.Target("h1", None, "b2", "t1"),  # 本文だけが変わった（#4446）
        gate.Target("h1", None, "b1", "t2"),  # 題だけが変わった（#4446）
    ],
)
def test_an_answer_whose_target_moved_before_it_is_read_goes_back_to_waiting(st, new_table, flow, now):
    tid = new_table()
    flow.turn(tid)
    q = gate.waiting(run.read(st, tid))
    assert q is not None and _readings(st, tid) == 1
    seen = gate.Target("h1", None, "b1", "t1")
    gate.answer(st, tid, gate.Answer(q.id, "approved", "見た", BY, "k-1", seen=seen))
    assert gate.recheck(st, tid, lambda _q: seen) is None
    assert run.decide(run.read(st, tid)).action == run.Action.CONSUME
    why = gate.recheck(st, tid, lambda _q: now)
    assert why is not None and "見た値" in why
    # #4446: 待ちへ戻すときに同じトランザクションで読み手役のステップを未了に戻し、次の駆動が今の対象で打ち直してから待つ
    assert "design-reading" not in run.step_progress(run.read(st, tid), "設計").done
    flow.turn(tid)
    new = gate.waiting(run.read(st, tid))
    assert new is not None and new.id != q.id and new.stage == "設計" and _readings(st, tid) == 2
    # MVV 判定で省いた回答は head だけを見るので、本文と題の値が無い見た対象は本文と題で断らない
    assert now.differs(gate.Target(now.head)) == []


def test_the_design_gate_asks_about_the_design_pr_of_the_finished_step(st, repo, new_table, flow):
    # 設計 PR は工程の途中ではステップの記録にしか無い（工程の出力は工程が終わる時に書く）
    tid = new_table()
    flow.turn(tid)
    v = run.read(st, tid)
    assert "design_pr" not in v.context()
    q = gate.waiting(v, repo="o/r")
    assert q is not None and q.pr == 7 and q.pr_url == "https://github.com/o/r/pull/7"


def test_github_observe_reads_the_head_and_refuses_when_it_cannot(st, repo, new_table, flow):
    tid = new_table()
    flow.turn(tid)
    q = gate.waiting(run.read(st, tid))
    assert q is not None and q.pr == 7
    # 同じ 1 回の取得で本文と題の sha256 も読む（#4446）
    sha = gate.Target(head="abc", body=gate.text_sha256("本文"), title=gate.text_sha256("題"))
    assert gate.github_observe(repo, st.root, lambda: _hub("abc"))(q) == sha

    def broken():
        def pull(n):
            raise GitHubError("届かない", unreached=True)

        return SimpleNamespace(pull=pull)

    with pytest.raises(model.Refused, match="head を読めない"):
        gate.github_observe(repo, st.root, broken)(q)
