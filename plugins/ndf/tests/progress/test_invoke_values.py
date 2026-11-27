"""差し込みを 1 回だけ組んで values.json に残し、決められなければ起動の前に返す（ndf.progress.invoke。#3908 の決定 2・4、受け入れ条件 3）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import sprint_fixture
from sprint_fixture import sprint_repo as _sprint_repo

from ndf.adapters import github
from ndf.contracts import worker_paths
from ndf.core import route
from ndf.progress import definition, invoke, issue_values

# 設計と実装は issues・worktree・mode を工程の入力に持つ
STAGE_INPUTS = {"design": ("issues", "worktree", "mode"), "impl": ("issues", "worktree", "mode"), "design-merge": ("issues", "worktree")}
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
    """設定 `PROJECT` を持つ作業場所。worker の既定を置き、課題の題と本文は偽の課題からコピーする。"""
    repo = _sprint_repo(tmp_path, PROJECT)
    sprint_fixture.with_worker(monkeypatch)
    monkeypatch.setattr(issue_values, "issue_text", lambda location, n, repo=None: (f"#{n} の題", f"#{n} の本文"))
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


def test_values_are_written_without_others_write_under_a_group_writable_umask(tmp_path):
    # PR 4307 の r6-2・r6-7: umask によらず書き込み権限を外して公開し、同じ工程が作った値を `ndf answer work` が拒まない
    before = os.umask(0o002)
    try:
        invoke.write_values(tmp_path, "design", {}, {}, True)
    finally:
        os.umask(before)
    assert (tmp_path / invoke.VALUES_FILE).stat().st_mode & 0o022 == 0 and invoke.read_values(tmp_path) is not None


@pytest.mark.parametrize("planted", ["values.tmp", invoke.VALUES_FILE])
def test_writing_values_leaves_the_target_of_a_planted_link_alone(tmp_path, planted):
    # PR 4307 の r6-1・r6-2: 共有の保存先に置かれたリンクの先を切り詰めず、アクセス権も変えない
    target = tmp_path / "利用者のファイル"
    target.write_text("消さない", encoding="utf-8")
    target.chmod(0o664)
    (tmp_path / planted).symlink_to(target)
    if planted == invoke.VALUES_FILE:
        with pytest.raises(worker_paths.EntryUnsafe):  # リンクの名前は置き換えずに止める（`plan_failure` が precondition にする）
            invoke.write_values(tmp_path, "design", {}, {}, True)
    else:
        invoke.write_values(tmp_path, "design", {}, {}, True)
        assert invoke.read_values(tmp_path) is not None
    assert target.read_text(encoding="utf-8") == "消さない" and target.stat().st_mode & 0o777 == 0o664


def test_values_others_can_write_are_refused_on_the_stage_path_too(tmp_path):
    # PR 4307 の r6-3: 工程の描画（`_prepared` が読む `read_values`）も、ほかの利用者が書き換えられる値を使わない
    invoke.write_values(tmp_path, "design", {}, {}, True)
    (tmp_path / invoke.VALUES_FILE).chmod(0o666)
    with pytest.raises(worker_paths.EntryUnsafe) as caught:
        invoke.read_values(tmp_path)
    failed = invoke.plan_failure("design", caught.value)
    assert failed is not None and failed["cause"]["kind"] == "precondition"


def test_clear_values_removes_the_file_from_each_dir_and_skips_dirs_without_it(tmp_path):
    held, bare = tmp_path / "a", tmp_path / "b"
    invoke.write_values(held, "design", {}, {}, True)
    issue_values.clear_values([held, bare])
    assert not (held / invoke.VALUES_FILE).exists() and not bare.exists()


def test_clear_values_removes_the_issue_copies_with_or_without_the_values(tmp_path):
    # 差し込みを組み終える前に止まった試行（values.json が無い）のコピーも消し、新しい試行で課題と作成者の権限を読み直す
    # （PR 4289 の指摘）
    done, stopped = tmp_path / "a", tmp_path / "b"
    for d in (done, stopped):
        d.mkdir()
        (d / issue_values.ISSUE_BODY_FILE.format(number=7)).write_text("本文", encoding="utf-8")
        (d / issue_values.ISSUE_TITLE_FILE.format(number=7)).write_text("{}", encoding="utf-8")
    invoke.write_values(done, "close", {}, {}, True)
    issue_values.clear_values([done, stopped])
    assert list(done.iterdir()) == [] and list(stopped.iterdir()) == []


def _counting(monkeypatch, name: str, module=invoke) -> list:
    """`<module>.<name>`（既定は `invoke`）を、呼ばれた回数を数えて元へ渡す見張りにする。"""
    held, calls = getattr(module, name), []

    def watch(*a, **k):
        calls.append(a)
        return held(*a, **k)

    monkeypatch.setattr(module, name, watch)
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
    built, read = _counting(monkeypatch, "template_values"), _counting(monkeypatch, "issue_text", issue_values)
    work = tmp_path / "w"
    run = _stage_run_of("design", repo, [3092])
    invoke.prepare_plan(run, work, repo)
    first = json.loads(invoke.plan_path(run, work, repo).read_text())
    again = json.loads(invoke.plan_path(run, work, repo).read_text())
    assert len(built) == 1 and len(read) == 1 and first == again
    assert (work / issue_values.ISSUE_BODY_FILE.format(number=3092)).is_file()


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
    sprint_fixture.without_worker(monkeypatch)
    monkeypatch.setattr(issue_values, "issue_text", lambda *a: pytest.fail("課題が 2 つなら課題からコピーしない"))
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None, **_: "squash")
    work = tmp_path / "w"
    with pytest.raises(invoke.TemplateValuesMissing):
        invoke.prepare_plan(_stage_run_of("design-merge", repo, [1, 2]), work, repo)
    assert invoke.read_values(work) is None


def test_prepare_plan_names_the_input_the_work_needs_when_the_stage_lacks_it(tmp_path, monkeypatch):
    # 工程「要求と受け入れ条件」の作業 requirements は工程の入力 issues を読む。無ければ起動の前に名前と出所で止まる
    repo = _sprint_repo(tmp_path)
    sprint_fixture.with_worker(monkeypatch)
    run = _run("要求と受け入れ条件", inputs={"mode": "light"})
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
    got = invoke.prepare_plan(_run("要求と受け入れ条件", inputs={"issues": [3908], "mode": "light"}), work, repo)
    held = invoke.read_values(work)
    assert got.template.name == "requirements" and got.local_tests is True and "issues" not in got.values
    assert held is not None and held["inputs"] == {"issues": [3908], "mode": "light"} and held["values"] == got.values


def test_prepare_plan_for_a_ci_only_project_leaves_local_tests_off(tmp_path, monkeypatch):
    project = {**PROJECT, "test": {"ci": {"only": True}, "suites": [{"name": "lint", "kind": "lint", "command": "ruff check"}]}}
    repo = _sprint_repo(tmp_path, project)
    sprint_fixture.with_worker(monkeypatch)
    monkeypatch.setattr(issue_values, "issue_text", lambda location, n, repo=None: (f"#{n} の題", f"#{n} の本文"))
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


# --- #4513: 記録済みの値を使うプランだけが、記録済みの値を values.json に残す -------------------------------------------------

from dataclasses import replace  # noqa: E402

from ndf.contracts import worker_kinds  # noqa: E402

ROW = {"target": "本番", "kind": "manual", "trigger": "手で出す", "versioned": False}
NOW = worker_kinds.Recorded("main abc1234", ROW, "none")


def _release_template(monkeypatch, name: str = "release-x", work: str = "release", params=("worker",)) -> route.Template:
    """作業 `work` を既定の worker で起動するステップ 1 つのテンプレートを `route.templates()` が返すようにする。"""
    plan = {"name": name, "steps": [{"id": "a", "run": ["{{worker}}", work]}]}
    t = route.Template(name, name, "試験のテンプレート", params, plan)
    monkeypatch.setattr(route, "templates", lambda: {name: t})
    return t


def _released_run(name: str, reader) -> invoke.StageRun:
    return replace(invoke.StageRun(invoke.PLAN, name, {"pr": 3}, (), "none", "release"), released=reader)


def _fail():
    raise AssertionError("記録済みの値を使わないプランでは呼ばない")


@pytest.mark.parametrize("held", [None, worker_kinds.Recorded("main old", None, "none")])
def test_plan_path_rewrites_the_recorded_value_left_by_an_earlier_attempt(tmp_path, monkeypatch, held):
    # 前の試行・直す前の NDF（released_version の無い values.json）が残した値を使わず、今の記録で書き直す
    _release_template(monkeypatch)
    work = tmp_path / "w"
    worker_kinds.write_values(work, "release-x", {}, {"pr": 3}, True, held)
    invoke.plan_path(_released_run("release-x", lambda: NOW), work, tmp_path)
    assert worker_kinds.released_of(invoke.read_values(work)) == NOW


def test_plan_path_removes_the_recorded_value_when_there_is_none_now(tmp_path, monkeypatch):
    _release_template(monkeypatch)
    work = tmp_path / "w"
    worker_kinds.write_values(work, "release-x", {}, {"pr": 3}, True, NOW)
    invoke.plan_path(_released_run("release-x", lambda: None), work, tmp_path)
    assert not set(worker_kinds.RELEASED_KEYS) & set(invoke.read_values(work))


def test_plan_path_does_not_write_when_the_recorded_value_is_the_same(tmp_path, monkeypatch):
    _release_template(monkeypatch)
    work = tmp_path / "w"
    worker_kinds.write_values(work, "release-x", {}, {"pr": 3}, True, NOW)
    written = _counting(monkeypatch, "write_released")
    invoke.plan_path(_released_run("release-x", lambda: NOW), work, tmp_path)
    assert written == []


def test_a_plan_without_the_release_work_never_reads_the_recorded_value(tmp_path, monkeypatch):
    # 今のテンプレートに release が無ければ、values.json に released_version が残っていても書き直さず、履歴もたどらない
    _release_template(monkeypatch, work="fix")
    work = tmp_path / "w"
    worker_kinds.write_values(work, "release-x", {}, {}, True, NOW)
    written = _counting(monkeypatch, "write_released")
    invoke.plan_path(_released_run("release-x", _fail), work, tmp_path)
    assert written == []


def test_plan_path_after_prepare_plan_uses_its_judgement_without_listing_the_works_again(tmp_path, monkeypatch):
    # 工程の初回（plan_path が prepare_plan を呼ぶ回）は、prepare_plan の判定を引き継ぎ、作業の一覧を取り直さない
    _release_template(monkeypatch, params=("worker", "repo"))
    monkeypatch.setattr(invoke, "template_values", lambda *a, **k: {"repo": str(tmp_path)})
    rendered = _counting(monkeypatch, "rendered", route)
    work = tmp_path / "w"
    invoke.plan_path(_released_run("release-x", lambda: NOW), work, tmp_path)
    assert rendered == [] and worker_kinds.released_of(invoke.read_values(work)) == NOW


def test_uses_released_reads_the_outputs_of_the_work_definitions():
    assert invoke.uses_released(["fix", "release"]) is True
    assert invoke.uses_released([]) is False and invoke.uses_released(["fix"]) is False
    with pytest.raises(KeyError):
        invoke.uses_released(["no-such-work"])
