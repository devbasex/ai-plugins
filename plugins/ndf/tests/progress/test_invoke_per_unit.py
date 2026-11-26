"""工程「実装」を実装計画のレビューの単位ごとに流す変更（#4369）の、工程から作業を呼ぶ層のテスト。

どの課題の実装計画にも「レビューの単位」の見出しが無いスプリントは、課題が複数でも実装のプランを 1 本だけ流し、
ブランチは作業場所が今いるものを使う（受け入れ条件 2）。文脈の `branch`（工程「作業場所の用意」が登録したブランチ）は
差し込みに使わない。

単位ごとに実装のプランを流すための、呼び出し（`StageRun`）の追加の欄と、PR の番号の無い古い実行の記録の打ち直しも
ここで確かめる。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import sprint_fixture

from ndf import contracts
from ndf.contracts import result
from ndf.contracts.worker_kinds import read_values, write_values
from ndf.core import durable, route
from ndf.core.plan import files as plan_files
from ndf.core.plan import runner as plan_runner
from ndf.core.plan import steps as plan_steps
from ndf.progress import definition, invoke, issue_values, plan_runs

PROJECT = {
    "test": {"suites": [{"name": "pytest", "command": "pytest -q"}]},
    "ci": {"required_checks": ["pytest"]},
    "workflow": {"launch": {"review": ["ndf-review"]}},
}
PLAN_WITHOUT_UNITS = "# 3・4: 実装計画\n\n## タスク分解\n\n### Task 1: 直す\n- **対象ファイル:** `a.py`\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args], check=True)


def _sprint(tmp_path: Path, plan_text: str | None) -> Path:
    """ブランチ sprint/s1 に居る作業場所。`plan_text` があれば、課題 3・4 の実装計画としてコミットする。"""
    repo = sprint_fixture.sprint_repo(tmp_path, PROJECT)
    if plan_text is not None:
        (repo / "issues").mkdir()
        (repo / "issues" / "issue-3-4-plan.md").write_text(plan_text, encoding="utf-8")
        _git(repo, "add", "issues")
        _git(repo, "commit", "-q", "-m", "plan")
    return repo


@pytest.mark.parametrize("plan_text", [PLAN_WITHOUT_UNITS, None], ids=["見出しの無い実装計画", "実装計画が無い"])
def test_a_sprint_without_review_units_runs_one_impl_plan_on_the_current_branch(tmp_path, monkeypatch, plan_text):
    repo = _sprint(tmp_path, plan_text)
    sprint_fixture.with_worker(monkeypatch)
    monkeypatch.setattr(issue_values, "issue_text", lambda location, n, repo=None: (f"#{n} の題", f"#{n} の本文"))
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None, **_: "squash")
    stage = definition.load_definition("standard").stage("実装")
    context = {"issues": [3, 4], "worktree": str(repo), "mode": "standard", "branch": "sprint/registered"}
    run = invoke.stage_run(stage, stage.steps[0], invoke.resolve_inputs(stage, context))
    calls: list[list[str]] = []

    def runner(argv, cwd, env):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(result.result("plan", result.OK, "流した")), "")

    work = tmp_path / "w"
    got = invoke.invoke(run, repo, work, runner=runner)

    assert got.status == "ok", got.report
    assert run.name == "impl" and len(calls) == 1
    held = read_values(work)
    assert held is not None and held["values"]["branch"] == "sprint/s1"
    argv = calls[0]
    sets = dict(argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "--set")
    # `--set` に載るのは工程の入力だけで、ブランチは埋めたプランが持つ
    assert "branch" not in sets and sets["issues"] == "[3, 4]"
    assert json.loads(Path(argv[argv.index("next") + 1]).read_text())["name"] == "impl-sprint/s1"


# --- 呼び出しの追加の欄 ---------------------------------------------------------------------


def test_the_stage_run_copies_per_unit_and_the_plain_run_drops_it_with_the_branch():
    stage = replace(definition.load_definition("standard").stage("実装"), per_unit=True)
    inputs = {"issues": [3], "worktree": "/w", "mode": "standard", "branch": "sprint/s1"}
    run = invoke.stage_run(stage, stage.steps[0], inputs)
    assert run.per_unit is True
    plain = run.plain()
    assert plain.per_unit is False and plain.inputs == {"issues": [3], "worktree": "/w", "mode": "standard"}
    assert replace(plain, per_unit=True, inputs=run.inputs) == run and run.inputs == inputs


def test_the_stage_run_of_a_stage_without_per_unit_keeps_the_added_fields_empty():
    stage = definition.load_definition("standard").stage("設計")
    run = invoke.stage_run(stage, stage.steps[0], {})
    assert (run.per_unit, dict(run.overrides), run.template, dict(run.env_extra), run.start) == (False, {}, None, {}, None)


def _template(name: str, *steps: str, params: tuple[str, ...] = ()) -> route.Template:
    found = [{"id": s, "run": ["echo", *(f"{{{{{p}}}}}" for p in params)]} for s in steps]
    return route.Template(name, name, "試験のテンプレート", params, {"name": name, "steps": found})


def _step_ids(argv: list[str]) -> list[str]:
    return [s["id"] for s in json.loads(Path(argv[argv.index("next") + 1]).read_text())["steps"]]


def _ok(argv, cwd, env):
    return subprocess.CompletedProcess(argv, 0, json.dumps(result.result("plan", result.OK, "流した")), "")


def test_overrides_are_used_without_asking_the_sources(tmp_path, monkeypatch):
    t = _template("design", "a", params=("title", "branch", "base"))
    monkeypatch.setattr(route, "templates", lambda: {"design": t})
    monkeypatch.setattr(issue_values, "issue_text", lambda *a: pytest.fail("上書きした値は課題から写さない"))
    overrides = {"title": "単位の題", "branch": "sprint/s1-u3", "base": "sprint/s1"}
    run = invoke.StageRun(invoke.PLAN, "design", {"issues": [3]}, (), "none", "design", overrides=overrides)
    got = invoke.prepare_plan(run, tmp_path / "w", tmp_path)
    assert {k: got.values[k] for k in overrides} == overrides


def test_the_template_of_the_run_is_rendered_even_when_the_saved_values_are_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(route, "templates", lambda: {"design": _template("design", "done-check", "a")})
    run = invoke.StageRun(invoke.PLAN, "design", {}, (), "none", "design", template=_template("design", "a"))
    calls: list[list[str]] = []
    work = tmp_path / "w"
    for _ in range(2):  # 2 回目は保存した values.json を使い回す
        runner = lambda argv, cwd, env: calls.append(argv) or _ok(argv, cwd, env)  # noqa: E731
        assert invoke.invoke(run, tmp_path, work, runner=runner).status == "ok"
        assert _step_ids(calls[-1]) == ["a"]
    plain = replace(run, template=None)
    assert invoke.invoke(plain, tmp_path, work, runner=lambda *a: calls.append(a[0]) or _ok(*a)).status == "ok"
    assert _step_ids(calls[-1]) == ["done-check", "a"]


def test_a_run_with_its_own_template_does_not_need_one_of_the_same_name(tmp_path, monkeypatch):
    monkeypatch.setattr(route, "templates", lambda: {})
    run = invoke.StageRun(invoke.PLAN, "design", {}, (), "none", "design", template=_template("design", "a"))
    assert invoke.invoke(run, tmp_path, tmp_path / "w", runner=_ok).status == "ok"


def test_the_plan_gets_the_extra_environment_of_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(route, "templates", lambda: {"design": _template("design", "a")})
    extra = {"GH_REPO": "o/r", "NDF_UNIT_FILE": "/w/unit.md"}
    run = invoke.StageRun(invoke.PLAN, "design", {}, (), "none", "design", env_extra=extra, notes="直す")
    seen: list[dict[str, str]] = []
    invoke.invoke(run, tmp_path, tmp_path / "w", runner=lambda *a: seen.append(a[2]) or _ok(*a))
    assert {k: seen[0][k] for k in extra} == extra and seen[0][plan_runs.NOTES_ENV] == "直す"


def test_units_stopped_becomes_a_stopped_result(tmp_path, monkeypatch):
    monkeypatch.setattr(route, "templates", lambda: {"design": _template("design", "a")})

    def stop(*a):
        raise plan_runs.UnitsStopped("base_conflict", "単位 甲 のマージが衝突した", [{"kind": "unit", "name": "甲"}])

    monkeypatch.setattr(invoke, "plan_argv", stop)
    run = invoke.StageRun(invoke.PLAN, "design", {}, (), "none", "design")
    got = invoke.invoke(run, tmp_path, tmp_path / "w", runner=lambda *a: pytest.fail("打たない"))
    assert got.status == "stopped" and got.cause["kind"] == "base_conflict"
    assert "単位 甲 のマージが衝突した" in got.cause["detail"] and got.report["items"] == [{"kind": "unit", "name": "甲"}]


# --- 開始点 ---------------------------------------------------------------------------------


def _record(work: Path, name: str, start: str | None, stopped_at: str | None = None) -> None:
    """前の回の記録を置く。`stopped_at` があれば、そのステップで止まった結果のバックアップも置く。"""
    work.mkdir(exist_ok=True)
    plan_files.write_json(work / plan_runner.RUN_FILE, {"key": "k", "name": name, "start": start})
    if stopped_at is None:
        return
    done = result.stopped("plan", "test_failed", "落ちた")
    done["next"] = result.command("plan", "next", "p.json", "--from", stopped_at)
    path = plan_runner.result_path(work, durable.run_name("k"))
    path.parent.mkdir()
    plan_files.write_json(path, done)


def test_the_start_of_the_run_is_used_only_without_a_start_from_the_record(tmp_path, monkeypatch):
    monkeypatch.setattr(route, "templates", lambda: {"design": _template("design", "a", "b")})
    run = invoke.StageRun(invoke.PLAN, "design", {}, (), "none", "design", start="b")
    argv = invoke.plan_argv(run, tmp_path, None, tmp_path)
    assert argv[argv.index("--from") + 1] == "b"
    _record(tmp_path, "design", "b", stopped_at="a")
    argv = invoke.plan_argv(run, tmp_path, None, tmp_path)
    assert argv[argv.index("--from") + 1] == "a"


def _impl_plan(tmp_path: Path) -> Path:
    """番号を `probe` が書くステップと読むステップを持つ、テンプレート impl と同じ形の小さなプラン。"""
    steps = [
        {"id": "test", "run": ["true"]},
        {"id": "pr", "run": ["true", route.PR_NUMBER_FILE], "probe": ["true", route.PR_NUMBER_FILE]},
        {"id": "ci", "run": ["true", route.PR_NUMBER_FILE]},
    ]
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"name": "impl-sprint/s1", "steps": steps}), encoding="utf-8")
    return path


def _probed(monkeypatch, got: dict | None) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(plan_steps, "probe", lambda ctx, step, timeout: calls.append(step.id) or got)
    return calls


@pytest.mark.parametrize(("start", "saved", "asked"), [("ci", False, ["pr"]), ("ci", True, []), ("pr", False, []), ("test", False, [])])
def test_the_missing_pr_number_is_asked_with_the_probe_of_the_pr_step(tmp_path, monkeypatch, start, saved, asked):
    # PR の番号を保存する前に始めた実装は、PR を作った後で止まっていても番号を持たない
    calls = _probed(monkeypatch, {"status": "ok"})
    if saved:
        (tmp_path / "pr-number").write_text("7\n", encoding="utf-8")
    invoke.restore_pr_number(_impl_plan(tmp_path), tmp_path, start)
    assert calls == asked


@pytest.mark.parametrize(("start", "asked"), [("resolved", []), ("head-reviewed", ["pr"]), ("fix-push", [])])
def test_the_pr_number_is_asked_only_when_a_route_of_impl_reads_it(tmp_path, monkeypatch, start, asked):
    # resolved は指摘の有無を確かめて終わる。head-reviewed は通れば終わるが、止まった先の差分のレビューが番号を読む
    calls = _probed(monkeypatch, {"status": "ok"})
    plan = route.expanded(route.templates()["impl"])
    for step in plan["steps"]:
        step["run"] = [route.PR_NUMBER_FILE if a == "{{review}}" else a for a in step["run"]]
    path = tmp_path / "impl.json"
    path.write_text(json.dumps({**plan, "name": "impl-sprint/s1"}), encoding="utf-8")
    invoke.restore_pr_number(path, tmp_path, start)
    assert calls == asked


@pytest.mark.parametrize(
    ("got", "kind", "reason"),
    [
        (None, "precondition", "開いた PR が無い"),
        ({"status": "stopped", "kind": "precondition", "summary": "照会できない"}, "precondition", "照会できない"),
    ],
)
def test_the_resume_stops_with_the_reason_when_the_pr_number_is_not_restored(tmp_path, monkeypatch, got, kind, reason):
    _probed(monkeypatch, got)
    with pytest.raises(plan_runs.PrNumberUnrestored) as stopped:
        invoke.restore_pr_number(_impl_plan(tmp_path), tmp_path, "ci")
    report = invoke.plan_failure("impl", stopped.value)
    assert report["cause"]["kind"] == kind and reason in report["cause"]["detail"]


def test_a_plan_that_does_not_read_the_pr_number_is_not_asked(tmp_path, monkeypatch):
    calls = _probed(monkeypatch, None)
    path = _impl_plan(tmp_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    # 番号を書くステップの後ろから始めても、読むステップが無ければ照会しない
    steps = [plan["steps"][1], {"id": "after", "run": ["true"]}]
    path.write_text(json.dumps({**plan, "steps": steps}), encoding="utf-8")
    invoke.restore_pr_number(path, tmp_path, "after")
    assert calls == []


def test_an_impl_stopped_after_the_pr_keeps_its_start_without_the_pr_number(tmp_path):
    _record(tmp_path, "impl-sprint/s1", None, stopped_at="ci-fix")
    assert invoke.plan_start(tmp_path) == "ci-fix"


# --- 前の試行の値と設定 ---------------------------------------------------------------------


def test_clear_values_removes_the_values_of_the_units_and_keeps_the_records(tmp_path):
    work = tmp_path / "w"
    round_dir = work / "units" / "sprint" / "s1-u3-abc" / "1"  # 単位のブランチ名の `/` は入れ子のディレクトリになる
    write_values(round_dir, "impl", {}, {}, True)
    gone = [work / "units.json", round_dir / "issue-3-body.md", round_dir / "issue-3-title.json"]
    kept = [round_dir / plan_runner.RUN_FILE, round_dir / "merge.json", round_dir / "pr-number"]
    for path in (*gone, *kept):
        path.write_text("{}", encoding="utf-8")
    issue_values.clear_values([work, tmp_path / "無い"])
    assert [p for p in (*gone, round_dir / invoke.VALUES_FILE) if p.exists()] == []
    assert all(p.exists() for p in kept)


@pytest.mark.parametrize(("checks", "ok"), [([], True), (["pytest"], True), ([""], False), ("pytest", False)])
def test_the_workflow_settings_take_the_checks_a_unit_waits_for(checks, ok):
    errors = contracts.schema_errors({"unit_required_checks": checks}, invoke.WORKFLOW_SCHEMA)
    assert (errors == []) is ok
