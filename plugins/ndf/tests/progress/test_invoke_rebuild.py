"""工程「設計」「ドキュメント再構成」の差し込み `rebuild` の既定（`ndf.progress.rebuild_values`。#4337 の条件 1・2・5・14・15）。"""

from __future__ import annotations

from pathlib import Path

import pytest
import strategy_fixture
from sprint_fixture import sprint_repo as _sprint_repo
from sprint_fixture import with_worker as _with_worker

from ndf.adapters import git
from ndf.core import branch_strategy, route
from ndf.progress import invoke, rebuild_values

PROJECT = {"branch_strategy": strategy_fixture.section("web-develop"), "ci": {"required_checks": ["test"]}}
TRUNK = {**strategy_fixture.section("web-trunk"), "branches": {"production": "trunk", "base": "trunk"}}
SETTING = ["ndf-rebuild", "--x"]


@pytest.mark.parametrize(
    ("name", "mode", "kinds"),
    [
        ("design", "standard", ("design",)),
        ("design", "documentation", ("design",)),
        ("document-rebuild", "standard", ("design", "plan")),
        ("document-rebuild", None, ("design", "plan")),
        ("document-rebuild", "documentation", ("design", "plan", "manuscript")),
        ("implement", "standard", ()),
    ],
)
def test_kinds_for(name: str, mode, kinds: tuple[str, ...]) -> None:
    assert rebuild_values.kinds_for(name, mode) == kinds


def test_launch_lists_issues_and_kinds_and_keeps_the_state_dir_placeholder() -> None:
    got = rebuild_values.launch("main", "origin", ["1", "2"], ["design", "plan"])
    assert got == [
        "ndf", "document-rebuild", "run", "--base", "main", "--remote", "origin",
        "--issue", "1", "--issue", "2", "--kind", "design", "--kind", "plan", "--state-dir", "{state_dir}",
    ]  # fmt: skip


class Remote:
    """`Git.remote` の偽物（呼んだ回数と、呼んだ `Git` の作業場所を残す）。"""

    def __init__(self, name: str = "origin", error: Exception | None = None) -> None:
        self.name, self.error, self.calls = name, error, 0
        self.cwds: list[Path | None] = []

    def __call__(self, g: git.Git) -> str:
        self.calls += 1
        self.cwds.append(g.cwd)
        if self.error is not None:
            raise self.error
        return self.name


@pytest.fixture
def remote(monkeypatch) -> Remote:
    fake = Remote()
    monkeypatch.setattr(git.Git, "remote", lambda g: fake(g))
    return fake


def _fill(name: str, values: dict, project: dict = PROJECT, mode: str = "standard", issues=("4337",)) -> rebuild_values.Filled:
    t = invoke.template(name)
    assert t is not None
    return rebuild_values.fill(t, values, list(issues), mode, Path("/w"), project=project)


def test_design_without_the_setting_gets_the_default_with_the_same_base_and_remote(remote: Remote) -> None:
    got = _fill("design", {})
    assert got.undecided is None
    argv = got.values["rebuild"]
    assert argv[argv.index("--base") + 1] == got.values["base"] == "develop"
    assert argv[argv.index("--remote") + 1] == got.values["remote"] == "origin"
    assert remote.calls == 1


def test_names_come_from_the_settings_not_from_fixed_words(remote: Remote) -> None:
    """条件 5: ベースブランチ `trunk`・リモート `up` でも同じ（`origin`・`main` を書かない）。"""
    remote.name = "up"
    got = _fill("design", {}, {"branch_strategy": TRUNK})
    argv = got.values["rebuild"]
    assert (argv[argv.index("--base") + 1], argv[argv.index("--remote") + 1]) == ("trunk", "up")
    assert (got.values["base"], got.values["remote"]) == ("trunk", "up")


def test_document_rebuild_does_not_add_a_base_it_does_not_use(remote: Remote) -> None:
    got = _fill("document-rebuild", {}, mode="documentation")
    assert "base" not in got.values and got.values["remote"] == "origin"
    assert got.values["rebuild"].count("--kind") == 3


def test_the_setting_wins(remote: Remote) -> None:
    """条件 15: 設定のコマンドがあれば既定を組まない。"""
    assert _fill("design", {"rebuild": SETTING}) == rebuild_values.Filled()
    assert remote.calls == 0


def test_given_base_and_remote_are_not_read_again(remote: Remote, monkeypatch) -> None:
    monkeypatch.setattr(branch_strategy, "of_project", lambda project: pytest.fail("読まない"))
    got = _fill("design", {"base": "b", "remote": "r"}, project={})
    assert got.values == {"rebuild": rebuild_values.launch("b", "r", ["4337"], ["design"])}
    assert remote.calls == 0


def test_without_branch_strategy_it_is_undecided_before_asking_git(remote: Remote) -> None:
    got = _fill("design", {}, project={})
    assert got.values == {} and got.undecided is not None and got.undecided.reason
    assert remote.calls == 0


def test_a_broken_remote_setting_is_undecided(remote: Remote) -> None:
    remote.error = git.RemoteError(".ndf/worktree.json の remote を直す: ''")
    got = _fill("design", {})
    assert got.undecided == rebuild_values.Undecided(".ndf/worktree.json の remote を直す: ''")


def test_without_issues_it_is_undecided(remote: Remote) -> None:
    assert _fill("design", {}, issues=()).undecided == rebuild_values.Undecided(rebuild_values.NO_ISSUES)


def test_undecided_fields_name_the_setting_and_its_shape() -> None:
    assert rebuild_values.Undecided("理由").fields() == {
        "reason": "理由",
        "setting": "workflow.launch.rebuild",
        "shape": rebuild_values.SHAPE,
    }


def test_missing_items_add_details_only_to_their_rows() -> None:
    got = invoke.TemplateValuesMissing(
        "design",
        {"rebuild": invoke.FROM_PROJECT, "worker": invoke.FROM_DEFAULT},
        {"rebuild": {"reason": "r", "setting": "s", "shape": "h"}},
    )
    assert got.items == [
        {"key": "rebuild", "source": invoke.FROM_PROJECT, "reason": "r", "setting": "s", "shape": "h"},
        {"key": "worker", "source": invoke.FROM_DEFAULT},
    ]


def _stage(name: str, repo: Path) -> invoke.StageRun:
    inputs = {"issues": [4337], "worktree": str(repo), "mode": "standard"}
    return invoke.StageRun(invoke.PLAN, name, inputs, (), "none", name)


@pytest.mark.parametrize("name", ["design", "document-rebuild"])
def test_stage_without_branch_strategy_stops_with_the_setting_and_shape(tmp_path, monkeypatch, name) -> None:
    """条件 14: `branch_strategy` の無いリポジトリでは、プランを打たずに止まり、`rebuild` の行が理由・設定のキー・値の形を持つ。"""
    _with_worker(monkeypatch)
    repo = _sprint_repo(tmp_path)
    (repo / ".ndf" / "project.json").write_text('{"ci": {"required_checks": ["test"]}}', encoding="utf-8")
    monkeypatch.setattr(invoke, "issue_text", lambda *a: pytest.fail("課題を読む前に止まる"))
    got = invoke.invoke(_stage(name, repo), repo, tmp_path / "w", runner=lambda *a: pytest.fail("打たない"))
    assert got.status == "stopped" and got.cause["kind"] == "precondition"
    row = next(i for i in got.report["items"] if i["key"] == "rebuild")
    assert set(row) == {"key", "source", "reason", "setting", "shape"}
    assert row["setting"] == "workflow.launch.rebuild" and row["shape"] == rebuild_values.SHAPE and row["reason"]


def test_design_plan_uses_one_base_and_one_remote(tmp_path, monkeypatch, remote: Remote) -> None:
    """決定 7: 設計のプランの `base` と `--base`、`remote` と `--remote` は同じ値で、ブランチ戦略と `Git.remote` を 1 回ずつ読む。

    `Git.remote` は偽物で、`git worktree list` を打たない。
    """
    _with_worker(monkeypatch)
    repo = _sprint_repo(tmp_path, {"ci": {"required_checks": ["test"]}})
    monkeypatch.setattr(invoke, "issue_text", lambda location, n: (f"#{n} の題", f"#{n} の本文"))
    reads = {"strategy": 0}
    held_of = branch_strategy.of_project

    def of_project(project):
        reads["strategy"] += 1
        return held_of(project)

    monkeypatch.setattr(branch_strategy, "of_project", of_project)
    monkeypatch.setattr(route, "default_base", lambda repo: pytest.fail("base は rebuild と同じ値を使う"))
    work = tmp_path / "w"
    work.mkdir()
    project = invoke._project(repo)
    t = invoke.template("design")
    assert t is not None
    values = invoke.template_values(t, _stage("design", repo), work, repo, project=project, local_tests=True)
    argv = values["rebuild"]
    assert argv[argv.index("--base") + 1] == values["base"] == "develop"
    assert argv[argv.index("--remote") + 1] == values["remote"] == remote.name
    assert reads == {"strategy": 1}
    assert remote.calls == 1 and remote.cwds == [repo]
