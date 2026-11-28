"""承認ゲートと承認の記録のつなぎ（issues/issue-3702-design-detail.md の「承認ゲートとのつなぎ」）。

工程「ドキュメントレビュー」が設計承認ゲートを持つ定義は、まだ配る定義に無い（工程の並びの切り替えは後の変更）。
ここでは同じ id とステップの形の定義を組んで、ステートマシンを流す。
"""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import git_sandbox
import pytest

from ndf.adapters.git import Git
from ndf.contracts import result
from ndf.progress import definition, gate, invoke, lane, model, plan, run, run_view, stage_inputs, stages, store
from ndf.progress import design_record as dr

REVIEW, IMPLEMENT = "ドキュメントレビュー", "実装"
PR = 4565
HEAD_STEPS = ["design-pr-review", "units-check", "docs-ci", "design-mvv"]
SKIP_WHY = ["進め方 normal は承認ゲートを MVV 判定で省かない"]


def _snapshot(real: stages.Snapshot) -> stages.Snapshot:
    """設計のステップの形を借りた、工程「ドキュメントレビュー」（設計承認ゲートを持つ）と工程「実装」の定義。"""
    steps = {st.id: st for st in real.steps()}

    def step(sid: str, like: str, stage: str = REVIEW, **change) -> stages.StepDef:
        return dataclasses.replace(steps[like], id=sid, stage=stage, **change)

    review = stages.StageDef(
        id="document-review",
        name=REVIEW,
        phase="design",
        steps=(
            step("design-pr-review", "design-review"),
            step("units-check", "design-mvv"),
            step("docs-ci", "design"),
            step("design-mvv", "design-mvv"),
            step("design-gate", "design-gate"),
            step("design-merge-approved", "design-merge"),
            step("design-sync", "design-mvv"),
        ),
        inputs=("worktree", "branch"),
        optional_inputs=("reviewed", "design_units"),
        input_ref=real.stages[2].input_ref,
        output_ref=real.stages[2].output_ref,
    )
    implement = stages.StageDef(
        id="implement",
        name=IMPLEMENT,
        phase="implement",
        steps=(step("impl", "design", IMPLEMENT),),
        input_ref=real.stages[2].input_ref,
        output_ref=real.stages[2].output_ref,
    )
    return dataclasses.replace(real, stages=(real.stage("作業場所の用意"), review, implement))


def _unit_doc(*issues: int) -> dict:
    tasks = [
        {"id": f"T{i}", "title": "作業", "files": [f"{n}.py"], "lines": 10, "criteria": [], "after": [], "issue": n}
        for i, n in enumerate(issues, 1)
    ]
    units = [{"name": f"単位{issues[0]}", "tasks": [t["id"] for t in tasks], "after": []}]
    return {"version": 1, "issues": list(issues), "tasks": tasks, "units": units}


def _unit_item(name: str, issue: int, order: int) -> dict:
    return {
        "kind": "unit",
        "file": f"issues/issue-{issue}-plan.units.json",
        "name": name,
        "order": order,
        "merge": order,
        "after": [],
        "issues": [issue],
        "tasks": [{"id": "T1", "issue": issue}],
        "criteria": [f"{issue}:1"],
        "lines": 10,
        "touches": [f"{issue}.py"],
    }


@pytest.fixture
def work(repo):
    """単位のファイル（課題 7・8）をコミットしたスプリントの作業場所。HEAD の SHA を持つ。"""
    (repo / "issues").mkdir()
    for n in (7, 8):
        (repo / "issues" / f"issue-{n}-plan.units.json").write_text(json.dumps(_unit_doc(n)), encoding="utf-8")
    git_sandbox.sh(repo, "add", "-A", identity=True)
    git_sandbox.sh(repo, "commit", "-q", "-m", "c", identity=True)
    return Git(repo).run("rev-parse", "HEAD").strip()


class Fake:
    """作業の偽物。ステップごとの outputs を返し、`script[ステップ]` があればその結果を返す。"""

    def __init__(self, repo, head: str, worktree_steps: set[str]) -> None:
        self.worktree_steps = worktree_steps
        self.reviewed = {"repo": "o/r", "pr": PR, "head": head, "base": "main"}
        self.units = {
            "head": head,
            "branch": "sprint/s1",
            "issues": [7, 8],
            "handling": {str(n): {"handling": "実装する", "into": None} for n in (7, 8)},
            "omitted": [],
            "limits": {"target": 400, "max": 1000},
            "criteria": {"7": 1, "8": 1},
            "units": [_unit_item("単位7", 7, 1), _unit_item("単位8", 8, 2)],
        }
        self.mvv = {"gate": "design", "skip": False, "verdict": None, "why": SKIP_WHY}
        self.values = {"worktree": str(repo), "branch": "sprint/s1"}
        self.calls: list[invoke.StageRun] = []
        self.script: dict[str, invoke.StageResult] = {}

    def __call__(self, r, cwd, work_dir, answer):
        if r.step in self.worktree_steps:
            return invoke.finished(r, result.result("plan", "ok", "用意した", outputs={k: self.values[k] for k in r.outputs}))
        self.calls.append(r)
        if r.step in self.script:
            return self.script.pop(r.step)
        outputs = {
            "design-pr-review": {"reviewed": self.reviewed},
            "units-check": {"design_units": self.units},
            "design-mvv": {"mvv_gate": self.mvv},
        }
        out = outputs[r.step] if r.step in outputs else None
        warnings = ["W1 単位8: 目安を超える"] if r.step == "units-check" else None
        return invoke.finished(r, result.result("plan", "ok", f"{r.step} を終えた", outputs=out, warnings=warnings))

    def record(self) -> dict:
        return dr.build(self.reviewed, self.units)


@pytest.fixture
def machine(monkeypatch, repo, work, st, env, lane_sprint, flow):
    """レーンにあるステートマシンを、その定義で作り、偽の作業と合わせて返す。"""
    real = definition.load_definition("standard")
    monkeypatch.setattr(definition, "load_definition", lambda mode: _snapshot(real))
    # 工程の入力の規約への reviewed・design_units の追加は後の変更（工程の並びの切り替え）
    monkeypatch.setattr(definition, "validate_stage_input", lambda stage, obj: [])
    fake = Fake(repo, work, {step.id for step in real.stage("作業場所の用意").steps})
    _, tid = lane_sprint("--issues", "7", "8")
    fenv = run.Env(**{**vars(env), "invoker": fake})
    return SimpleNamespace(tid=tid, fake=fake, env=fenv, head=work, turn=lambda: flow.turn(tid, fenv))


def _observe(m, **change):
    """今の設計 PR を読む口（`change` で head・base などを差し替える）。"""

    def observe(q, *, material=True):
        got = {**m.fake.reviewed, **change}
        return gate.Target(got["head"], None, got["repo"], got["pr"], got["base"], False)

    return observe


def _done(st, tid, stage: str = REVIEW) -> list[str]:
    return list(run.step_progress(run.read(st, tid), stage).done)


def _answers(st, tid) -> list[dict]:
    return [run.event_payload(e) for e in run.read(st, tid).events if e.draft.kind == run.ANSWERED]


def _present(st, tid, repo, observe):
    v = run.read(st, tid)
    return gate.present(st, tid, repo, run.report(v, run.decide(v)), observe)


def _answer(st, tid, observe, value: str = "approved", key: str = "k1", **extra):
    q = gate.waiting(run.read(st, tid))
    seen = observe(q)
    a = gate.Answer(q.id, value, "理由", "human:reviewer", key, seen=gate.Target(seen.head), **extra)
    return gate.answer(st, tid, a, observe)


# --- 承認資料と質問 ------------------------------------------------------------------------------


def test_the_material_has_the_unit_table_the_warnings_and_the_reviewed_pr(st, repo, machine):
    machine.fake.units["omitted"] = [9]
    machine.fake.units["issues"] = [7, 8, 9]
    machine.turn()

    shown = _present(st, machine.tid, repo, _observe(machine))

    assert (shown["status"], shown["metrics"]["gate"]["pr"]) == ("gate", PR)
    text = open(shown["presentation_path"], encoding="utf-8").read()
    for word in ("## レビューの単位", "単位7", "単位8", "W1 単位8: 目安を超える", "実装計画を省いた課題", "#9"):
        assert word in text, word


@pytest.mark.parametrize("change", [{"head": "b" * 40}, {"base": "sprint/x"}, {"pr": 1}])
def test_a_pr_that_moved_after_the_review_is_not_presented(st, repo, machine, change):
    machine.turn()

    shown = _present(st, machine.tid, repo, _observe(machine, **change))

    assert shown["status"] == "ok" and shown["next"]["argv"][1:3] == ["progress", "next"], shown
    assert "presentation_path" not in shown
    assert _done(st, machine.tid) == []
    assert run.decide(run.read(st, machine.tid)).step.id == "design-pr-review"


def test_outputs_that_cannot_make_a_record_are_not_presented(st, repo, machine):
    machine.fake.units["head"] = "b" * 40
    machine.turn()

    shown = _present(st, machine.tid, repo, _observe(machine))

    assert shown["status"] == "ok" and "design_units.head" in shown["summary"]
    assert _done(st, machine.tid) == []


def test_a_pr_that_cannot_be_read_keeps_the_gate_waiting(st, repo, machine):
    machine.turn()

    with pytest.raises(model.Refused):
        _present(st, machine.tid, repo, gate.no_observe)
    with pytest.raises(model.Refused):
        _answer(st, machine.tid, gate.no_observe)

    assert _done(st, machine.tid) == HEAD_STEPS
    assert _answers(st, machine.tid) == []


# --- 回答 ----------------------------------------------------------------------------------------


def test_the_approval_carries_the_record(st, machine):
    machine.turn()

    _answer(st, machine.tid, _observe(machine))

    record = machine.fake.record()
    assert _answers(st, machine.tid)[-1][dr.TARGET_KEY] == record
    v = run.read(st, machine.tid)
    assert dr.approved_of(v) == record
    assert run.approved_seen(v, REVIEW, st.root)[dr.TARGET_KEY] == record


@pytest.mark.parametrize("change", [{"head": "b" * 40}, {"base": "sprint/x"}])
def test_an_answer_to_a_moved_pr_is_not_written_and_the_review_starts_over(st, machine, change):
    machine.turn()

    with pytest.raises(model.Refused, match="progress next"):
        _answer(st, machine.tid, _observe(machine, **change))

    assert _answers(st, machine.tid) == []
    assert _done(st, machine.tid) == []
    assert run.step_progress(run.read(st, machine.tid), REVIEW).waiting is None


def test_dropping_an_issue_narrows_the_record(st, machine):
    machine.turn()

    _answer(st, machine.tid, _observe(machine), drop=(8,))

    record = _answers(st, machine.tid)[-1][dr.TARGET_KEY]
    assert record["targets"]["issues"] == [7]
    assert [u["name"] for u in record["units"]] == ["単位7"]


def test_dropping_one_side_of_a_merged_issue_is_refused(st, machine):
    machine.fake.units["handling"]["8"] = {"handling": "マージする", "into": 7}
    machine.turn()

    with pytest.raises(model.Refused, match="分け直す"):
        _answer(st, machine.tid, _observe(machine), drop=(8,))

    assert _answers(st, machine.tid) == []
    assert _done(st, machine.tid) == HEAD_STEPS


@pytest.mark.parametrize("change", [{"head": "b" * 40}, {"base": "sprint/x"}])
def test_a_move_after_the_approval_starts_the_review_over(st, machine, change):
    machine.turn()
    _answer(st, machine.tid, _observe(machine), drop=(8,))

    why = gate.recheck(st, machine.tid, _observe(machine, **change))

    assert why is not None
    assert _done(st, machine.tid) == []
    v = run.read(st, machine.tid)
    assert v.context()["issues"] == [7, 8]
    assert run.step_progress(v, REVIEW).waiting is None
    assert run.decide(v).step.id == "design-pr-review"


def test_an_unchanged_pr_is_not_reset_after_the_approval(st, machine):
    machine.turn()
    _answer(st, machine.tid, _observe(machine))

    assert gate.recheck(st, machine.tid, _observe(machine)) is None
    assert _done(st, machine.tid) == HEAD_STEPS


# --- MVV 判定で省く回答 ----------------------------------------------------------------------------


def _skipping(m, **change) -> None:
    target = {**m.fake.reviewed, "production": False, **change}
    m.fake.mvv = {"gate": "design", "skip": True, "verdict": None, "why": [], "holds": [], "pr": PR, "head": m.head, "target": target}


def test_the_mvv_answer_carries_the_record(st, machine):
    _skipping(machine)

    machine.turn()

    assert _answers(st, machine.tid)[-1][dr.TARGET_KEY] == machine.fake.record()


@pytest.mark.parametrize("change", [{"base": "sprint/x"}, {"head": "b" * 40}, {"repo": None}])
def test_a_judgement_of_another_target_does_not_skip_the_gate(st, machine, change):
    _skipping(machine, **change)

    machine.turn()

    assert _answers(st, machine.tid) == []
    assert _done(st, machine.tid) == []


# --- approval_stale ------------------------------------------------------------------------------


def _stale(step: str) -> invoke.StageResult:
    return invoke.StageResult(result.stopped("plan", "approval_stale", f"{step}: HEAD が違う"), {})


def _fail_and_retry(st, machine, step: str) -> None:
    v = run.read(st, machine.tid)
    assert run.decide(v).action == run.Action.FAILED
    assert _done(st, machine.tid) == []
    run.append(
        st, machine.tid, lambda now: [model.EventDraft(run_view.RETRY_EVENT, run.CONDUCTOR, REVIEW, reason="approval_stale", rev=now.rev)]
    )
    machine.fake.calls.clear()
    machine.turn()
    assert machine.fake.calls[0].step == "design-pr-review", step


@pytest.mark.parametrize("step", ["design-pr-review", "units-check", "docs-ci", "design-mvv"])
def test_a_stale_check_returns_to_the_review(st, machine, step):
    machine.fake.script[step] = _stale(step)

    machine.turn()

    _fail_and_retry(st, machine, step)


def test_a_stale_merge_returns_to_the_review(st, machine):
    machine.turn()
    _answer(st, machine.tid, _observe(machine), drop=(8,))
    machine.fake.script["design-merge-approved"] = _stale("design-merge-approved")

    machine.turn()

    # 無効になった承認で外した課題は、今のスプリントへ戻る
    assert run.read(st, machine.tid).context()["issues"] == [7, 8]
    _fail_and_retry(st, machine, "design-merge-approved")


def _approved_with_a_drop(st, machine) -> tuple[str, str]:
    """課題 8 を外して承認し、（計画の ID, 繰り越し行のキー）を返す。"""
    machine.turn()
    _answer(st, machine.tid, _observe(machine), drop=(8,))
    machine.fake.script["design-merge-approved"] = _stale("design-merge-approved")
    with store.transaction(st) as conn:
        pid = lane.sprint_row(conn, machine.tid)[0]
    return pid, _answers(st, machine.tid)[-1]["carried_row"]


def test_a_cancelled_carried_row_does_not_block_the_stale_merge(st, machine):
    pid, carried = _approved_with_a_drop(st, machine)
    with store.transaction(st) as conn:
        lane.cancel_row(conn, pid, "a", carried, lane.RowChange("conductor", "整理"))

    machine.turn()

    assert run.read(st, machine.tid).context()["issues"] == [7, 8]
    _fail_and_retry(st, machine, "design-merge-approved")


def test_a_carried_row_another_lane_waits_for_keeps_the_approval_until_released(st, machine):
    pid, carried = _approved_with_a_drop(st, machine)
    decl = {
        "name": "b",
        "kind": "sprint",
        "touches": ["b/"],
        "after": [f"a/{carried}"],
        "rows": [{"kind": "sprint", "issues": [99], "args": []}],
    }
    with store.transaction(st) as conn:
        plan.add_lane(conn, pid, decl)

    machine.turn()

    v = run.read(st, machine.tid)
    assert run.decide(v).action == run.Action.FAILED
    assert v.context()["issues"] == [7]
    assert "design-gate" in _done(st, machine.tid)
    assert "ndf lanes set" in run.report(v, run.decide(v))["summary"]


# --- 入力の組み立て ------------------------------------------------------------------------------


def test_the_steps_get_the_carried_values_and_the_machine_id(monkeypatch, st, repo, machine):
    held = run_view.View.context
    # 前の回の値が文脈にあっても、ステップへは渡さない
    monkeypatch.setattr(run_view.View, "context", lambda v: {**held(v), "reviewed": {"pr": 1}, "design_units": None})

    machine.turn()

    inputs = {c.step: c.inputs for c in machine.fake.calls}
    assert "reviewed" not in inputs["design-pr-review"] and "design_units" not in inputs["design-pr-review"]
    assert inputs["units-check"]["reviewed"] == machine.fake.reviewed
    assert inputs["docs-ci"]["design_units"] == machine.fake.units
    extras = {c.step: dict(c.env_extra) for c in machine.fake.calls}
    assert extras["units-check"] == {"NDF_MACHINE_ID": machine.tid}
    assert extras["docs-ci"] == {}
    # `ndf records mvv gate --machine` も同じ入力を組む
    got = stage_inputs.stage_inputs_of(repo, machine.tid, "design-mvv")
    assert (got["reviewed"], got["design_units"]) == (machine.fake.reviewed, machine.fake.units)


def test_a_script_step_passes_the_extra_environment_to_its_child(tmp_path):
    seen = {}

    def runner(argv, cwd, env):
        seen.update(env)
        return SimpleNamespace(stdout=json.dumps(result.result("plan", "ok", "終えた")), returncode=0)

    call = invoke.StageRun(invoke.SCRIPT, "ndf", {}, (), "none", "design-sync", {"argv": ["ndf"]}, env_extra={"NDF_MACHINE_ID": "m1"})
    invoke.invoke(call, tmp_path, tmp_path, runner=runner)

    assert seen["NDF_MACHINE_ID"] == "m1"


# --- 工程「実装」の再承認 ------------------------------------------------------------------------


def test_the_reapproval_of_the_implement_stage_carries_its_record(st, machine):
    machine.turn()
    _answer(st, machine.tid, _observe(machine))
    converted = {**machine.fake.record(), "review": {**machine.fake.reviewed, "pr": None}}
    waiting = result.result("plan", result.GATE, "再承認を待つ", routes={"approved": None, "rejected": None})
    machine.fake.script["impl"] = invoke.StageResult({**waiting, dr.TARGET_KEY: converted}, {})
    machine.turn()
    machine.turn()

    q = gate.waiting(run.read(st, machine.tid))
    assert q is not None and q.stage == IMPLEMENT
    gate.answer(st, machine.tid, gate.Answer(q.id, "approved", "分け方を承認", "human:reviewer", "k2"))

    assert dr.reapproved_of(run.read(st, machine.tid)) == converted
