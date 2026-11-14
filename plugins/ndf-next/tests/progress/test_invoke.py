"""工程から作業を呼ぶ層（ndf.progress.invoke。#1142 の設計の「工程から作業を呼ぶ層」・3364-7）。"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from ndf.adapters import github
from ndf.contracts import result
from ndf.core import route
from ndf.core.plan import cli as plan_cli
from ndf.core.plan import files as plan_files
from ndf.core.plan import records as plan_records
from ndf.core.plan import runner as plan_runner
from ndf.progress import definition, invoke, model, stages


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
    # 3364-7: 次の工程の引数・作業ディレクトリになる値は、鍵があるかだけでなく形を確かめてから渡す
    with pytest.raises(invoke.StageInputInvalid):
        invoke.resolve_inputs(_stage("コードレビュー"), values)


def test_delivery_must_not_be_empty():
    assert invoke.value_errors({"delivery": {}})
    assert not invoke.value_errors({"delivery": {"kind": "plugin"}})


def test_stage_run_refuses_an_unknown_call_and_a_missing_name():
    s = _stage("設計")
    bad = definition.stages.StepDef("x", s.name, "ndf.unknown", "work", "none")
    with pytest.raises(model.Refused):
        invoke.stage_run(s, bad, {})
    nameless = definition.stages.StepDef("y", s.name, "ndf.plan", "work", "none")
    with pytest.raises(model.Refused):
        invoke.stage_run(s, nameless, {})


def test_listen_step_is_not_invoked():
    run = _run("設計", 2)
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
    """ステップ a・b の雛形 1 本だけを `route.templates()` が返すようにする。`worktree` を受ければプランの作業場所へ差し込む。"""
    plan = {"name": name, "steps": STEPS, **({"worktree": "{{worktree}}"} if "worktree" in params else {})}
    t = route.Template(name, name, "試験の雛形", params, plan)
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
    assert argv[-2:] == ["--answer", "approved"]
    assert argv[1] == "-P"


def test_stage_plans_name_the_route_templates_where_one_fits():
    # 工程の with.plan は C3 の雛形の名前（route.templates() の鍵）。当たる雛形の無い工程は雛形が足されるまで止まる
    pairs = {
        (st.id, invoke.stage_run(s, st, {}).name)
        for mode in ("light", "standard")
        for s in definition.load_definition(mode).stages
        for st in s.steps
        if st.call == "ndf.plan"
    }
    assert {("implement", "impl"), ("design", "design"), ("design-merge", "design-merge")} <= pairs
    assert {"impl", "design", "design-merge"} <= set(route.templates())


def test_plan_gets_the_worktree_as_the_plans_place_and_not_as_a_set_value(tmp_path, monkeypatch):
    # PR 3464 の指摘: --set worktree=... はプランの実行が決まった名前の上書きとして断る
    _plan_template(monkeypatch, params=("worktree",))
    work, place = tmp_path / "w", str(tmp_path / "wt")
    work.mkdir()
    argv = invoke.plan_argv(_plan_run({"issues": [1], "worktree": place}), work, None, tmp_path)
    sets = [argv[i + 1] for i, a in enumerate(argv) if a == "--set"]
    assert sets == ["issues=[1]"]
    plan = Path(argv[argv.index("next") + 1])
    assert plan.parent == work and json.loads(plan.read_text())["worktree"] == place
    # プランの実行は同じ引数を断らずに受け、作業場所はプランの作業場所に入る
    inv = plan_runner.prepare(tmp_path / "root", plan, None, str(work), tmp_path, plan_cli._pairs(sets, "--set"))
    assert inv.plan.worktree == place and inv.values["worktree"] == place


def test_a_template_that_cannot_be_filled_stops_as_invalid(tmp_path, monkeypatch):
    # 差し込み worktree を受ける雛形に、作業場所も省いた作業場所を決めるブランチも無い
    _plan_template(monkeypatch, params=("worktree",))
    got = invoke.invoke(_plan_run({"issues": [1]}), tmp_path, tmp_path / "w", runner=lambda *a: pytest.fail("打たない"))
    assert got.status == "stopped" and got.cause["kind"] == "invalid" and "worktree" in got.cause["detail"]


def _sprint_repo(tmp_path: Path, project: dict | None = None) -> Path:
    """ブランチ sprint/s1 に居る作業場所（リモート無し）。宛先は宣言の base_branch、`project` は `.ndf/project.json`。"""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "sprint/s1", str(repo)], check=True)
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "worktree.json").write_text(json.dumps({"base_branch": "develop"}))
    if project is not None:
        (repo / ".ndf" / "project.json").write_text(json.dumps(project))
    return repo


PROJECT = {
    "test": {"suites": [{"name": "pytest", "command": "pytest -q"}, {"name": "lint", "kind": "lint", "command": "ruff check"}]},
    "ci": {"required_checks": ["pytest", "lint"]},
    "workflow": {"launch": {"review": ["ndf-review"], "rebuild": ["ndf-rebuild"]}},
}
# 雛形 → 工程の入力（設計は issues・worktree、実装は issues・worktree・mode、設計のマージは設計の工程の入力）
STAGE_INPUTS = {"design": ("issues", "worktree"), "impl": ("issues", "worktree", "mode"), "design-merge": ("issues", "worktree")}


def _stage_run_of(name: str, repo: Path, issues: list[int]) -> invoke.StageRun:
    inputs = {"issues": issues, "worktree": str(repo), "mode": "light"}
    return invoke.StageRun(invoke.PLAN, name, {k: inputs[k] for k in STAGE_INPUTS[name]}, (), "none", name)


@pytest.mark.parametrize(
    ("name", "missing"),
    [
        ("design", {"title", "body_file", "worker", "rebuild", "review", "required"}),
        ("impl", {"title", "body_file", "test_command", "worker", "required"}),
        ("design-merge", {"worker", "gate"}),
    ],
)
def test_template_values_missing_from_every_source_stop_as_invalid_by_name(tmp_path, monkeypatch, name, missing):
    # PR 3464 の指摘（#3092）: 雛形の必須の差し込みが工程の入力にも文脈にも既定にも無いと、実行の前に invalid で止まる。
    # 出所（課題・宣言・既定）のどれからも決まらなければ、足りない名前と出所を挙げて止まる
    repo = _sprint_repo(tmp_path)
    monkeypatch.setattr(invoke, "issue_text", lambda *a: pytest.fail("課題が 2 つなら課題から写さない"))
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base: "squash")
    got = invoke.invoke(_stage_run_of(name, repo, [1, 2]), repo, tmp_path / "w", runner=lambda *a: pytest.fail("打たない"))
    assert got.status == "stopped" and got.cause["kind"] == "invalid"
    named = {i["key"]: i["source"] for i in got.report["items"]}
    assert set(named) >= missing and "branch" not in named
    assert all(n in got.cause["detail"] for n in missing)
    assert named.get("worker") == invoke.FROM_DEFAULT and named.get("title", invoke.FROM_ISSUE) == invoke.FROM_ISSUE


@pytest.mark.parametrize("name", ["design", "impl", "design-merge"])
def test_template_values_are_filled_from_the_issue_the_declaration_and_the_defaults(tmp_path, monkeypatch, name):
    # #3092: 課題の題と本文は課題から、テストのコマンド・必須のチェック・レビューと組み直しの起動は宣言から、
    # worker と承認ゲートの起動は params.json の既定から埋め、ブランチは作業場所から決める
    repo = _sprint_repo(tmp_path, PROJECT)
    defaults = {"worker": ["ndf-worker"], "gate": ["ndf-gate"]}
    held = route.params()
    patched = {n: replace(p, default=defaults[n]) if n in defaults else p for n, p in held.items()}
    monkeypatch.setattr(route, "params", lambda: patched)
    monkeypatch.setattr(invoke, "issue_text", lambda place, n: (f"#{n} の題", f"#{n} の本文"))
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base: "squash")
    work = tmp_path / "w"
    work.mkdir()
    run = _stage_run_of(name, repo, [3092])
    argv = invoke.plan_argv(run, work, None, repo)
    plan = json.loads(Path(argv[argv.index("next") + 1]).read_text())
    text = json.dumps(plan, ensure_ascii=False)
    assert plan["worktree"] == str(repo) and "sprint/s1" in plan["name"]
    assert "ndf-worker" in text and "{{" not in text
    t = route.templates()[name]
    if "title" in t.params:
        assert "#3092 の題" in text
        body = work / invoke.ISSUE_BODY_FILE.format(number=3092)
        assert body.read_text() == "#3092 の本文" and str(body) in text
    if "test_command" in t.params:
        assert "pytest -q" in text and "ruff check" not in text
    if "required" in t.params:
        assert "--required=pytest" in text and "--required=lint" in text
    if "review" in t.params:
        assert "ndf-review" in text and "ndf-rebuild" in text
    if "gate" in t.params:
        assert "ndf-gate" in text


def test_test_command_joins_every_test_suite_in_declared_order():
    # PR 3469（lr-flow の C3 の保留分）: テストの suite が複数あれば、宣言の順に && でつないだ 1 つのコマンドにする。
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


def test_an_issue_by_an_untrusted_author_is_not_written_and_stops_as_invalid_by_name(tmp_path, monkeypatch):
    # PR 3469 の指摘（#3364）: write 以上の権限が無い書き手の課題の本文を、指示として読まれる作業の入力へ写さない。
    # 題と本文は欠けとして、信用しない出所を挙げて止まり、本文のファイルは書かない
    repo = _sprint_repo(tmp_path, PROJECT)
    monkeypatch.setattr(invoke, "issue_text", lambda place, n: None)
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base: "squash")
    work = tmp_path / "w"
    work.mkdir()
    got = invoke.invoke(_stage_run_of("impl", repo, [3092]), repo, work, runner=lambda *a: pytest.fail("打たない"))
    assert got.status == "stopped" and got.cause["kind"] == "invalid"
    named = {i["key"]: i["source"] for i in got.report["items"]}
    assert named["title"] == invoke.FROM_UNTRUSTED_ISSUE and named["body_file"] == invoke.FROM_UNTRUSTED_ISSUE
    assert not (work / invoke.ISSUE_BODY_FILE.format(number=3092)).exists()


def test_the_issue_is_read_once_and_later_calls_use_the_copy_in_the_work_dir(tmp_path, monkeypatch):
    # PR 3469 の指摘: プランを描き直すたび（承認の答え・続き・打ち直し）に課題を GitHub から読み直さない。
    # 途中で題が直されてもプランは同じで、終わっていない回へ同じ鍵で届く
    repo = _sprint_repo(tmp_path, PROJECT)
    reads = []

    def read(place, n):
        reads.append(n)
        return (f"#{n} の題（{len(reads)} 回目）", f"#{n} の本文（{len(reads)} 回目）")

    monkeypatch.setattr(invoke, "issue_text", read)
    work = tmp_path / "w"
    work.mkdir()
    run = _stage_run_of("impl", repo, [3092])
    first = invoke._issue_values(run, work, repo)
    again = invoke._issue_values(run, work, repo)
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

    def fail(place, n):
        raise error

    monkeypatch.setattr(invoke, "issue_text", fail)
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base: "squash")
    work = tmp_path / "w"
    work.mkdir()
    got = invoke.invoke(_stage_run_of("impl", repo, [3092]), repo, work, runner=lambda *a: pytest.fail("打たない"))
    assert got.status == "stopped" and got.cause["kind"] == kind
    assert str(error) in got.cause["detail"]
    assert not (work / invoke.ISSUE_BODY_FILE.format(number=3092)).exists()


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
    assert invoke.issue_text(Path("."), 1) is None
    authored = github.Authored("owner", "OWNER", "本文")
    assert invoke.issue_text(Path("."), 1) == ("題", "本文")


def test_every_template_value_has_one_source():
    # 出所の表は差し込みの定義（params.json）の名前だけを持つ
    assert set(invoke.FILL_SOURCES) <= set(route.params())
    assert set(invoke.PROJECT_KEYS) <= {n for n, s in invoke.FILL_SOURCES.items() if s == invoke.FROM_PROJECT}


def test_a_stopped_plan_is_tried_again_from_the_step_it_stopped_at_in_a_new_round(tmp_path, monkeypatch):
    # PR 3464 の指摘: 同じ引数で呼び直すと、プランは前の回の控えの失敗を返すだけ
    _plan_template(monkeypatch)
    work, root = tmp_path / "w", tmp_path / "root"
    work.mkdir()
    run = _plan_run({"issues": [1]})
    first = invoke.plan_argv(run, work, None, tmp_path)
    assert "--from" not in first
    plan = Path(first[first.index("next") + 1])
    inv = plan_runner.prepare(root, plan, None, str(work), tmp_path, {"issues": "[1]"})
    plan_runner._record_run(inv)
    final = {"status": result.STOPPED, "kind": "test_failed", "detail": "落ちた", "last": "b", "count": 2}
    plan_files.write_json(inv.result_path, plan_runner.final_result(inv, final, "SUCCESS"))
    again = invoke.plan_argv(run, work, None, tmp_path)
    assert again[again.index("--from") + 1] == "b" and again.index("--from") < again.index("--state-dir")
    retried = plan_runner.prepare(root, plan, "b", str(work), tmp_path, {"issues": "[1]"})
    assert retried.key != inv.key and retried.round_no == inv.round_no + 1
    # 新しい回が承認ゲートで待つ間（結果の控えが無い）は、答えを渡す呼び出しも同じ開始点で同じ回に届く
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
    """`argv` の呼び出しでプランの回を流し、ステップ `last` で止まった結果を控えに残す。"""
    start = argv[argv.index("--from") + 1] if "--from" in argv else None
    sets = plan_cli._pairs([argv[i + 1] for i, a in enumerate(argv) if a == "--set"], "--set")
    inv = plan_runner.prepare(root, plan, start, str(work), work, sets)
    plan_runner._record_run(inv)
    final = {"status": result.STOPPED, "kind": "test_failed", "detail": "落ちた", "last": last, "count": 2}
    plan_files.write_json(inv.result_path, plan_runner.final_result(inv, final, "SUCCESS"))
    return inv


def test_every_retry_of_a_plan_starts_a_new_round(tmp_path, monkeypatch):
    # PR 3464 の指摘: 2 回目以降の打ち直しも、開始点と head が前の回と同じでプランが控えの結果を返すだけにならない
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
    # 打ち直さない呼び出し（作業が落ちた後の続き・答え）は同じ回に届く
    argv = invoke.plan_argv(run, work, None, tmp_path)
    assert f"{invoke.ATTEMPT_SET}=3" in argv


def _gate_ended(tmp_path: Path, monkeypatch) -> tuple[invoke.StageRun, Path]:
    """プランの回がステップ a の承認ゲートで終わった（結果の控えが gate）置き場。"""
    _plan_template(monkeypatch)
    work = tmp_path / "w"
    work.mkdir()
    run = _plan_run({"issues": [1]})
    argv = invoke.plan_argv(run, work, None, tmp_path)
    plan = Path(argv[argv.index("next") + 1])
    inv = plan_runner.prepare(tmp_path / "root", plan, None, str(work), work, {"issues": "[1]"})
    plan_runner._record_run(inv)
    final = {"status": "gate", "kind": None, "detail": "判断を待つ", "last": "a", "count": 1}
    plan_files.write_json(inv.result_path, plan_runner.final_result(inv, final, "SUCCESS"))
    return run, work


def test_an_answer_to_a_plan_that_ended_at_a_gate_starts_where_the_route_points(tmp_path, monkeypatch):
    # PR 3464 の指摘: 終わった回へ --answer を渡すと、プランは控えの gate を返し続ける
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
    place = tmp_path / "wt"
    place.mkdir()
    seen = []
    run = invoke.StageRun(invoke.SCRIPT, "true", {"worktree": str(place)}, (), "sprint_diff", "s", {"argv": ["true"]})
    ok = subprocess.CompletedProcess([], 0, json.dumps(result.result("script", "ok", "済んだ")), "")
    invoke.invoke(run, tmp_path, tmp_path / "w", runner=lambda argv, cwd, env: seen.append(cwd) or ok)
    assert seen == [place]
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
    # PR 3464 の指摘: プランの結果は版を outputs に集め、metrics には回とステップの数だけを載せる
    work = tmp_path / "w"
    inv = plan_runner.prepare(tmp_path / "root", _plan_file(tmp_path, "release"), None, str(work), tmp_path)
    plan_records._add_outputs(work, {"version": "11.0.0"})
    out = plan_runner.final_result(inv, {"status": result.OK, "kind": None, "detail": "", "last": "b", "count": 2}, "SUCCESS")
    step = next(s for s in _stage("デプロイ").steps if s.id == "release")
    assert "version" not in out["metrics"]
    assert definition.validate_io(step.output_ref, out) == []
    assert definition.validate_io(step.output_ref, result.result("plan", "ok", "出した", metrics={"version": "11.0.0"}))


def test_plan_without_a_template_stops_as_precondition(tmp_path):
    # 作業場所の用意の with.plan（worktree）に当たる雛形は C3 にまだ無い
    run = _run("作業場所の用意", inputs={"issues": [1]})
    assert run.name not in route.templates()
    got = invoke.invoke(run, tmp_path, tmp_path / "w", runner=lambda *a: pytest.fail("打たない"))
    assert got.status == "stopped" and got.cause["kind"] == "precondition"
    assert got.cause["detail"] == f"プランの雛形 {run.name} が無い"
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
    assert str(invoke.SRC) in seen["env"]["PYTHONPATH"].split(os.pathsep)
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


# --- 現状固定: _call_skill（I-025。`invoke` の call_claude で差し替えた `claude -p` の呼び出しと、答えから結果への写し） ---------------------


def _skill_run() -> invoke.StageRun:
    return invoke.StageRun("skill", "design", {"issues": [5]}, ("pr",), "none", "s", {"skill": "design"})


def _reply(**kw):
    from ndf.adapters import claude

    return claude.ClaudeReply(**{"ok": True, "answer": None, "raw": "", "cost": 0.0, **kw})


@pytest.fixture
def calls():
    """`claude.call_claude` を差し替え、受けた引数を残す。答えは `calls.reply` に置く。"""
    # 差し替えは `invoke` の `call_claude` へ偽物を渡して行う。答えは `box["reply"]` に置く
    seen: list[dict] = []
    box = {"reply": _reply()}

    def fake(argv, prompt, timeout, cwd=None):
        seen.append({"argv": list(argv), "prompt": prompt, "timeout": timeout, "cwd": cwd})
        return box["reply"]

    return box, seen, fake


def test_skill_calls_claude_call_claude_by_default():
    import inspect

    from ndf.adapters import claude

    assert inspect.signature(invoke.invoke).parameters["call_claude"].default is claude.call_claude


def test_skill_is_called_with_full_permission_the_result_schema_and_the_implement_limit(tmp_path, calls):
    from ndf import contracts
    from ndf.adapters import claude
    from ndf.contracts import limits

    box, seen, fake = calls
    run = _skill_run()
    box["reply"] = _reply(answer=result.result("skill", "ok", "済んだ", outputs={"pr": 3}))
    got = invoke.invoke(run, tmp_path, tmp_path / "w", call_claude=fake)
    schema = contracts.load_json(contracts.schema_path("result"))
    want = claude.claude_args(claude.ClaudeCall(system=invoke.SKILL_SYSTEM, permission=claude.Permission.FULL, json_schema=schema))
    assert seen == [{"argv": want, "prompt": invoke.skill_prompt(run), "timeout": limits.seconds(invoke.SKILL_LIMIT), "cwd": tmp_path}]
    assert got.status == "ok" and got.outputs == {"pr": 3} and got.warnings == ()
    # Skill の作業はプランの置き場を作らない
    assert not (tmp_path / "w").exists()


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
    assert got.cause["detail"] == "作業 skill design が結果 JSON を返さない: 答えが結果 JSON の形に合わない"


def test_skill_answer_keeps_only_declared_outputs(tmp_path, calls):
    box, _, fake = calls
    box["reply"] = _reply(answer=result.result("skill", "ok", "済んだ", outputs={"pr": 3, "branch": "b"}))
    got = invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=fake)
    # outputs の鍵は文脈の閉じた語彙なので、宣言に無い鍵は語彙の中の別の鍵で確かめる
    assert got.outputs == {"pr": 3} and got.warnings and "branch" in got.warnings[0]
