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
from ndf.progress import invoke, issue_values, merge_pending

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
    monkeypatch.setattr(issue_values, "issue_text", lambda location, n: read.append(n) or (f"#{n} の題", f"#{n} の本文"))
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None, **_: read.append("merge_method") or "squash")
    return read


def _values(tmp_path: Path, run: invoke.StageRun, repo: Path) -> dict:
    work = tmp_path / "w"
    work.mkdir(exist_ok=True)
    return invoke.template_values(route.choose("implement"), run, work, repo, project=PROJECT, local_tests=True)


def test_one_issue_fills_the_acceptance_of_the_issue_and_the_default_review(tmp_path, filled):
    # 課題が 1 つなら、課題から写した issue で acceptance を --issue <issue> に補い、review は <リモート>/<base> から組む
    repo = _sprint_repo(tmp_path)
    got = _values(tmp_path, _impl_run(repo, [3092]), repo)
    assert filled == [3092]
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


def test_several_issues_fill_the_acceptance_with_an_issue_flag_for_each_issue(tmp_path, filled):
    # #3908 の決定 7・受け入れ条件 2: 課題をまとめた単位は、代表の課題（#3）から title をコピーし、acceptance で全部の課題を検証する
    repo = _sprint_repo(tmp_path)
    got = _values(tmp_path, _impl_run(repo, [4, 3]), repo)
    assert got["issue"] == "3" and got["title"] == "#3 の題"
    assert got["acceptance"] == ["--issue", "3", "--issue", "4"]


def test_a_valid_acceptance_of_several_issues_fills_the_title_from_the_representative_issue(tmp_path, filled):
    # #3908 の決定 7: 形の合う acceptance なら検査を通り、title・body_file は代表の課題（番号の最も小さい #3）から決まる
    repo = _sprint_repo(tmp_path)
    got = _values(tmp_path, _impl_run(repo, [4, 3], acceptance=["--issue", "3", "--issue", "4"]), repo)
    assert filled == [3] and got["title"] == "#3 の題"
    assert got["acceptance"] == ["--issue", "3", "--issue", "4"]


def test_a_missing_project_value_stops_before_reading_the_issue(tmp_path, filled):
    # 課題に依存しない差し込み（設定の required）が欠ければ、課題の題・本文と作成者の権限を GitHub から読む前に止まる。
    # 試行の始まりにコピーを消すので、読んでから止まると打ち直すたびに読み直す（PR 4289 の指摘）
    repo = _sprint_repo(tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    project = {"test": PROJECT["test"]}
    with pytest.raises(invoke.TemplateValuesMissing) as got:
        invoke.template_values(route.choose("implement"), _impl_run(repo, [3092]), work, repo, project=project, local_tests=True)
    assert got.value.missing == {"required": invoke.FROM_PROJECT}
    assert filled == []


@pytest.fixture
def local(tmp_path, monkeypatch):
    """手元（git と設定ファイル）で決まる値を差し替えた作業場所（git を起動しない）。ブランチは sprint/s1、マージ先は develop。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(invoke, "_branch_of", lambda run: "sprint/s1")
    monkeypatch.setattr(route, "derived_locally", lambda t, given, cwd: {"repo": str(repo), "remote": "origin", "base": "develop", **given})
    return repo


def _close(tmp_path: Path, repo: Path) -> dict:
    """課題とマージの方式の両方を使う閉じのテンプレート（`close`）の差し込み。"""
    work = tmp_path / "w"
    work.mkdir(exist_ok=True)
    run = invoke.StageRun(invoke.PLAN, "close", {"issues": [3092], "worktree": str(repo)}, (), "none", "close")
    return invoke.template_values(route.choose("close"), run, work, repo, project=PROJECT, local_tests=True)


def test_close_reads_the_issue_before_asking_github_for_the_merge_method(tmp_path, filled, local):
    got = _close(tmp_path, local)
    assert filled == [3092, "merge_method"]
    assert got["title"] == "#3092 の題" and got["merge_method"] == "squash"


def test_a_failed_issue_read_does_not_ask_github_for_the_merge_method(tmp_path, filled, local, monkeypatch):
    # マージの方式は課題を読んだ後に GitHub に問う。課題を読めずに止まる試行では問い合わせない（PR 4289 の指摘）
    def fail(location, n):
        raise invoke.github.GitHubError("読めない")

    monkeypatch.setattr(issue_values, "issue_text", fail)
    with pytest.raises(invoke.github.GitHubError):
        _close(tmp_path, local)
    assert filled == []


def test_a_retry_after_an_unknown_merge_method_asks_the_merge_method_before_the_issue(tmp_path, filled, local, monkeypatch):
    # マージの方式を決められずに止まった試行の打ち直しは、方式を課題より先に問うて、決まらなければ課題を読まずに止める。
    # 決まった試行で課題と作成者の権限を読み直す（コピーは clear_values が消している。PR 4289 の指摘）
    answers = iter([None, None, "squash"])
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None, **_: filled.append("merge_method") or next(answers))
    for _ in range(2):
        with pytest.raises(invoke.TemplateValuesMissing) as got:
            _close(tmp_path, local)
        assert set(got.value.missing) == {"merge_method"}
        issue_values.clear_values([tmp_path / "w"])
    got = _close(tmp_path, local)
    assert filled == [3092, "merge_method", "merge_method", "merge_method", 3092]
    assert got["merge_method"] == "squash" and not (tmp_path / "w" / merge_pending.PENDING_FILE).exists()


def _pending(tmp_path: Path) -> Path:
    """マージの方式を決められずに止まった試行の目印（前の試行が残したもの）。"""
    work = tmp_path / "w"
    work.mkdir(exist_ok=True)
    merge_pending.mark(work / merge_pending.PENDING_FILE)
    return work / merge_pending.PENDING_FILE


def test_a_retry_whose_issue_read_fails_after_the_merge_method_does_not_ask_it_again(tmp_path, filled, local, monkeypatch):
    # 目印のある打ち直しで方式が決まった後に課題を読めずに止まっても、方式は目印に記録しておき、次の打ち直しは
    # 問い合わせ直さない。許す方式の変化には、GitHub がマージを断ったときに値を捨てて追う。課題を読めた試行が
    # 目印を消す（PR 4289 の指摘）
    pending = _pending(tmp_path)

    def fail(location, n):
        raise invoke.github.GitHubError("読めない", status=502)

    with monkeypatch.context() as m:
        m.setattr(issue_values, "issue_text", fail)
        with pytest.raises(invoke.github.GitHubError):
            _close(tmp_path, local)
    issue_values.clear_values([tmp_path / "w"])
    got = _close(tmp_path, local)
    assert filled == ["merge_method", 3092]
    assert got["merge_method"] == "squash" and not pending.exists()


def test_a_rate_limit_on_the_merge_method_is_a_transient_stop_with_the_wait(tmp_path, filled, local, monkeypatch):
    # 目印のある打ち直しで方式を問う GitHub が上限に当たれば、方式が無いとせずに待ち時間付きの transient で止まり、
    # 課題を読まずに目印を残す（PR 4289 の指摘）
    pending = _pending(tmp_path)

    def limited(repo, base, remote=None, *, raise_passing=False):
        assert raise_passing
        raise invoke.github.GitHubError("上限", status=403, retry_after=60.0, quota="rest")

    monkeypatch.setattr(route, "default_merge_method", limited)
    with pytest.raises(invoke.github.GitHubError) as got:
        _close(tmp_path, local)
    stopped = invoke.plan_failure("close", got.value)
    assert stopped["cause"]["kind"] == "transient" and stopped["cause"]["retry_after"] == 60.0
    assert filled == [] and pending.exists()


# --- 工程から組むプランの production_gate と承認の対象（#4409 の決定 4・5） ---

from ndf.core import route as _route  # noqa: E402
from ndf.progress import invoke as _invoke  # noqa: E402
from ndf.progress import model as _model  # noqa: E402


def _stage_run(name: str, gated: bool = True, approved=None) -> _invoke.StageRun:
    return _invoke.StageRun("plan", name, {}, (), "none", "s", {}, approved=approved, gated=gated)


@pytest.mark.parametrize(
    ("name", "gated", "want"),
    [("merge", False, "defer"), ("plan-to-spec", False, "defer"), ("design-merge", True, "stale"), ("design-merge", False, "defer")],
)
def test_stage_plans_choose_how_an_unapproved_production_merge_ends(name, gated, want):
    t = _route.templates()[name]
    assert _invoke._fill_production_gate(t, _stage_run(name, gated))["production_gate"] == want


def test_templates_outside_the_table_keep_the_stop_default():
    assert "production_gate" not in _invoke._fill_production_gate(_route.templates()["fix"], _stage_run("fix"))


def test_release_takes_the_approved_head_and_base_from_the_answer():
    """利用者の回答と MVV 判定が通した回答（`service:mvv`）のどちらの seen からも同じ値を作る。旧版の回答は空。"""
    t = _route.templates()["release"]
    user = {"head": "h", "material_sha256": "m", "repo": "o/r", "pr": 8, "base": "main", "production": True}
    held = {"gate": "release", "skip": True, "head": "h", "target": {"repo": "o/r", "pr": 8, "base": "main", "head": "h", "production": True}}
    mvv, _ = _model.mvv_answer("release-gate", "listen", "approved", held, "q")
    for seen in (user, mvv["seen"]):
        got = _invoke._fill_production_gate(t, _stage_run("release", approved=seen))
        assert (got["approved_head"], got["approved_base"]) == ("h", "main")
    old = _invoke._fill_production_gate(t, _stage_run("release", approved={"head": "h", "material_sha256": None}))
    assert (old["approved_head"], old["approved_base"]) == ("h", "")
