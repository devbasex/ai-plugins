"""差し込みを 1 回だけ組んで values.json に残し、決められなければ起動の前に返す（ndf.progress.invoke。#3908 の決定 2・4、受け入れ条件 3）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sprint_fixture
from sprint_fixture import sprint_repo as _sprint_repo

from ndf.adapters import github
from ndf.core import route
from ndf.progress import definition, invoke

# 設計は issues・worktree、実装は issues・worktree・mode を工程の入力に持つ
STAGE_INPUTS = {"design": ("issues", "worktree"), "impl": ("issues", "worktree", "mode"), "design-merge": ("issues", "worktree")}
PROJECT = {
    "test": {"suites": [{"name": "pytest", "command": "pytest -q"}]},
    "ci": {"required_checks": ["pytest"]},
    "workflow": {"launch": {"review": ["ndf-review"], "rebuild": ["ndf-rebuild"]}},
}


def _run(stage_name: str, inputs: dict) -> invoke.StageRun:
    s = definition.load_definition("light").stage(stage_name)
    return invoke.stage_run(s, s.steps[0], inputs)


def _plan_template(monkeypatch, name: str = "design") -> route.Template:
    """ステップ a・b のテンプレート 1 本だけを `route.templates()` が返すようにする。"""
    plan = {"name": name, "steps": [{"id": "a", "run": ["true"]}, {"id": "b", "run": ["true"]}]}
    t = route.Template(name, name, "試験のテンプレート", (), plan)
    monkeypatch.setattr(route, "templates", lambda: {name: t})
    return t


def _plan_run(inputs: dict, name: str = "design") -> invoke.StageRun:
    return invoke.StageRun(invoke.PLAN, name, inputs, (), "sprint_diff", "design")


def _stage_run_of(name: str, repo: Path, issues: list[int]) -> invoke.StageRun:
    inputs = {"issues": issues, "worktree": str(repo), "mode": "light"}
    return invoke.StageRun(invoke.PLAN, name, {k: inputs[k] for k in STAGE_INPUTS[name]}, (), "none", name)


def _issue_repo(tmp_path: Path, monkeypatch) -> Path:
    """設定 `PROJECT` を持つ作業場所。worker の既定を置き、課題の題と本文は偽の課題から写す。"""
    repo = _sprint_repo(tmp_path, PROJECT)
    sprint_fixture.with_worker(monkeypatch)
    monkeypatch.setattr(invoke, "issue_text", lambda location, n: (f"#{n} の題", f"#{n} の本文"))
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None, **_: "squash")
    return repo


PLAN_ERRORS = [
    (invoke.TemplateValuesMissing("design", {"title": invoke.FROM_ISSUE}), "precondition"),
    (route.RouteError("形が違う"), "invalid"),
    (invoke.GitError("リモートが無い"), "precondition"),
    (invoke.FactUnknown("起点が無い"), "precondition"),
    (github.GitHubError("5xx", status=502, retry_after=30.0), "transient"),
    (github.GitHubError("認証が無い"), "precondition"),
    (OSError("書けない"), "precondition"),
]


@pytest.mark.parametrize(("error", "kind"), PLAN_ERRORS)
def test_call_plan_sorts_the_failures_of_building_the_plan_by_type(tmp_path, monkeypatch, error, kind):
    _plan_template(monkeypatch)

    def fail(*a):
        raise error

    monkeypatch.setattr(invoke, "plan_argv", fail)
    got = invoke.invoke(_plan_run({"issues": [1]}), tmp_path, tmp_path / "w", runner=lambda *a: pytest.fail("打たない"))
    # 工程から呼ぶ経路も起動の前の確かめと同じ plan_failure を通る（2 つの経路で分類が分かれない）
    assert got.status == "stopped" and got.cause["kind"] == kind and got.report == invoke.plan_failure("design", error)


@pytest.mark.parametrize(("error", "kind"), PLAN_ERRORS)
def test_plan_failure_sorts_like_the_call_plan_and_only_missing_values_turn_to_precondition(error, kind):
    # 決定 4: 差し込みの欠けは値を足せば通るので invalid でなく precondition。ほかは今の _call_plan の分類のまま
    got = invoke.plan_failure("design", error)
    assert got is not None and got["status"] == "stopped" and got["cause"]["kind"] == kind


def test_plan_failure_lists_the_missing_values_by_name_and_source():
    got = invoke.plan_failure("design", invoke.TemplateValuesMissing("design", {"title": invoke.FROM_ISSUE, "worker": invoke.FROM_DEFAULT}))
    assert got is not None and got["items"] == [
        {"key": "title", "source": invoke.FROM_ISSUE},
        {"key": "worker", "source": invoke.FROM_DEFAULT},
    ]
    assert got["cause"]["detail"] == "プラン design の差し込み title, worker を決められない"


def test_plan_failure_keeps_the_wait_of_a_passing_github_failure_and_leaves_other_errors_to_the_caller():
    got = invoke.plan_failure("design", github.GitHubError("上限", status=403, retry_after=60.0, quota="rest"))
    assert got is not None and got["cause"]["kind"] == "transient" and got["cause"]["retry_after"] == 60.0
    assert invoke.plan_failure("design", ValueError("分類に無い")) is None


def test_read_values_returns_what_was_written_and_the_last_write_wins(tmp_path):
    invoke.write_values(tmp_path, "design", {"title": "題"}, {"issues": [1]}, True)
    invoke.write_values(tmp_path, "design", {"title": "直した題"}, {"issues": [1]}, False)
    got = invoke.read_values(tmp_path)
    assert got is not None and {k: got[k] for k in ("plan", "values", "inputs", "local_tests")} == {
        "plan": "design",
        "values": {"title": "直した題"},
        "inputs": {"issues": [1]},
        "local_tests": False,
    }
    assert isinstance(got["at"], str) and got["at"]


@pytest.mark.parametrize("text", [None, "{壊れた", json.dumps({"plan": "design", "values": {}, "local_tests": True, "at": "t"})])
def test_read_values_is_none_without_a_whole_file(tmp_path, text):
    # 無い・壊れている・inputs が無い
    if text is not None:
        (tmp_path / invoke.VALUES_FILE).write_text(text, encoding="utf-8")
    assert invoke.read_values(tmp_path) is None


def test_clear_values_removes_the_file_from_each_dir_and_skips_dirs_without_it(tmp_path):
    held, bare = tmp_path / "a", tmp_path / "b"
    invoke.write_values(held, "design", {}, {}, True)
    invoke.clear_values([held, bare])
    assert not (held / invoke.VALUES_FILE).exists() and not bare.exists()


def test_clear_values_removes_the_issue_copies_with_or_without_the_values(tmp_path):
    # 差し込みを組み終える前に止まった試行（values.json が無い）の写しも消し、新しい試行で課題と書き手の権限を読み直す
    # （PR 4289 の指摘）
    done, stopped = tmp_path / "a", tmp_path / "b"
    for d in (done, stopped):
        d.mkdir()
        (d / invoke.ISSUE_BODY_FILE.format(number=7)).write_text("本文", encoding="utf-8")
        (d / invoke.ISSUE_TITLE_FILE.format(number=7)).write_text("{}", encoding="utf-8")
    invoke.write_values(done, "close", {}, {}, True)
    invoke.clear_values([done, stopped])
    assert list(done.iterdir()) == [] and list(stopped.iterdir()) == []


def _counting(monkeypatch, name: str) -> list:
    """`invoke.<name>` を、呼ばれた回数を数えて元へ渡す見張りにする。"""
    held, calls = getattr(invoke, name), []

    def watch(*a, **k):
        calls.append(a)
        return held(*a, **k)

    monkeypatch.setattr(invoke, name, watch)
    return calls


def test_plan_path_leaves_the_values_it_built_the_plan_with(tmp_path, monkeypatch):
    repo = _issue_repo(tmp_path, monkeypatch)
    work = tmp_path / "w"
    text = invoke.plan_path(_stage_run_of("impl", repo, [3092]), work, repo).read_text(encoding="utf-8")
    held = invoke.read_values(work)
    assert held is not None and held["plan"] == "impl" and held["local_tests"] is True
    assert held["inputs"] == {"issues": [3092], "worktree": str(repo), "mode": "light"}
    assert held["values"]["title"] == "#3092 の題" and held["values"]["body_file"] in text
    assert held["values"]["acceptance"] == ["--issue", "3092"]


def test_plan_path_draws_from_the_values_already_built_and_does_not_build_again(tmp_path, monkeypatch):
    repo = _issue_repo(tmp_path, monkeypatch)
    built, read = _counting(monkeypatch, "template_values"), _counting(monkeypatch, "issue_text")
    work = tmp_path / "w"
    run = _stage_run_of("design", repo, [3092])
    invoke.prepare_plan(run, work, repo)
    first = json.loads(invoke.plan_path(run, work, repo).read_text())
    again = json.loads(invoke.plan_path(run, work, repo).read_text())
    assert len(built) == 1 and len(read) == 1 and first == again
    assert (work / invoke.ISSUE_BODY_FILE.format(number=3092)).is_file()


def test_plan_path_builds_again_over_the_values_of_another_plan(tmp_path, monkeypatch):
    # 工程の定義を書き換えた後の打ち直し: 同じ保存先に別のテンプレートの values.json があれば使わずに組み直す
    _plan_template(monkeypatch)
    work = tmp_path / "w"
    invoke.write_values(work, "impl", {"title": "別のプラン"}, {}, True)
    built = _counting(monkeypatch, "template_values")
    invoke.plan_path(_plan_run({"issues": [1]}), work, tmp_path)
    held = invoke.read_values(work)
    assert len(built) == 1 and held is not None and held["plan"] == "design"


def test_prepare_plan_leaves_no_values_when_a_value_cannot_be_decided(tmp_path, monkeypatch):
    repo = _sprint_repo(tmp_path)
    monkeypatch.setattr(invoke, "issue_text", lambda *a: pytest.fail("課題が 2 つなら課題から写さない"))
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None, **_: "squash")
    work = tmp_path / "w"
    with pytest.raises(invoke.TemplateValuesMissing):
        invoke.prepare_plan(_stage_run_of("design-merge", repo, [1, 2]), work, repo)
    assert invoke.read_values(work) is None


def test_prepare_plan_names_the_input_the_work_needs_when_the_stage_lacks_it(tmp_path, monkeypatch):
    # 工程「要求と受け入れ条件」の作業 requirements は工程の入力 issues を読む。無ければ起動の前に名前と出所で止まる
    repo = _sprint_repo(tmp_path)
    sprint_fixture.with_worker(monkeypatch)
    run = _run("要求と受け入れ条件", inputs={})
    work = tmp_path / "w"
    with pytest.raises(invoke.KindInputsMissing) as caught:
        invoke.prepare_plan(run, work, repo)
    assert set(caught.value.missing) == {"issues"} and "issues" in caught.value.missing["issues"]
    assert caught.value.items == [{"key": "issues", "source": caught.value.missing["issues"]}]
    assert invoke.read_values(work) is None
    got = invoke.plan_failure(run.name, caught.value)
    assert got is not None and got["cause"]["kind"] == "precondition" and got["items"] == caught.value.items


def test_prepare_plan_keeps_the_stage_inputs_the_template_does_not_take(tmp_path, monkeypatch):
    repo = _sprint_repo(tmp_path)
    sprint_fixture.with_worker(monkeypatch)
    work = tmp_path / "w"
    got = invoke.prepare_plan(_run("要求と受け入れ条件", inputs={"issues": [3908]}), work, repo)
    held = invoke.read_values(work)
    assert got.template.name == "requirements" and got.local_tests is True and "issues" not in got.values
    assert held is not None and held["inputs"] == {"issues": [3908]} and held["values"] == got.values


def test_prepare_plan_for_a_ci_only_project_leaves_local_tests_off(tmp_path, monkeypatch):
    project = {**PROJECT, "test": {"ci": {"only": True}, "suites": [{"name": "lint", "kind": "lint", "command": "ruff check"}]}}
    repo = _sprint_repo(tmp_path, project)
    sprint_fixture.with_worker(monkeypatch)
    monkeypatch.setattr(invoke, "issue_text", lambda location, n: (f"#{n} の題", f"#{n} の本文"))
    work = tmp_path / "w"
    got = invoke.prepare_plan(_stage_run_of("impl", repo, [3473]), work, repo)
    held = invoke.read_values(work)
    assert got.local_tests is False and "test_command" not in got.values
    assert held is not None and held["local_tests"] is False


def test_prepare_plan_raises_when_the_values_cannot_be_written(tmp_path, monkeypatch):
    _plan_template(monkeypatch)
    work = tmp_path / "w"
    work.mkdir()
    work.chmod(0o500)
    try:
        with pytest.raises(OSError):
            invoke.prepare_plan(_plan_run({"issues": [1]}), work, tmp_path)
    finally:
        work.chmod(0o700)
    assert invoke.read_values(work) is None
