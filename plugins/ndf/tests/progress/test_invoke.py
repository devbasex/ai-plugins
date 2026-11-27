"""工程から作業を呼ぶ層（ndf.progress.invoke。#1142 の設計の「工程から作業を呼ぶ層」・3364-7）。"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import sprint_fixture
from sprint_fixture import sprint_repo as _sprint_repo

from ndf import cli
from ndf.adapters import github
from ndf.contracts import result
from ndf.core import route
from ndf.core.plan import cli as plan_cli
from ndf.core.plan import files as plan_files
from ndf.core.plan import records as plan_records
from ndf.core.plan import runner as plan_runner
from ndf.progress import definition, invoke, issue_values, model, stages


# 定義の読み込みは工程の文書をスキーマで確かめるため 1 回 0.3 秒ほどかかる。スナップショットは不変なのでモードごとに 1 回だけ読む


def _stage(name: str, mode: str = "light"):
    return definition.load_definition(mode).stage(name)


def _run(stage_name: str, step_index: int = 0, inputs: dict | None = None) -> invoke.StageRun:
    s = _stage(stage_name)
    return invoke.stage_run(s, s.steps[step_index], inputs if inputs is not None else {})


def test_resolve_inputs_passes_only_the_declared_keys():
    s = _stage("作業場所の用意")
    assert invoke.resolve_inputs(s, {"issues": [3092], "mode": "light", "pace": "normal"}) == {"issues": [3092]}


def test_resolve_inputs_names_every_missing_key():
    with pytest.raises(invoke.StageInputMissing) as caught:
        invoke.resolve_inputs(_stage("コードレビュー"), {"issues": [1]})
    assert caught.value.keys == ("worktree", "branch")


@pytest.mark.parametrize(
    "values",
    [
        {"worktree": "relative/path", "branch": "sprint/s1"},
        {"worktree": "/work/a", "branch": "-rf"},
        {"worktree": "/work/a", "branch": "a..b"},
        {"worktree": "--upload-pack=x", "branch": "sprint/s1"},
    ],
)
def test_resolve_inputs_refuses_values_of_the_wrong_shape(values):
    # 3364-7: 次の工程の引数・作業ディレクトリになる値は、キーがあるかだけでなく形を確かめてから渡す
    with pytest.raises(invoke.StageInputInvalid):
        invoke.resolve_inputs(_stage("コードレビュー"), values)


# .ndf/project.json の delivery の 1 行（このリポジトリの設定と同じ形）
DELIVERY = {
    "target": "ndf プラグイン",
    "kind": "auto",
    "trigger": ".github/workflows/release-cd.yml",
    "channel": "production",
    "versioned": True,
    "publishes_release": True,
    "result": {"workflow": "release-cd.yml"},
}


def test_delivery_of_the_declared_shape_passes():
    assert not invoke.value_errors({"delivery": DELIVERY})
    assert not invoke.value_errors({"delivery": {**DELIVERY, "kind": "manual", "channel": None, "branch": "release/x", "result": None}})


@pytest.mark.parametrize(
    "delivery",
    [
        {},
        {"kind": "plugin"},
        {**DELIVERY, "kind": "plugin"},
        {**DELIVERY, "channel": "prod"},
        {**DELIVERY, "target": 3},
        {**DELIVERY, "branch": "-rf"},
        {**DELIVERY, "versioned": "yes"},
        {**DELIVERY, "result": {"workflow": 1}},
        {**DELIVERY, "unknown": 1},
        {k: v for k, v in DELIVERY.items() if k != "target"},
    ],
)
def test_delivery_of_the_wrong_shape_is_refused(delivery):
    # 3364-7: デプロイの経路になる値は、空でないかだけでなくキーの型と kind・channel の取りうる値を確かめる
    assert invoke.value_errors({"delivery": delivery})


def test_stage_run_refuses_an_unknown_call_and_a_missing_name():
    s = _stage("設計")
    bad = definition.stages.StepDef("x", s.name, "ndf.unknown", "work", "none")
    with pytest.raises(model.Refused):
        invoke.stage_run(s, bad, {})
    nameless = definition.stages.StepDef("y", s.name, "ndf.plan", "work", "none")
    with pytest.raises(model.Refused):
        invoke.stage_run(s, nameless, {})


def test_listen_step_is_not_invoked():
    # 設計承認ゲート（listen）は standard の設計のステップ（light の設計はゲートを持たない。#3425）
    s = definition.load_definition("standard").stage("設計")
    run = invoke.stage_run(s, next(st for st in s.steps if st.kind == "gate"), {})
    assert run.kind == invoke.LISTEN
    with pytest.raises(model.Refused):
        invoke.invoke(run, Path("/"), Path("/tmp"))


def test_read_report_takes_the_last_line_and_refuses_bad_shapes():
    good = result.result("plan", "ok", "済んだ")
    assert invoke.read_report(f"途中の行\n{json.dumps(good)}\n") == good
    assert invoke.read_report("") is None
    assert invoke.read_report("JSON でない") is None
    assert invoke.read_report(json.dumps({"status": "ok"})) is None


def test_finished_keeps_only_declared_outputs_and_warns_about_the_rest():
    run = _run("作業場所の用意", inputs={"issues": [1]})
    report = result.result("plan", "ok", "済んだ", outputs={"worktree": "/w", "branch": "sprint/s", "pr": 3})
    got = invoke.finished(run, report)
    assert got.outputs == {"worktree": "/w", "branch": "sprint/s"}
    assert got.warnings and "pr" in got.warnings[0]


def test_finished_turns_a_badly_shaped_output_into_bad_report():
    run = _run("作業場所の用意", inputs={"issues": [1]})
    got = invoke.finished(run, result.result("plan", "ok", "済んだ", outputs={"worktree": "/w", "branch": "-x"}))
    assert got.status == "stopped" and got.cause["kind"] == "bad_report"


def test_record_outputs_writes_one_event_with_the_declared_keys_only():
    s = _stage("作業場所の用意")
    draft, warnings = stages.outputs_draft(s, 1, {"worktree": "/w", "branch": "sprint/s", "pr": 9}, invoke.ACTOR)
    assert draft is not None and draft.kind == "outputs_recorded" and draft.actor == "workflow"
    assert draft.payload == {"outputs": {"worktree": "/w", "branch": "sprint/s"}}
    assert warnings and "pr" in warnings[0]
    assert stages.outputs_draft(s, 1, {"pr": 9}, invoke.ACTOR)[0] is None


STEPS = [{"id": "a", "run": ["true"]}, {"id": "b", "run": ["true"]}]


def _plan_template(monkeypatch, name: str = "design", params: tuple[str, ...] = ()) -> route.Template:
    """ステップ a・b のテンプレート 1 本だけを `route.templates()` が返すようにする。`worktree` を受ければプランの作業場所へ差し込む。"""
    plan = {"name": name, "steps": STEPS, **({"worktree": "{{worktree}}"} if "worktree" in params else {})}
    t = route.Template(name, name, "試験のテンプレート", params, plan)
    monkeypatch.setattr(route, "templates", lambda: {name: t})
    return t


def _plan_file(tmp_path: Path, name: str = "design") -> Path:
    """プランの実行（runner）へ直に渡すプランのファイル。"""
    path = tmp_path / f"{name}-plan.json"
    path.write_text(json.dumps({"name": name, "steps": STEPS}))
    return path


def _plan_run(inputs: dict, name: str = "design") -> invoke.StageRun:
    return invoke.StageRun(invoke.PLAN, name, inputs, (), "sprint_diff", "design")


def test_plan_argv_passes_inputs_and_the_answer(tmp_path, monkeypatch):
    _plan_template(monkeypatch)
    argv = invoke.plan_argv(_plan_run({"issues": [1, 2]}), tmp_path, "approved", tmp_path)
    assert argv[argv.index("plan") + 1] == "next" and argv[argv.index("next") + 1] == str(tmp_path / "design.json")
    assert json.loads((tmp_path / "design.json").read_text()) == {"name": "design", "steps": STEPS}
    assert argv[argv.index("--state-dir") + 1] == str(tmp_path)
    assert argv[argv.index("--set") + 1] == "issues=[1, 2]"
    assert argv[-4:] == ["--answer", "approved", "--by", invoke.PLAN_ANSWER_BY]
    assert argv[1] == "-I"


def test_every_plan_a_stage_calls_has_a_route_template():
    # #3474: 工程の定義の with.plan は C3 のテンプレートの名前（route.templates() のキー）。どのモードのどの工程が呼ぶ名前もテンプレートにある
    called = {
        (s.name, st.id, invoke.stage_run(s, st, {}).name)
        for mode in definition.modes()
        for s in definition.load_definition(mode).stages
        for st in s.steps
        if st.call == "ndf.plan"
    }
    assert {("実装", "implement", "impl"), ("作業場所の用意", "worktree", "worktree")} <= called
    known = set(route.templates())
    assert [c for c in sorted(called) if c[2] not in known] == []


def test_a_stage_calling_a_plan_run_in_the_worktree_takes_the_worktree_as_an_input():
    # PR 3486 の指摘: 作業場所で打つテンプレート（差し込みに worktree）を呼ぶ工程は、文脈の作業場所を入力に持つ。持たないと
    # テンプレートの差し込みがブランチ名から別の作業場所を推し、そこの HEAD を push する
    templates = route.templates()
    lacking = sorted(
        (mode, s.name, st.id)
        for mode in definition.modes()
        for s in definition.load_definition(mode).stages
        for st in s.steps
        if st.call == "ndf.plan" and "worktree" in templates[invoke.stage_run(s, st, {}).name].params and "worktree" not in s.inputs
    )
    assert lacking == []


def test_plan_gets_the_worktree_as_the_plans_location_and_not_as_a_set_value(tmp_path, monkeypatch):
    # PR 3464 の指摘: --set worktree=... はプランの実行が決まった名前の上書きとして断る
    _plan_template(monkeypatch, params=("worktree",))
    work, location = tmp_path / "w", str(tmp_path / "wt")
    work.mkdir()
    argv = invoke.plan_argv(_plan_run({"issues": [1], "worktree": location}), work, None, tmp_path)
    sets = [argv[i + 1] for i, a in enumerate(argv) if a == "--set"]
    assert sets == ["issues=[1]"]
    plan = Path(argv[argv.index("next") + 1])
    assert plan.parent == work and json.loads(plan.read_text())["worktree"] == location
    # プランの実行は同じ引数を断らずに受け、作業場所はプランの作業場所に入る
    inv = plan_runner.prepare(tmp_path / "root", plan, None, str(work), tmp_path, plan_runner.RunOptions(plan_cli._pairs(sets, "--set")))
    assert inv.plan.worktree == location and inv.values["worktree"] == location


def test_a_template_that_cannot_be_filled_stops_as_precondition(tmp_path, monkeypatch):
    # 差し込み worktree を受けるテンプレートに、作業場所も省いた作業場所を決めるブランチも無い（#3908 の決定 4 で precondition）
    _plan_template(monkeypatch, params=("worktree",))
    got = invoke.invoke(_plan_run({"issues": [1]}), tmp_path, tmp_path / "w", runner=lambda *a: pytest.fail("打たない"))
    assert got.status == "stopped" and got.cause["kind"] == "precondition" and "worktree" in got.cause["detail"]


PROJECT = {
    "test": {"suites": [{"name": "pytest", "command": "pytest -q"}, {"name": "lint", "kind": "lint", "command": "ruff check"}]},
    "ci": {"required_checks": ["pytest", "lint"]},
    "workflow": {"launch": {"review": ["ndf-review"], "rebuild": ["ndf-rebuild"]}},
}
# テンプレート → 工程の入力（設計と実装は issues・worktree・mode、設計のマージは issues・worktree）
STAGE_INPUTS = {
    "design": ("issues", "worktree", "mode"),
    "impl": ("issues", "worktree", "mode"),
    "design-merge": ("issues", "worktree"),
    "quality-gates": ("issues", "worktree"),
    "cross-review": ("issues", "worktree"),
}


def _stage_run_of(name: str, repo: Path, issues: list[int]) -> invoke.StageRun:
    inputs = {"issues": issues, "worktree": str(repo), "mode": "light"}
    return invoke.StageRun(invoke.PLAN, name, {k: inputs[k] for k in STAGE_INPUTS[name]}, (), "none", name)


@pytest.mark.parametrize(
    ("name", "missing"),
    [
        # rebuild は設定が無ければ既定のコマンドを組むので欠けに挙がらない（#4337）
        ("design", {"worker", "required"}),
        ("impl", {"test_command", "worker", "required"}),
        ("design-merge", {"worker"}),
    ],
)
def test_template_values_missing_from_every_source_stop_as_precondition_by_name(tmp_path, monkeypatch, name, missing):
    # PR 3464 の指摘（#3092）: テンプレートの必須の差し込みが工程の入力にも文脈にも既定にも無いと、実行の前に止まる（#3908 の決定 4 で precondition）。
    # 出所（設定・既定）のどれからも決まらなければ、足りない名前と出所を挙げて止まる。課題に依存しない差し込みが欠ければ
    # 課題を読む前に止まるので、課題の出所の差し込み（title・body_file・issue）はまだ挙げない（PR 4289 の r2-1）
    repo = _sprint_repo(tmp_path)
    sprint_fixture.without_worker(monkeypatch)
    monkeypatch.setattr(issue_values, "issue_text", lambda *a: pytest.fail("課題が 2 つなら課題から写さない"))
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None, **_: "squash")
    got = invoke.invoke(_stage_run_of(name, repo, [1, 2]), repo, tmp_path / "w", runner=lambda *a: pytest.fail("打たない"))
    assert got.status == "stopped" and got.cause["kind"] == "precondition"
    named = {i["key"]: i["source"] for i in got.report["items"]}
    assert set(named) >= missing and "branch" not in named
    assert not {"title", "body_file", "issue"} & set(named)
    assert all(n in got.cause["detail"] for n in missing)
    assert named.get("worker") == invoke.FROM_DEFAULT


def _filled_plan(tmp_path, monkeypatch, name):
    """テンプレート `name` を、課題 #3092・設定 `PROJECT`・差し替えた既定（worker と gate）で埋めたプランを組む。

    課題の題と本文は課題から、テストのコマンド・必須のチェック・レビューと組み直しの起動は設定から、worker の起動は
    params.json の既定から埋め、ブランチは作業場所から決める（#3092）。プランと、その JSON の文字列・作業場所の根・
    実行の保存先を返す。
    """
    repo = _sprint_repo(tmp_path, PROJECT)
    defaults = {"worker": ["ndf-worker"], "gate": ["ndf-gate"]}
    held = route.params()
    patched = {n: replace(p, default=defaults[n]) if n in defaults else p for n, p in held.items()}
    monkeypatch.setattr(route, "params", lambda: patched)
    monkeypatch.setattr(issue_values, "issue_text", lambda location, n: (f"#{n} の題", f"#{n} の本文"))
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None, **_: "squash")
    work = tmp_path / "w"
    work.mkdir()
    run = _stage_run_of(name, repo, [3092])
    argv = invoke.plan_argv(run, work, None, repo)
    plan = json.loads(Path(argv[argv.index("next") + 1]).read_text())
    return plan, json.dumps(plan, ensure_ascii=False), repo, work


@pytest.mark.parametrize("name", ["design", "impl", "design-merge"])
def test_template_values_fill_the_worker_from_the_default_and_the_branch_from_the_location(tmp_path, monkeypatch, name):
    plan, text, repo, _ = _filled_plan(tmp_path, monkeypatch, name)
    assert plan["worktree"] == str(repo) and "sprint/s1" in plan["name"]
    assert "ndf-worker" in text and "{{" not in text


def test_impl_with_an_acceptance_of_several_issues_does_not_ask_for_the_issue(tmp_path, monkeypatch):
    # PR 3888 の指摘: 受け入れ条件（acceptance）を入力で渡せば、課題をまとめた単位でも issue を求めない。
    # 題と本文は代表の課題（番号の最も小さい #3）からコピーする（#3908 の決定 7）
    repo = _sprint_repo(tmp_path, PROJECT)
    sprint_fixture.with_worker(monkeypatch)
    monkeypatch.setattr(issue_values, "issue_text", lambda location, n: (f"#{n} の題", f"#{n} の本文"))
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None, **_: "squash")
    run = _stage_run_of("impl", repo, [4, 3])
    run = replace(run, inputs={**run.inputs, "acceptance": ["--issue", "3", "--issue", "4"]})
    work = tmp_path / "w"
    work.mkdir()
    got = invoke.template_values(route.choose("implement"), run, work, repo, project=PROJECT, local_tests=True)
    assert "issue" not in got and got["title"] == "#3 の題"


def test_quality_gates_builds_for_a_sprint_with_several_issues(tmp_path, monkeypatch):
    # 完了判定の design-tests は工程の入力の課題の並びを読む。課題が 2 つ以上でも、課題を読まずにプランを組める
    repo = _sprint_repo(tmp_path, PROJECT)
    held = route.params()
    monkeypatch.setattr(route, "params", lambda: {n: replace(p, default=["ndf-worker"]) if n == "worker" else p for n, p in held.items()})
    monkeypatch.setattr(issue_values, "issue_text", lambda *a: pytest.fail("完了判定は課題の題と本文を読まない"))
    work = tmp_path / "w"
    work.mkdir()
    argv = invoke.plan_argv(_stage_run_of("quality-gates", repo, [1, 2]), work, None, repo)
    plan = json.loads(Path(argv[argv.index("next") + 1]).read_text())
    step = next(s for s in plan["steps"] if s.get("id") == "design-tests")
    assert step["run"] == ["ndf", "loop", "design-tests", "--issue", "1", "2"]


@pytest.mark.parametrize("name", ["design", "impl"])
def test_template_values_fill_the_title_and_the_body_file_from_the_issue(tmp_path, monkeypatch, name):
    _, text, _, work = _filled_plan(tmp_path, monkeypatch, name)
    assert "#3092 の題" in text
    body = work / issue_values.ISSUE_BODY_FILE.format(number=3092)
    assert body.read_text() == "#3092 の本文" and str(body) in text


def test_template_values_fill_the_test_command_from_the_test_suites_of_the_ndf_settings(tmp_path, monkeypatch):
    _, text, _, _ = _filled_plan(tmp_path, monkeypatch, "impl")
    assert "pytest -q" in text and "ruff check" not in text


@pytest.mark.parametrize("name", ["design", "impl"])
def test_template_values_fill_the_required_checks_from_the_ndf_settings(tmp_path, monkeypatch, name):
    _, text, _, _ = _filled_plan(tmp_path, monkeypatch, name)
    assert "--required=pytest" in text and "--required=lint" in text


def test_template_values_fill_the_review_and_rebuild_launch_from_the_ndf_settings(tmp_path, monkeypatch):
    _, text, _, _ = _filled_plan(tmp_path, monkeypatch, "design")
    assert "ndf-review" in text and "ndf-rebuild" in text


def test_template_values_pass_the_plan_gate_through_to_the_stage_gate(tmp_path, monkeypatch):
    # プランの中の承認ゲートは工程の承認ゲートが決めるため、params.json の既定（人の回答を待つ）を使わずに待たずに通す
    # （PR 3496 の指摘。承認待ちを足さない）
    plan, text, _, _ = _filled_plan(tmp_path, monkeypatch, "design-merge")
    gates = [s for s in plan["steps"] if s["id"] == "merge-gate"]
    assert "ndf-gate" not in text and gates and all(s["run"] == invoke.STAGE_GATE for s in gates)


def test_test_command_joins_every_test_suite_in_declared_order():
    # PR 3469（lr-flow の C3 の保留分）: テストの suite が複数あれば、設定の順に && でつないだ 1 つのコマンドにする。
    # テストでない suite（kind が lint）と command の無い suite は入れない
    suites = [
        {"name": "unit", "command": "pytest -q tests/unit"},
        {"name": "lint", "kind": "lint", "command": "ruff check"},
        {"name": "empty", "kind": "test"},
        {"name": "e2e", "kind": "test", "command": "pytest -q tests/e2e"},
    ]
    got = invoke._project_value("test_command", {"test": {"suites": suites}})
    assert got == "pytest -q tests/unit && pytest -q tests/e2e"
    assert invoke._project_value("test_command", {"test": {"suites": suites[1:3]}}) is None


def test_ci_only_ndf_settings_builds_the_impl_plan_without_local_tests(tmp_path, monkeypatch):
    # #3473: テストを CI に寄せる設定（test.ci.only）があれば、実装のプランは手元でテストを打たない。テストの suite が
    # 設定に無くても test_command を求めず、worker の後は push へ、CI の修正の後は CI の前の push（push-fixed）へ進む
    project = {**PROJECT, "test": {"ci": {"only": True}, "suites": [{"name": "lint", "kind": "lint", "command": "ruff check"}]}}
    repo = _sprint_repo(tmp_path, project)
    held = route.params()
    monkeypatch.setattr(route, "params", lambda: {n: replace(p, default=["ndf-worker"]) if n == "worker" else p for n, p in held.items()})
    monkeypatch.setattr(issue_values, "issue_text", lambda location, n: (f"#{n} の題", f"#{n} の本文"))
    work = tmp_path / "w"
    work.mkdir()
    argv = invoke.plan_argv(_stage_run_of("impl", repo, [3473]), work, None, repo)
    plan = json.loads(Path(argv[argv.index("next") + 1]).read_text())
    ids = [s["id"] for s in plan["steps"]]
    assert not {"test", "ci-local"} & set(ids)
    assert not any(s["run"][:3] == ["ndf", "route", "test"] for s in plan["steps"])
    by_id = {s["id"]: s for s in plan["steps"]}
    assert ids[ids.index("work") + 1] == "acceptance" and ids[ids.index("acceptance") + 1] == "push"
    assert by_id["fix"]["next"] == "push" and by_id["ci-fix"]["next"] == "push-fixed"


def test_the_impl_plan_checks_the_acceptance_records_of_the_issue_after_the_worker(tmp_path, monkeypatch):
    # #1682: 実装のプランは worker の後に、課題の受け入れ条件の確認の区分を照らす（記録の無い条件が残れば止まる）
    plan, _, _, _ = _filled_plan(tmp_path, monkeypatch, "impl")
    ids = [s["id"] for s in plan["steps"]]
    check = next(s for s in plan["steps"] if s["id"] == "acceptance")
    assert ids[ids.index("work") + 1] == "acceptance"
    assert check["run"] == ["ndf", "loop", "acceptance", "check", "--issue", "3092"]


def test_review_without_a_ndf_settings_runs_cross_review_with_the_pr_made_before_the_step(tmp_path, monkeypatch):
    # #3489: 設定に review が無ければ、設計のプランの review は pr のステップの後に、ブランチの PR の番号で cross-review run を打つ
    held = PROJECT["workflow"]
    monkeypatch.setitem(PROJECT, "workflow", {"launch": {"rebuild": held["launch"]["rebuild"]}})
    plan, _, _, _ = _filled_plan(tmp_path, monkeypatch, "design")
    ids = [s["id"] for s in plan["steps"]]
    review = next(s for s in plan["steps"] if s["id"] == "review")
    assert ids.index("pr") < ids.index("review")
    head = review["run"][:4]
    assert head[:2] == ["sh", "-c"] and head[3] == "sprint/s1"
    rest = review["run"][4:]
    assert rest[1:4] == ["cross-review", "run", "--state-dir"] and rest[rest.index("--purpose") + 1] == "design"
    assert rest[rest.index("--base") + 1] == "develop" and "--pr" not in rest and rest[-2] == "--findings"


def test_review_without_a_ndf_settings_resolves_the_base_for_the_cross_review_template(tmp_path, monkeypatch):
    # PR 3537 の指摘: cross-review のテンプレートは params に base を持たないので、マージ先はリポジトリのブランチの運用から決める
    held = PROJECT["workflow"]
    monkeypatch.setitem(PROJECT, "workflow", {"launch": {"rebuild": held["launch"]["rebuild"]}})
    plan, _, _, _ = _filled_plan(tmp_path, monkeypatch, "cross-review")
    review = next(s for s in plan["steps"] if s["id"] == "review")
    rest = review["run"][4:]
    assert rest[rest.index("--base") + 1] == "develop" and rest[rest.index("--purpose") + 1] == "code"


def test_impl_review_starts_from_the_remote_base_like_the_single_route(tmp_path, monkeypatch):
    # #3879 構成要素 4: 工程から組む実装のプランも、単発の経路と同じ値（起点 <リモート>/<base>）で単位の PR をレビューする
    held = PROJECT["workflow"]
    monkeypatch.setitem(PROJECT, "workflow", {"launch": {"rebuild": held["launch"]["rebuild"]}})
    plan, _, _, _ = _filled_plan(tmp_path, monkeypatch, "impl")
    review = next(s for s in plan["steps"] if s["id"] == "review")
    assert review["run"][8:] == route.review_launch("impl", "sprint/s1", "origin/develop")


# 打つ時点にブランチの PR の番号を読み、--pr に足して打つシェルの文
REVIEW_WITH_PR = 'n=$(gh pr view "$0" --json number --jq .number) && [ -n "$n" ] || exit 1; exec "$@" --pr "$n"'


def test_review_without_a_ndf_settings_is_the_fixed_launch_of_cross_review(tmp_path, monkeypatch):
    # 現状固定（#3879 Task 4）: 工程から組む経路の {{review}} の既定の並び（review_launch）
    held = PROJECT["workflow"]
    monkeypatch.setitem(PROJECT, "workflow", {"launch": {"rebuild": held["launch"]["rebuild"]}})
    plan, _, _, _ = _filled_plan(tmp_path, monkeypatch, "cross-review")
    review = next(s for s in plan["steps"] if s["id"] == "review")
    assert review["run"] == [
        "sh", "-c", REVIEW_WITH_PR, "sprint/s1",
        "ndf", "cross-review", "run", "--state-dir", "{state_dir}/cross-review", "--base", "develop", "--purpose", "code",
    ]  # fmt: skip


def _code_review_plan(tmp_path, monkeypatch, repo: Path, context: dict) -> dict:
    """実際の工程「コードレビュー」の定義と文脈 `context` から入力を取り（`resolve_inputs`）、プランを組んで返す。"""
    sprint_fixture.with_worker(monkeypatch)
    stage = _stage("コードレビュー", "standard")
    run_ = invoke.stage_run(stage, stage.steps[0], invoke.resolve_inputs(stage, context))
    work = tmp_path / "w"
    work.mkdir(exist_ok=True)
    argv = invoke.plan_argv(run_, work, None, repo)
    return json.loads(Path(argv[argv.index("next") + 1]).read_text())


def test_current_code_review_stage_without_gathered_reviews_from_the_base_branch(tmp_path, monkeypatch):
    # 現状固定（#3879 Task 8）: 文脈に gathered が無い工程「コードレビュー」のプランは、ベースブランチを起点にレビューする
    repo = _sprint_repo(tmp_path)
    plan = _code_review_plan(tmp_path, monkeypatch, repo, {"issues": [1], "worktree": str(repo), "branch": "sprint/s1", "mode": "standard"})
    review = next(s for s in plan["steps"] if s["id"] == "review")
    assert review["run"] == route.review_launch("cross-review", "sprint/s1", "develop")


def _gathered_repo(tmp_path: Path, project: dict | None = None) -> tuple[Path, str]:
    """スプリントブランチ sprint/s1 に 1 つコミットを持つ作業場所と、そのコミット（集めた時点）。"""
    repo = _sprint_repo(tmp_path, project)
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "集めた"], check=True)
    return repo, subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def _seam_context(repo: Path, gathered: str) -> dict:
    return {"issues": [1], "worktree": str(repo), "branch": "sprint/s1", "mode": "standard", "gathered": gathered}


def test_resolve_inputs_takes_an_optional_input_only_when_the_context_has_it():
    # #3879 構成要素 5: 任意の入力（optional_inputs）は文脈に無くても断らず、あれば形を確かめて渡す
    s = _stage("コードレビュー", "standard")
    context = {"worktree": "/w", "branch": "sprint/s1", "mode": "standard"}
    assert invoke.resolve_inputs(s, context) == {"worktree": "/w", "branch": "sprint/s1"}
    assert invoke.resolve_inputs(s, {**context, "gathered": "a" * 40})["gathered"] == "a" * 40
    with pytest.raises(invoke.StageInputInvalid):
        invoke.resolve_inputs(s, {**context, "gathered": "main"})


def test_code_review_with_gathered_reviews_from_the_gathered_commit(tmp_path, monkeypatch):
    # #3879 構成要素 5: 文脈に gathered があれば、既定の起動の起点（--base）を gathered にする
    repo, gathered = _gathered_repo(tmp_path)
    plan = _code_review_plan(tmp_path, monkeypatch, repo, _seam_context(repo, gathered))
    review = next(s for s in plan["steps"] if s["id"] == "review")
    assert review["run"] == route.review_launch("cross-review", "sprint/s1", gathered)


def test_code_review_with_gathered_appends_the_base_to_the_launch_of_the_ndf_settings(tmp_path, monkeypatch):
    # 設定（workflow.launch.review）の起動は書き換えず、並びの後ろに --base <gathered> を足す（後に書いた値が勝つ）
    repo, gathered = _gathered_repo(tmp_path, {"workflow": {"launch": {"review": ["ndf-review", "--base", "develop"]}}})
    plan = _code_review_plan(tmp_path, monkeypatch, repo, _seam_context(repo, gathered))
    review = next(s for s in plan["steps"] if s["id"] == "review")
    assert review["run"] == ["ndf-review", "--base", "develop", "--base", gathered]


def test_code_review_with_a_gathered_off_the_sprint_branch_stops_before_the_plan(tmp_path, monkeypatch):
    # unreviewed_range が判定できなければ、工程の条件と同じ理由（precondition）でプランを組まずに止まる
    repo, gathered = _gathered_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "other"], check=True)
    sprint_fixture.with_worker(monkeypatch)  # 課題に依存しない worker の欠けで先に止まらないようにする
    stage = _stage("コードレビュー", "standard")
    run_ = invoke.stage_run(stage, stage.steps[0], invoke.resolve_inputs(stage, _seam_context(repo, gathered)))
    got = invoke.invoke(run_, repo, tmp_path / "w", runner=lambda *a: pytest.fail("打たない"))
    assert got.status == "stopped" and got.cause["kind"] == "precondition" and "other" in got.cause["detail"]


def test_workflow_ndf_settings_with_launch_and_stages_passes_the_schema(tmp_path):
    # #3473 の受け入れ条件 3: workflow.launch の review・rebuild・refactor（語の並び）と stages を持つ設定はスキーマの検査を通る
    launch = {"review": ["ndf", "review"], "rebuild": ["ndf", "rebuild"], "refactor": ["ndf", "refactor"]}
    stages_decl = {"design": {"enabled": True}, "release": {"enabled": False, "reason": "手で出す"}}
    project = {**PROJECT, "workflow": {"launch": launch, "stages": stages_decl}}
    repo = _sprint_repo(tmp_path, project)
    assert invoke._project(repo)["workflow"]["launch"] == launch


@pytest.mark.parametrize(
    "workflow",
    [
        {"launch": {"review": []}},
        {"launch": {"rebuild": [""]}},
        {"launch": {"review": "ndf review"}},
        {"launch": {"other": ["x"]}},
        {"stages": {"release": {"enabled": False}}},
        {"stages": {"release": {"enabled": "no", "reason": "x"}}},
        {"other": {}},
    ],
)
def test_workflow_ndf_settings_out_of_shape_stops_as_invalid(tmp_path, workflow):
    # #3473: workflow の形が違う設定は、値を黙って捨てずにプランを組む前に invalid で止まる
    repo = _sprint_repo(tmp_path, {**PROJECT, "workflow": workflow})
    with pytest.raises(route.RouteError, match="workflow"):
        invoke._project(repo)
    got = invoke.invoke(_stage_run_of("design", repo, [1, 2]), repo, tmp_path / "w", runner=lambda *a: pytest.fail("打たない"))
    assert got.status == "stopped" and got.cause["kind"] == "invalid" and "workflow" in got.cause["detail"]


def test_an_issue_by_an_untrusted_author_is_not_written_and_stops_as_precondition_by_name(tmp_path, monkeypatch):
    # PR 3469 の指摘（#3364）: write 以上の権限が無い書き手の課題の本文を、指示として読まれる作業の入力へ写さない。
    # 題と本文は欠けとして、信用しない出所を挙げて止まり、本文のファイルは書かない
    repo = _sprint_repo(tmp_path, PROJECT)
    sprint_fixture.with_worker(monkeypatch)  # 課題に依存しない差し込みがそろわないと課題を読まない
    monkeypatch.setattr(issue_values, "issue_text", lambda location, n: None)
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None, **_: "squash")
    work = tmp_path / "w"
    work.mkdir()
    got = invoke.invoke(_stage_run_of("impl", repo, [3092]), repo, work, runner=lambda *a: pytest.fail("打たない"))
    assert got.status == "stopped" and got.cause["kind"] == "precondition"
    named = {i["key"]: i["source"] for i in got.report["items"]}
    assert named["title"] == invoke.FROM_UNTRUSTED_ISSUE and named["body_file"] == invoke.FROM_UNTRUSTED_ISSUE
    assert not (work / issue_values.ISSUE_BODY_FILE.format(number=3092)).exists()


def test_a_sprint_of_several_issues_takes_the_values_from_the_smallest_numbered_issue(tmp_path, monkeypatch):
    # #3908 の決定 7: 課題が 2 件以上なら、番号の最も小さい課題を代表にして title・body_file・issue をコピーする
    repo = _sprint_repo(tmp_path, PROJECT)
    monkeypatch.setattr(issue_values, "issue_text", lambda location, n: (f"#{n} の題", f"#{n} の本文"))
    work = tmp_path / "w"
    work.mkdir()
    got = issue_values.issue_values(_stage_run_of("impl", repo, [4211, 3908]).inputs, work, repo)
    body = work / issue_values.ISSUE_BODY_FILE.format(number=3908)
    assert got == {"title": "#3908 の題", "body_file": str(body), "issue": "3908"}
    # 残りの課題（#4211）の閉鎖参照は足さない。設計の PR にも同じ本文が渡り、課題を閉じるのはスプリントの PR の
    # 本文と「スプリントを閉じる」である（`/ndf:pr`。#3908 の r2-2・r2-3）
    assert body.read_text(encoding="utf-8") == "#3908 の本文"


def test_the_sprint_pr_refers_to_every_bundled_issue(tmp_path):
    # #3908 の r2-2: スプリントの PR（`pr` のテンプレート）の本文へ、まとめたすべての課題を `Refs #<番号>` で載せる
    run = invoke.StageRun(invoke.PLAN, "pr", {"issues": [4211, 3908]}, (), "none", "pr")
    pr = route.templates()["pr"]
    assert invoke._fill_refs(pr, {}, run) == {"refs": ["3908", "4211"]}
    # 渡した値は上書きせず、`refs` を持たないテンプレートと課題の無い入力には足さない
    assert invoke._fill_refs(pr, {"refs": ["1"]}, run) == {}
    assert invoke._fill_refs(route.templates()["impl"], {}, run) == {}
    assert invoke._fill_refs(pr, {}, replace(run, inputs={})) == {}


def test_the_issue_is_read_once_and_later_calls_use_the_copy_in_the_work_dir(tmp_path, monkeypatch):
    # PR 3469 の指摘: プランを描き直すたび（承認の回答・続き・打ち直し）に課題を GitHub から読み直さない。
    # 途中で題が直されてもプランは同じで、終わっていない回へ同じキーで届く
    repo = _sprint_repo(tmp_path, PROJECT)
    reads = []

    def read(location, n):
        reads.append(n)
        return (f"#{n} の題（{len(reads)} 回目）", f"#{n} の本文（{len(reads)} 回目）")

    monkeypatch.setattr(issue_values, "issue_text", read)
    work = tmp_path / "w"
    work.mkdir()
    run = _stage_run_of("impl", repo, [3092])
    first = issue_values.issue_values(run.inputs, work, repo)
    again = issue_values.issue_values(run.inputs, work, repo)
    assert reads == [3092] and first == again and first["title"] == "#3092 の題（1 回目）"
    assert Path(first["body_file"]).read_text() == "#3092 の本文（1 回目）"


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (github.GitHubError("上限", status=403, retry_after=60.0, quota="rest"), "transient"),
        (github.GitHubError("5xx", status=502), "transient"),
        (github.GitHubError("届かない", unreached=True), "transient"),
        (github.GitHubError("認証が無い"), "precondition"),
        (invoke.GitError("リモートが無い"), "precondition"),
    ],
)
def test_a_failure_to_read_the_issue_stops_with_its_reason_not_as_missing_values(tmp_path, monkeypatch, error, kind):
    # PR 3469 の指摘: 課題を読めない失敗を「題と本文が無い」（打ち直せない invalid）に変えず、理由を添えて
    # 打ち直せる失敗（不達・5xx・上限）は transient、ほか（認証が無い・リモートが無い）は precondition で止める
    repo = _sprint_repo(tmp_path, PROJECT)
    sprint_fixture.with_worker(monkeypatch)  # 課題に依存しない差し込みがそろわないと課題を読まない

    def fail(location, n):
        raise error

    monkeypatch.setattr(issue_values, "issue_text", fail)
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None, **_: "squash")
    work = tmp_path / "w"
    work.mkdir()
    got = invoke.invoke(_stage_run_of("impl", repo, [3092]), repo, work, runner=lambda *a: pytest.fail("打たない"))
    assert got.status == "stopped" and got.cause["kind"] == kind
    assert str(error) in got.cause["detail"]
    assert not (work / issue_values.ISSUE_BODY_FILE.format(number=3092)).exists()


def test_the_issue_text_is_withheld_unless_the_author_is_trusted(monkeypatch):
    # #3364: 課題の題と本文は、`GitHub.trusts` が真の書き手のときだけ返す
    authored = github.Authored("someone", "NONE", "本文")

    class Hub:
        def __init__(self, repo, client):
            pass

        def issue_text(self, number):
            return "題", authored

        def trusts(self, a):
            return a.association == "OWNER"

    monkeypatch.setattr(invoke.github, "GitHub", Hub)
    monkeypatch.setattr(invoke.github, "client", lambda: None)
    monkeypatch.setattr(invoke.git.Git, "remote_repo", lambda self: "o/r")
    assert issue_values.issue_text(Path("."), 1) is None
    authored = github.Authored("owner", "OWNER", "本文")
    assert issue_values.issue_text(Path("."), 1) == ("題", "本文")


def test_every_template_value_has_one_source():
    # 出所の表は差し込みの定義（params.json）の名前だけを持つ
    assert set(invoke.FILL_SOURCES) <= set(route.params())
    assert set(invoke.PROJECT_KEYS) <= {n for n, s in invoke.FILL_SOURCES.items() if s == invoke.FROM_PROJECT}


def test_a_stopped_plan_is_tried_again_from_the_step_it_stopped_at_in_a_new_round(tmp_path, monkeypatch):
    # PR 3464 の指摘: 同じ引数で呼び直すと、プランは前の回のバックアップの失敗を返すだけ
    _plan_template(monkeypatch)
    work, root = tmp_path / "w", tmp_path / "root"
    work.mkdir()
    run = _plan_run({"issues": [1]})
    first = invoke.plan_argv(run, work, None, tmp_path)
    assert "--from" not in first
    plan = Path(first[first.index("next") + 1])
    inv = plan_runner.prepare(root, plan, None, str(work), tmp_path, plan_runner.RunOptions({"issues": "[1]"}))
    plan_runner._record_run(inv)
    final = {"status": result.STOPPED, "kind": "test_failed", "detail": "落ちた", "last": "b", "count": 2}
    plan_files.write_json(inv.result_path, plan_runner.final_result(inv, final, "SUCCESS"))
    again = invoke.plan_argv(run, work, None, tmp_path)
    assert again[again.index("--from") + 1] == "b" and again.index("--from") < again.index("--state-dir")
    retried = plan_runner.prepare(root, plan, "b", str(work), tmp_path, plan_runner.RunOptions({"issues": "[1]"}))
    assert retried.key != inv.key and retried.round_no == inv.round_no + 1
    # 新しい回が承認ゲートで待つ間（結果のバックアップが無い）は、回答を渡す呼び出しも同じ開始点で同じ回に届く
    plan_runner._record_run(retried)
    assert invoke.plan_start(work) == "b"


def test_a_plan_that_finished_keeps_the_start_of_its_round(tmp_path):
    work = tmp_path / "w"
    inv = plan_runner.prepare(tmp_path / "root", _plan_file(tmp_path), "b", str(work), tmp_path)
    plan_runner._record_run(inv)
    final = {"status": result.OK, "kind": None, "detail": "", "last": "b", "count": 1}
    plan_files.write_json(inv.result_path, plan_runner.final_result(inv, final, "SUCCESS"))
    assert invoke.plan_start(work) == "b"


def _stop_at(root: Path, plan: Path, work: Path, argv: list[str], last: str = "b") -> plan_runner.Invocation:
    """`argv` の呼び出しでプランの回を流し、ステップ `last` で止まった結果をバックアップに残す。"""
    start = argv[argv.index("--from") + 1] if "--from" in argv else None
    sets = plan_cli._pairs([argv[i + 1] for i, a in enumerate(argv) if a == "--set"], "--set")
    inv = plan_runner.prepare(root, plan, start, str(work), work, plan_runner.RunOptions(sets))
    plan_runner._record_run(inv)
    final = {"status": result.STOPPED, "kind": "test_failed", "detail": "落ちた", "last": last, "count": 2}
    plan_files.write_json(inv.result_path, plan_runner.final_result(inv, final, "SUCCESS"))
    return inv


def test_every_retry_of_a_plan_starts_a_new_round(tmp_path, monkeypatch):
    # PR 3464 の指摘: 2 回目以降の打ち直しも、開始点と head が前の回と同じでプランがバックアップの結果を返すだけにならない
    _plan_template(monkeypatch)
    work, root = tmp_path / "w", tmp_path / "root"
    work.mkdir()
    run = _plan_run({"issues": [1]})
    first = invoke.plan_argv(run, work, None, tmp_path)
    plan = Path(first[first.index("next") + 1])
    keys = [_stop_at(root, plan, work, first).key]
    for again in (replace(run, again=True), replace(run, again=True), replace(run, retries=1)):
        argv = invoke.plan_argv(again, work, None, tmp_path)
        assert argv[argv.index("--from") + 1] == "b"
        keys.append(_stop_at(root, plan, work, argv).key)
    assert len(set(keys)) == 4
    # 打ち直さない呼び出し（作業が落ちた後の続き・回答）は同じ回に届く
    argv = invoke.plan_argv(run, work, None, tmp_path)
    assert f"{invoke.ATTEMPT_SET}=3" in argv


def _gate_ended(tmp_path: Path, monkeypatch) -> tuple[invoke.StageRun, Path]:
    """プランの回がステップ a の承認ゲートで終わった（結果のバックアップが gate）保存先。"""
    _plan_template(monkeypatch)
    work = tmp_path / "w"
    work.mkdir()
    run = _plan_run({"issues": [1]})
    argv = invoke.plan_argv(run, work, None, tmp_path)
    plan = Path(argv[argv.index("next") + 1])
    inv = plan_runner.prepare(tmp_path / "root", plan, None, str(work), work, plan_runner.RunOptions({"issues": "[1]"}))
    plan_runner._record_run(inv)
    final = {"status": "gate", "kind": None, "detail": "判断を待つ", "last": "a", "count": 1}
    plan_files.write_json(inv.result_path, plan_runner.final_result(inv, final, "SUCCESS"))
    return run, work


def test_an_answer_to_a_plan_that_ended_at_a_gate_starts_where_the_route_points(tmp_path, monkeypatch):
    # PR 3464 の指摘: 終わった回へ --answer を渡すと、プランはバックアップの gate を返し続ける
    run, work = _gate_ended(tmp_path, monkeypatch)
    argv = invoke.plan_argv(run, work, "approved", tmp_path)
    assert argv[argv.index("--from") + 1] == "b" and "--answer" not in argv


def test_an_answer_with_no_route_ends_the_plan_without_running_it(tmp_path, monkeypatch):
    run, work = _gate_ended(tmp_path, monkeypatch)
    never = lambda *a: pytest.fail("打たない")  # noqa: E731
    got = invoke.invoke(run, tmp_path, work, "rejected", runner=never)
    assert got.status == "stopped" and got.cause["kind"] == "refused"


def test_skills_and_scripts_run_in_the_worktree_of_the_inputs(tmp_path):
    # PR 3464 の指摘: 打った場所でなく、用意した作業場所のファイルを読み書きする
    location = tmp_path / "wt"
    location.mkdir()
    seen = []
    run = invoke.StageRun(invoke.SCRIPT, "true", {"worktree": str(location)}, (), "sprint_diff", "s", {"argv": ["true"]})
    ok = subprocess.CompletedProcess([], 0, json.dumps(result.result("script", "ok", "済んだ")), "")
    invoke.invoke(run, tmp_path, tmp_path / "w", runner=lambda argv, cwd, env: seen.append(cwd) or ok)
    assert seen == [location]
    gone = replace(run, inputs={"worktree": str(tmp_path / "none")})
    got = invoke.invoke(gone, tmp_path, tmp_path / "w", runner=lambda *a: pytest.fail("打たない"))
    assert got.cause["kind"] == "precondition"


def test_same_run_tells_a_stored_result_of_the_same_round():
    first = invoke.StageResult(result.stopped("plan", "transient", "5xx", run_id="r1"), {})
    assert invoke.same_run(first, invoke.StageResult(result.stopped("plan", "transient", "5xx", run_id="r1"), {}))
    assert not invoke.same_run(first, invoke.StageResult(result.stopped("plan", "transient", "5xx", run_id="r2"), {}))
    bare = invoke.StageResult(result.stopped("plan", "transient", "5xx"), {})
    assert not invoke.same_run(bare, bare)


def test_release_result_takes_the_version_from_the_outputs_of_the_plan_result(tmp_path):
    # PR 3464 の指摘: プランの結果はバージョンを outputs に集め、metrics には回とステップの数だけを載せる
    work = tmp_path / "w"
    inv = plan_runner.prepare(tmp_path / "root", _plan_file(tmp_path, "release"), None, str(work), tmp_path)
    plan_records._add_outputs(work, {"product_version": "11.0.0"})
    out = plan_runner.final_result(inv, {"status": result.OK, "kind": None, "detail": "", "last": "b", "count": 2}, "SUCCESS")
    step = next(s for s in _stage("デプロイ").steps if s.id == "release")
    assert "product_version" not in out["metrics"]
    assert definition.validate_io(step.output_ref, out) == []
    assert definition.validate_io(step.output_ref, result.result("plan", "ok", "出した", metrics={"product_version": "11.0.0"}))


def _stopped_at(step: str, kind: str):
    """プランがステップ `step` で `kind` の理由で止まった結果を返す runner。"""
    out = result.stopped("plan", kind, "断られた", items=[{"kind": "plan", "name": "design", "step": step}])
    return lambda argv, cwd, env: subprocess.CompletedProcess(argv, 0, json.dumps(out) + "\n", "")


@pytest.mark.parametrize(
    ("step", "kind", "inputs", "dropped"),
    [
        ("merge", "merge_method", {"issues": [1]}, True),
        ("merge", "merge_method", {"issues": [1], "merge_method": "squash"}, False),
        ("merge", "refused", {"issues": [1]}, False),
        ("merge", "base_conflict", {"issues": [1]}, False),
        ("a", "merge_method", {"issues": [1]}, False),
    ],
)
def test_a_refused_merge_drops_the_values_only_when_the_method_was_asked(tmp_path, monkeypatch, step, kind, inputs, dropped):
    # GitHub に問うて決めた方式をマージのステップで断られたときだけ、組み立て済みの値を捨てて次の打ち直しで方式をもう一度問う。
    # 課題を読めずに止まった打ち直しは記録した方式を使うので、その間に許す方式が変わると捨てなければ断られ続ける（PR 4289 の指摘）
    _plan_template(monkeypatch)
    work = tmp_path / "w"
    got = invoke.invoke(_plan_run(inputs), tmp_path, work, runner=_stopped_at(step, kind))
    assert got.status == "stopped" and got.cause["kind"] == kind
    assert (work / invoke.VALUES_FILE).exists() is not dropped


def test_plan_without_a_template_stops_as_precondition(tmp_path, monkeypatch):
    # 作業場所の用意の with.plan（worktree）に当たるテンプレートが無い
    monkeypatch.setattr(route, "templates", lambda: {})
    run = _run("作業場所の用意", inputs={"issues": [1]})
    got = invoke.invoke(run, tmp_path, tmp_path / "w", runner=lambda *a: pytest.fail("打たない"))
    assert got.status == "stopped" and got.cause["kind"] == "precondition"
    assert got.cause["detail"] == f"プランのテンプレート {run.name} が無い"
    assert not (tmp_path / "w").exists()


def test_plan_is_run_with_the_package_on_the_import_path_and_the_notes(tmp_path, monkeypatch):
    _plan_template(monkeypatch)
    seen = {}

    def runner(argv, cwd, env):
        seen.update(argv=argv, env=env)
        out = result.result("plan", "ok", "済んだ", outputs={"worktree": "/w", "branch": "sprint/s"})
        return subprocess.CompletedProcess(argv, 0, json.dumps(out) + "\n", "")

    run = invoke.StageRun(**{**vars(_plan_run({"issues": [1]})), "outputs": ("worktree", "branch"), "notes": "直す所"})
    got = invoke.invoke(run, tmp_path, tmp_path / "w", runner=runner)
    assert got.outputs == {"worktree": "/w", "branch": "sprint/s"}
    # #4029: src は子の argv（sys.path）で渡し、PYTHONPATH には入れない（プランのステップへ渡らない）
    assert seen["argv"][: len(cli.ndf_command())] == cli.ndf_command()
    assert seen["env"].get("PYTHONPATH") == os.environ.get("PYTHONPATH")
    assert seen["env"][invoke.NOTES_ENV] == "直す所"
    assert (tmp_path / "w").is_dir()


def test_plan_that_prints_no_result_is_bad_report(tmp_path, monkeypatch):
    _plan_template(monkeypatch)
    got = invoke.invoke(
        _plan_run({"issues": [1]}),
        tmp_path,
        tmp_path / "w",
        runner=lambda argv, cwd, env: subprocess.CompletedProcess(argv, 1, "落ちた\n", ""),
    )
    assert got.cause["kind"] == "bad_report"


def test_script_receives_the_inputs_as_json(tmp_path):
    run = invoke.StageRun("script", "x", {"issues": [5]}, (), "none", "s", {"argv": ["x", "--flag"]})
    seen = {}

    def runner(argv, cwd, env):
        seen.update(argv=argv, input=json.loads(env[invoke.INPUT_ENV]))
        return subprocess.CompletedProcess(argv, 0, json.dumps(result.result("script", "ok", "済んだ")), "")

    assert invoke.invoke(run, tmp_path, tmp_path, runner=runner).status == "ok"
    assert seen == {"argv": ["x", "--flag"], "input": {"issues": [5]}}


def test_script_argv_runs_a_subcommand_of_this_plugin_with_the_same_python():
    assert invoke.script_argv([invoke.PACKAGE, "records", "mvv", "gate"]) == invoke.ndf_command("records", "mvv", "gate")
    assert invoke.script_argv(["x", 1]) == ["x", "1"]


def test_skill_prompt_names_the_skill_and_carries_the_notes():
    run = invoke.StageRun("skill", "design", {"issues": [5]}, (), "none", "s", {"skill": "design"}, notes="ここを直す")
    prompt = invoke.skill_prompt(run)
    assert prompt.startswith("/") and ":design" in prompt.splitlines()[0]
    assert '"issues": [5]' in prompt and prompt.endswith("ここを直す")


@pytest.mark.parametrize(
    ("failure", "kind"),
    [(None, "transient"), ("usage_limit", "usage_limit"), ("timeout", "interrupted"), ("refused", "precondition")],
)
def test_skill_failures_map_to_causes(failure, kind):
    from ndf.adapters import claude

    assert invoke._skill_cause(claude.Failure(failure) if failure else None) == kind


# --- 現状固定: _call_skill（I-025。`invoke` の call_claude で差し替えた `claude -p` の呼び出しと、回答から結果への写し） ---------------------


def _skill_run() -> invoke.StageRun:
    return invoke.StageRun("skill", "design", {"issues": [5]}, ("pr",), "none", "s", {"skill": "design"})


def _reply(**kw):
    from ndf.adapters import claude

    return claude.ClaudeReply(**{"ok": True, "answer": None, "raw": "", "cost": 0.0, **kw})


@pytest.fixture
def calls():
    """`claude.call_claude` を差し替え、受けた引数を残す。回答は `calls.reply` に置く。"""
    # 差し替えは `invoke` の `call_claude` へ偽物を渡して行う。回答は `box["reply"]` に置く
    seen: list[dict] = []
    box = {"reply": _reply()}

    def fake(argv, prompt, timeout, cwd=None, options=None):
        seen.append({"argv": list(argv), "prompt": prompt, "timeout": timeout, "cwd": cwd})
        return box["reply"]

    return box, seen, fake


def test_skill_calls_claude_call_claude_by_default():
    import inspect

    from ndf.adapters import claude

    assert inspect.signature(invoke.invoke).parameters["call_claude"].default is claude.call_claude


def test_skill_is_called_with_full_permission_the_result_schema_and_the_implement_limit(tmp_path, calls, monkeypatch):
    from ndf import contracts
    from ndf.adapters import claude
    from ndf.contracts import limits

    # 導入済みの一覧を読めた（外す登録が無い）起動に固定する。読めないときの知らせは次のテストが見る
    monkeypatch.setattr(claude, "list_plugins", lambda **k: [])
    box, seen, fake = calls
    run = _skill_run()
    box["reply"] = _reply(answer=result.result("skill", "ok", "済んだ", outputs={"pr": 3}))
    got = invoke.invoke(run, tmp_path, tmp_path / "w", call_claude=fake)
    schema = contracts.load_json(contracts.schema_path("result"))
    want = claude.claude_args(claude.ClaudeCall(system=invoke.SKILL_SYSTEM, permission=claude.Permission.FULL, json_schema=schema))
    assert seen == [{"argv": want, "prompt": invoke.skill_prompt(run), "timeout": limits.seconds(invoke.SKILL_LIMIT), "cwd": tmp_path}]
    assert got.status == "ok" and got.outputs == {"pr": 3} and got.warnings == ()
    # Skill の作業はプランの保存先を作らない
    assert not (tmp_path / "w").exists()


def test_skill_carries_the_notice_of_an_unreadable_plugin_list_into_its_warnings(tmp_path, calls, monkeypatch):
    """導入済みの一覧を読めずに旧い NDF を外さずに起動したことを、工程の結果の warnings に残す（I10a）。"""
    from ndf.adapters import claude

    unreadable = claude.PluginArgs(["--plugin-dir", "p"], claude.LIST_UNREADABLE)
    monkeypatch.setattr(claude, "plugin_args", lambda root, cwd=None: unreadable)
    box, _, fake = calls
    box["reply"] = _reply(answer=result.result("skill", "ok", "済んだ", outputs={"pr": 3}))
    assert invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=fake).warnings == (claude.LIST_UNREADABLE,)
    box["reply"] = _reply(ok=False, error="落ちた")
    assert invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=fake).warnings == (claude.LIST_UNREADABLE,)


@pytest.mark.parametrize(
    ("failure", "kind"),
    [(None, "transient"), ("usage_limit", "usage_limit"), ("timeout", "interrupted"), ("no_slot", "precondition")],
)
def test_skill_that_cannot_answer_stops_with_the_cause_of_the_failure(tmp_path, calls, failure, kind):
    from ndf.adapters import claude

    box, _, fake = calls
    box["reply"] = _reply(ok=False, error="落ちた", failure=claude.Failure(failure) if failure else None)
    got = invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=fake)
    assert got.status == "stopped" and got.outputs == {}
    assert got.report["tool"] == "skill"
    assert got.cause == {"kind": kind, "detail": "Skill design を回せない: 落ちた"}


@pytest.mark.parametrize("answer", [None, "文字列", [1], {"status": "ok"}])
def test_skill_answer_that_is_not_a_result_is_bad_report(tmp_path, calls, answer):
    box, _, fake = calls
    box["reply"] = _reply(answer=answer)
    got = invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=fake)
    assert got.status == "stopped" and got.cause["kind"] == "bad_report" and got.outputs == {}
    assert got.cause["detail"] == "作業 skill design が結果 JSON を返さない: 回答が結果 JSON の形に合わない"


def test_skill_answer_keeps_only_declared_outputs(tmp_path, calls):
    box, _, fake = calls
    box["reply"] = _reply(answer=result.result("skill", "ok", "済んだ", outputs={"pr": 3, "branch": "b"}))
    got = invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=fake)
    # outputs のキーは文脈の閉じた語彙なので、設定に無いキーは語彙の中の別のキーで確かめる
    assert got.outputs == {"pr": 3} and got.warnings and "branch" in got.warnings[0]
