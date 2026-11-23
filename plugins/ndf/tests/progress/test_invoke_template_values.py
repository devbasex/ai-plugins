"""工程から組むプランの差し込み（`invoke.template_values`）の、起動の補いと受け入れ条件の検査の現状固定。

単発の経路（`ndf route render` の `_write_plan`）と同じ補い（`route.launch_values`）と検査（`route.check_acceptance`）を、
出所の表・打った場所（`route.derived_locally`）・課題・マージの方式で埋めた後、つなぎ目のレビュー・リファクタリングの補いと欠けの検査より前に通す。
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import strategy_fixture

from ndf.core import route
from ndf.progress import invoke

# 設定（`.ndf/project.json`）のうち、実装のプランの差し込みを埋める値（review の起動は持たない。既定の起動を補わせる）
PROJECT = {
    "test": {"suites": [{"name": "pytest", "command": "pytest -q"}]},
    "ci": {"required_checks": ["pytest"]},
}


def _sprint_repo(tmp_path: Path) -> Path:
    """ブランチ sprint/s1 に居る作業場所（リモート無し）。マージ先はブランチ戦略 web-develop の develop。"""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "sprint/s1", str(repo)], check=True)
    strategy_fixture.write(repo, "web-develop", PROJECT)
    return repo


def _impl_run(repo: Path, issues: list[int], **extra) -> invoke.StageRun:
    inputs = {"issues": issues, "worktree": str(repo), "mode": "light", **extra}
    return invoke.StageRun(invoke.PLAN, "impl", inputs, (), "none", "impl")


@pytest.fixture
def filled(monkeypatch):
    """worker の既定・課題の題と本文・マージの方法を差し替える（課題を読んだ番号を返す）。"""
    held = route.params()
    monkeypatch.setattr(route, "params", lambda: {**held, "worker": replace(held["worker"], default=["ndf-worker"])})
    read: list[int] = []
    monkeypatch.setattr(invoke, "issue_text", lambda location, n: read.append(n) or (f"#{n} の題", f"#{n} の本文"))
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None: read.append("merge_method") or "squash")
    return read


def _values(tmp_path: Path, run: invoke.StageRun, repo: Path) -> dict:
    work = tmp_path / "w"
    work.mkdir(exist_ok=True)
    return invoke.template_values(route.choose("implement"), run, work, repo, project=PROJECT, local_tests=True)


def test_one_issue_fills_the_acceptance_of_the_issue_and_the_default_review(tmp_path, filled):
    # 課題が 1 つなら、課題から写した issue で acceptance を --issue <issue> に補い、review は <リモート>/<base> から組む
    repo = _sprint_repo(tmp_path)
    got = _values(tmp_path, _impl_run(repo, [3092]), repo)
    assert filled == [3092, "merge_method"]
    assert got["issue"] == "3092" and got["acceptance"] == ["--issue", "3092"]
    assert got["review"] == route.review_launch("impl", "sprint/s1", "origin/develop")


def test_the_launch_values_are_the_same_as_the_single_route_on_the_same_values(tmp_path, filled):
    # 工程から組む経路の review・acceptance は、同じ値に単発の経路の補い（route.launch_values）を当てたものと同じ
    repo = _sprint_repo(tmp_path)
    got = _values(tmp_path, _impl_run(repo, [3092]), repo)
    t = route.choose("implement")
    before = {k: v for k, v in got.items() if k not in ("review", "acceptance")}
    assert route.launch_values(t, before) == {"review": got["review"], "acceptance": got["acceptance"]}


def test_an_acceptance_given_as_an_input_is_kept_as_is(tmp_path, filled):
    # 入力で渡した acceptance は補いで上書きしない（課題が 1 つでも --issue <issue> に戻さない）
    given = ["--issue", "3092", "--criteria", "3092:1"]
    repo = _sprint_repo(tmp_path)
    got = _values(tmp_path, _impl_run(repo, [3092], acceptance=given), repo)
    assert got["acceptance"] == given


@pytest.mark.parametrize(
    "acceptance",
    [["--criteria", "1:2"], ["--issue"], ["--issue", "3", "--criteria"], ["--issue", "--criteria", "1:1"]],
)
def test_an_acceptance_without_an_issue_or_a_value_is_refused_before_the_missing_values(tmp_path, filled, acceptance):
    # 渡した acceptance の検査は欠けの検査より先に働く。課題が 2 つで title・body_file が決まらなくても、欠けではなく
    # acceptance の誤りで断る（TemplateValuesMissing ではない route.RouteError）
    repo = _sprint_repo(tmp_path)
    with pytest.raises(route.RouteError, match="acceptance") as got:
        _values(tmp_path, _impl_run(repo, [3, 4], acceptance=acceptance), repo)
    assert not isinstance(got.value, invoke.TemplateValuesMissing)
    assert filled == []


def test_a_stage_with_an_acceptance_without_an_issue_stops_as_invalid_without_running(tmp_path, filled):
    # 工程から呼ぶと、acceptance の誤りは打たずに invalid で止まり、欠けの項目（items）を挙げない
    repo = _sprint_repo(tmp_path)
    run = _impl_run(repo, [3, 4], acceptance=["--criteria", "1:2"])
    got = invoke.invoke(run, repo, tmp_path / "w", runner=lambda *a: pytest.fail("打たない"))
    assert got.status == "stopped" and got.cause["kind"] == "invalid"
    assert "--issue" in got.cause["detail"] and not got.report.get("items")


def test_a_valid_acceptance_of_several_issues_reaches_the_missing_values_check(tmp_path, filled):
    # 形の合う acceptance なら検査を通り、その後の欠けの検査が課題から決まらない title・body_file を挙げる
    repo = _sprint_repo(tmp_path)
    with pytest.raises(invoke.TemplateValuesMissing) as got:
        _values(tmp_path, _impl_run(repo, [3, 4], acceptance=["--issue", "3", "--issue", "4"]), repo)
    assert set(got.value.missing) == {"title", "body_file"}
    assert got.value.missing["title"] == invoke.FROM_ISSUE


def test_a_missing_project_value_stops_before_reading_the_issue(tmp_path, filled):
    # 課題に依存しない差し込み（設定の required）が欠ければ、課題の題・本文と書き手の権限を GitHub から読む前に止まる。
    # 試行の始まりに写しを消すので、読んでから止まると打ち直すたびに読み直す（PR 4289 の指摘）
    repo = _sprint_repo(tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    project = {"test": PROJECT["test"]}
    with pytest.raises(invoke.TemplateValuesMissing) as got:
        invoke.template_values(route.choose("implement"), _impl_run(repo, [3092]), work, repo, project=project, local_tests=True)
    assert got.value.missing == {"required": invoke.FROM_PROJECT}
    assert filled == []


def test_a_failed_issue_read_does_not_ask_github_for_the_merge_method(tmp_path, filled, monkeypatch):
    # マージの方式は課題を読んだ後に GitHub に問う。課題を読めずに止まる試行では問い合わせない（PR 4289 の指摘）
    def fail(location, n):
        raise invoke.github.GitHubError("読めない")

    monkeypatch.setattr(invoke, "issue_text", fail)
    repo = _sprint_repo(tmp_path)
    with pytest.raises(invoke.github.GitHubError):
        _values(tmp_path, _impl_run(repo, [3092]), repo)
    assert filled == []
