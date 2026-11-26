"""設計承認ゲートの PR と工程「設計」の出力 design_pr（#4495・#4455）。

プラン design の結果は、設計 PR を items の `{"kind": "pr", "number": <番号>}` にだけ載せ、outputs に design_pr を書かない
（11.0.43 までの実物の形）。その形でも、質問は設計 PR を持ち、回答は証跡を書くか断り、承認の後に head が動けば待ちへ戻り、
工程「設計」は design_pr を出力に持って終わる。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ndf.adapters.github import FileAt
from ndf.contracts import result
from ndf.progress import gate, invoke, model, run, stages

DESIGN_PR = 4565


def _like_the_plan(env):
    """作業のステップ design の結果を、実物のプラン design の形にする `Env`（PR は items だけ、outputs は gaps と mvv）。"""

    def invoker(r, cwd, work_dir, answer):
        if r.step == "design":
            items = [{"kind": "plan", "name": "design-sprint/s1", "run": "r1"}, {"kind": "pr", "number": DESIGN_PR}]
            out = result.result("plan", "ok", "design-sprint/s1 が終わった", items, outputs={"gaps": [], "mvv": {"state": "absent"}})
            return invoke.finished(r, out)
        got = env.invoker(r, cwd, work_dir, answer)
        if "design_pr" not in got.outputs:
            return got
        # 偽の作業は定義した出力をすべて返す。実物のプランは design_pr を返さないので外す
        outputs = {k: v for k, v in got.outputs.items() if k != "design_pr"}
        report = {**got.report, "outputs": outputs} if outputs else {k: v for k, v in got.report.items() if k != "outputs"}
        return invoke.finished(r, report)

    return run.Env(**{**vars(env), "invoker": invoker})


def _hub(head, production=True):
    """設計 PR（HEAD `head`・main 宛て）とマージ先の設定を読む偽の GitHub。`production` なら main は本番へ反映される。"""
    branches = {"base": "main", "production": "main"} if production else {"base": "develop", "production": "main"}
    config = json.dumps({"branch_strategy": {"branches": branches}})
    return SimpleNamespace(
        repo="o/r",
        pull=lambda n: SimpleNamespace(head_sha=head, base_ref="main" if production else "develop", number=n, body="本文", title="題"),
        branch_file=lambda base, path: FileAt("o/r", config, "c1"),
    )


_FLAGS = {
    "--head": "head",
    "--material-sha256": "material",
    "--body-sha256": "body",
    "--title-sha256": "title",
    "--presentation": "presentation",
}


def _seen(shown):
    """提示した結果の承認の route（`ndf answer` の引数）が固定する、回答した者が見た対象。"""
    argv = shown["routes"]["approved"]["argv"]
    return gate.Target(**{_FLAGS[f]: v for f, v in zip(argv, argv[1:]) if f in _FLAGS})


def _approve(st, repo, tid, q, by, rows=None):
    """提示した対象（head h1 の設計 PR）を見て承認する。書いた証跡の行を `rows` へ足す。"""
    post = (lambda row: rows.append(row) or True) if rows is not None else (lambda row: True)
    a = gate.Answer(q.id, "approved", "設計を承認", by, "k-1", seen=_seen(q.shown))
    return gate.answer(st, tid, a, gate.github_observe(repo, st.root, lambda: _hub("h1")), post)


@pytest.fixture
def design_gate(st, env, repo, new_table, flow):
    """実物の形の結果で設計承認ゲートまで流し、（ステートマシンの ID, 質問）を返す。"""
    tid = new_table()
    flow.turn(tid, _like_the_plan(env))
    # `ndf progress next` と同じく、承認の対象（PR・マージ先・head・本番へ反映されるか）を読んで質問を提示する
    v = run.read(st, tid)
    shown = gate.present(st, tid, repo, run.report(v, run.decide(v)), gate.github_observe(repo, st.root, lambda: _hub("h1")))
    meta = shown["metrics"]["gate"]
    assert (meta["pr"], meta["head"]) == (DESIGN_PR, "h1"), meta
    q = gate.waiting(run.read(st, tid), repo="o/r")
    assert q is not None and q.gate == "design"
    return tid, SimpleNamespace(id=q.id, pr=q.pr, pr_url=q.pr_url, gate=q.gate, shown=shown)


def test_the_design_gate_question_carries_the_pr_of_the_plan_items(design_gate):
    _, q = design_gate
    assert q.pr == DESIGN_PR and q.pr_url == f"https://github.com/o/r/pull/{DESIGN_PR}"


def test_a_human_approval_of_a_production_merge_writes_the_trail(st, repo, design_gate):
    tid, q = design_gate
    rows = []
    _approve(st, repo, tid, q, "human:reviewer", rows)
    assert len(rows) == 1
    assert (rows[0].by, rows[0].production, rows[0].target) == ("user", True, ("o/r", DESIGN_PR, "main", "h1"))


def test_an_organizer_approval_of_a_production_merge_is_refused(st, repo, design_gate):
    tid, q = design_gate
    rows = []
    with pytest.raises(model.Refused, match="証跡にならない"):
        _approve(st, repo, tid, q, "organizer", rows)
    assert rows == []
    assert gate.waiting(run.read(st, tid)) is not None


def test_a_push_after_the_approval_sends_the_gate_back_to_waiting(st, env, repo, flow, design_gate):
    tid, q = design_gate
    _approve(st, repo, tid, q, "human:reviewer")
    why = gate.recheck(st, tid, gate.github_observe(repo, st.root, lambda: _hub("h2")))
    assert why is not None and "head" in why
    flow.turn(tid, _like_the_plan(env))
    again = gate.waiting(run.read(st, tid))
    assert again is not None and again.id != q.id and again.pr == DESIGN_PR


def test_the_design_stage_finishes_with_the_design_pr_of_the_plan_items(st, env, repo, flow, design_gate):
    # #4455: 設計 PR をマージした後に「文脈のキー design_pr が無い」で工程が失敗しない
    tid, q = design_gate
    _approve(st, repo, tid, q, "human:reviewer")
    flow.turn(tid, _like_the_plan(env))
    v = run.read(st, tid)
    assert v.context()["design_pr"] == DESIGN_PR
    assert "設計" not in [p.name for p in v.snap.stages if run.step_progress(v, p.name).failed]


def test_an_approval_recorded_without_the_design_pr_head_goes_back_to_waiting(st, env, repo, new_table, flow):
    # 設計 PR を特定できなかった版の承認（見た head が無い）は、対象に結び付いていないのでマージへ進めず待ちへ戻す
    tid = new_table()
    flow.turn(tid, _like_the_plan(env))
    q = gate.waiting(run.read(st, tid))
    assert q is not None and q.gate == "design"
    gate.answer(st, tid, gate.Answer(q.id, "approved", "設計を承認", "human:reviewer", "k-old"))
    why = gate.recheck(st, tid, gate.github_observe(repo, st.root, lambda: _hub("h1")))
    assert why is not None and "head" in why
    assert gate.waiting(run.read(st, tid)) is not None


def _design_stage(*kinds):
    steps = tuple(stages.StepDef(id=f"s{i}", stage="設計", call="ndf.plan", kind=k, range="none") for i, k in enumerate(kinds))
    return stages.StageDef(id="design", name="設計", phase="design", steps=steps, outputs=("design_pr",))


def _report(prs=(), outputs=None):
    rep = {"items": [{"kind": "pr", "number": n} for n in prs]}
    return {**rep, "outputs": outputs} if outputs is not None else rep


def test_the_design_pr_is_filled_only_from_the_work_step_items():
    stage = _design_stage("work", "review", "merge")
    done = {"s0": _report([DESIGN_PR]), "s1": _report([9001]), "s2": _report([9002])}
    assert stages.step_outputs(stage, done) == {"design_pr": DESIGN_PR}


def test_an_explicit_design_pr_wins_over_the_filled_one():
    stage = _design_stage("work", "work")
    done = {"s0": _report(outputs={"design_pr": 7}), "s1": _report([DESIGN_PR])}
    assert stages.step_outputs(stage, done) == {"design_pr": 7}
