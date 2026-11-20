"""サブコマンド `ndf route`: テンプレートの一覧・プランを書く・書いて流す・同じ原因の続きを数える・省いた保存先を決める。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import strategy_fixture

from ndf.core import route

from ._flow import git


def _fix_args(out: Path, repo: Path) -> list[str]:
    return [
        "immediate_fix", "--out", str(out), "--set", "branch=fix/x", "--set", "title=直す", "--set", "body_file=b.md",
        "--set", "test_command=pytest -q", "--add", "required=test", "--set", "base=develop",
        "--set", "merge_method=squash",
        "--set", f"repo={repo}", "--set", f"worktree={repo}",
    ]  # fmt: skip


def test_templates_lists_every_template(capsys, last_result):
    assert route.main(["templates"]) == 0
    found = last_result(capsys.readouterr().out)
    assert {i["purpose"] for i in found["items"]} >= {"immediate_fix", "implement", "check", "close", "design", "design_merge"}


def test_render_writes_plan_and_points_to_plan_next(tmp_path, capsys, last_result):
    out = tmp_path / "plans" / "fix.json"
    assert route.main(["render", *_fix_args(out, tmp_path)]) == 0
    found = last_result(capsys.readouterr().out)
    assert found["next"]["argv"] == ["ndf", "plan", "next", str(out)]
    assert [s["id"] for s in json.loads(out.read_text())["steps"]][0] == "test"


def test_run_is_one_entrypoint_for_immediate_fix(tmp_path, monkeypatch):
    """C3-1（#1142 の要求の不足 a）: conductor は ndf route run immediate_fix 1 つで、テンプレートを埋めたプランを流す。"""
    called: list[list[str]] = []
    monkeypatch.setattr(route.plan_cli, "main", lambda argv: called.append(argv) or 0)
    out = tmp_path / "fix.json"
    assert route.main(["run", *_fix_args(out, tmp_path)]) == 0
    assert called == [["next", str(out)]]
    ids = [s["id"] for s in json.loads(out.read_text())["steps"]]
    assert ids == ["test", "push", "pr", "conflict-check", "ci", "merge"]


def _impl_args(out: Path, repo: Path, *extra: str) -> list[str]:
    """supervisor が単位ごとに組む実装のプランの引数（`ndf route render implement`）。"""
    return [
        "implement", "--out", str(out), "--set", "branch=u1", "--set", "base=sprint/x", "--set", "issue=1",
        "--set", "title=単位 1", "--set", "body_file=b.md", "--set", "test_command=pytest -q", "--add", "worker=ndf-worker",
        "--add", "required=test", "--set", f"repo={repo}", "--set", f"worktree={repo}", *extra,
    ]  # fmt: skip


def _step(out: Path, step_id: str) -> dict:
    return next(s for s in json.loads(out.read_text())["steps"] if s["id"] == step_id)


def test_render_implement_checks_the_acceptance_of_the_issue(tmp_path):
    """現状固定（#3879 Task 4）: 今の supervisor の呼び方（acceptance を渡さない）では、課題の受け入れ条件をすべて照らす。"""
    out = tmp_path / "impl.json"
    assert route.main(["render", *_impl_args(out, tmp_path)]) == 0
    assert _step(out, "acceptance")["run"] == ["ndf", "loop", "acceptance", "check", "--issue", "1"]


# 設計書の決定 1 のコマンドの acceptance（課題 1 の条件 1・2 だけを照らす）
_UNIT_ACCEPTANCE = ["--issue", "1", "--criteria", "1:1", "--criteria", "1:2"]


def test_render_implement_with_the_command_of_decision_1(tmp_path):
    """#3879 決定 1: supervisor の組む実装のプランが、単位の PR を <リモート>/<base> からレビューし、単位の条件だけを照らす。"""
    out = tmp_path / "impl.json"
    extra = [x for v in _UNIT_ACCEPTANCE for x in ("--add", f"acceptance={v}")]
    assert route.main(["render", *_impl_args(out, tmp_path, *extra)]) == 0
    assert _step(out, "review")["run"][5:] == route.review_launch("impl", "u1", "origin/sprint/x")
    assert _step(out, "acceptance")["run"] == ["ndf", "loop", "acceptance", "check", *_UNIT_ACCEPTANCE]


def test_render_implement_takes_an_acceptance_of_several_issues_without_an_issue(tmp_path):
    """PR 3888 の指摘: acceptance を渡せば issue は要らない（単独の課題を持たない、課題を束ねた単位も組める）。"""
    out = tmp_path / "impl.json"
    acceptance = ["--issue", "3", "--issue", "4"]
    args = _impl_args(out, tmp_path)
    at = args.index("issue=1")
    del args[at - 1 : at + 1]
    extra = [x for v in acceptance for x in ("--add", f"acceptance={v}")]
    assert route.main(["render", *args, *extra]) == 0
    assert _step(out, "acceptance")["run"] == ["ndf", "loop", "acceptance", "check", *acceptance]


def test_needed_asks_for_the_issue_only_to_build_the_default_acceptance():
    t = route.choose("implement")
    assert "issue" in route.needed(t)
    assert "issue" not in route.needed(t, given={"acceptance": ["--issue", "3", "--issue", "4"]})


def test_render_implement_refuses_an_acceptance_without_an_issue(tmp_path, capsys, last_result):
    out = tmp_path / "impl.json"
    extra = ["--add", "acceptance=--criteria", "--add", "acceptance=1:2"]
    assert route.main(["render", *_impl_args(out, tmp_path, *extra)]) == 2
    found = last_result(capsys.readouterr().out)
    assert found["cause"]["kind"] == "invalid" and "--issue" in found["cause"]["detail"]
    assert not out.exists()


# 補い（launch_values）を確かめる見本のテンプレートの差し込み
_LAUNCH_PARAMS = ("branch", "base", "remote", "repo", "issue", "review", "acceptance")


def _launch_template(name: str) -> route.Template:
    return route.Template(name, f"{name}_purpose", "見本", _LAUNCH_PARAMS, {"name": "x", "steps": []})


def test_launch_values_fill_the_review_from_the_remote_base_for_impl():
    """#3879 構成要素 4: 実装のテンプレートのレビューの起点は <リモート>/<base>。リモートが無ければ既定のリモート。"""
    given = {"branch": "u1", "base": "sprint/x", "remote": "upstream", "issue": "1"}
    got = route.launch_values(_launch_template("impl"), given)
    assert got["review"] == route.review_launch("impl", "u1", "upstream/sprint/x")
    assert got["acceptance"] == ["--issue", "1"]
    without = {k: v for k, v in given.items() if k != "remote"}
    assert route.launch_values(_launch_template("impl"), without)["review"] == route.review_launch("impl", "u1", "origin/sprint/x")


def test_launch_values_keep_the_base_for_other_templates_and_skip_given_values():
    """実装でないテンプレートの起点は base のまま。値を渡した差し込みは補わない。"""
    given = {"branch": "d1", "base": "develop", "remote": "origin", "issue": "1"}
    assert route.launch_values(_launch_template("design"), given)["review"] == route.review_launch("design", "d1", "develop")
    assert route.launch_values(_launch_template("design"), {**given, "review": ["r"], "acceptance": ["--issue", "2"]}) == {}
    assert route.launch_values(_launch_template("design"), {"base": "develop"}) == {}


@pytest.mark.parametrize(
    "acceptance",
    [["--criteria", "1:2"], ["--issue"], ["--issue", "1", "--criteria"], ["--issue", "--criteria", "1:1"]],
)
def test_check_acceptance_refuses_a_list_without_an_issue_or_a_value(acceptance):
    with pytest.raises(route.RouteError, match="acceptance"):
        route.check_acceptance({"acceptance": acceptance})


def test_check_acceptance_passes_a_list_with_an_issue_and_the_values():
    route.check_acceptance({"acceptance": ["--issue", "1", "--criteria", "1:1", "--criteria", "1:2"]})
    route.check_acceptance({"acceptance": ["--issue", "3", "--issue", "4"]})
    route.check_acceptance({})


def test_render_fills_the_default_review_on_the_single_route(tmp_path):
    """#3879 決定 1: 単発の経路（ndf route render）でも、設定が無いときの {{review}} を工程から組む経路と同じ関数で補う。"""
    out = tmp_path / "design.json"
    args = [
        "design", "--out", str(out), "--set", "branch=d1", "--set", "base=develop", "--set", "title=t",
        "--set", "body_file=b.md", "--add", "worker=ndf-worker", "--add", "rebuild=ndf-rebuild", "--add", "required=test",
        "--set", f"repo={tmp_path}", "--set", f"worktree={tmp_path}",
    ]  # fmt: skip
    assert route.main(["render", *args]) == 0
    review = _step(out, "review")["run"]
    assert review[: len(route.review_launch("design", "d1", "develop"))] == route.review_launch("design", "d1", "develop")


def test_render_drops_local_tests_when_repo_declares_ci_only(tmp_path):
    """CI テストの設定（`test.ci.only`）のあるリポジトリでは、即時修正のプランも手元のテストを持たず、test_command を求めない。"""
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "project.json").write_text(json.dumps({"test": {"ci": {"only": True}}}), encoding="utf-8")
    out = tmp_path / "fix.json"
    args = _fix_args(out, tmp_path)
    at = args.index("test_command=pytest -q")
    del args[at - 1 : at + 1]
    assert route.main(["render", *args]) == 0
    assert "test" not in [s["id"] for s in json.loads(out.read_text())["steps"]]


def test_render_refuses_missing_value_as_invalid(tmp_path, capsys, last_result):
    code = route.main(["render", "immediate_fix", "--out", str(tmp_path / "p.json"), "--set", f"repo={tmp_path}"])
    found = last_result(capsys.readouterr().out)
    assert found["cause"]["kind"] == "invalid"
    assert code != 0


def test_set_and_add_same_name_is_refused(tmp_path, capsys, last_result):
    args = ["render", "immediate_fix", "--out", str(tmp_path / "p.json"), "--set", "required=a", "--add", "required=b"]
    route.main(args)
    assert "両方で渡した" in last_result(capsys.readouterr().out)["cause"]["detail"]


def _stopped_ci(path: Path, nodes: list[str]) -> None:
    found = {"tool": "ci", "status": "stopped", "summary": "落ちた", "items": [], "metrics": {"failed_tests": nodes},
             "cause": {"kind": "test_failed", "detail": "CI"}}  # fmt: skip
    path.write_text(json.dumps(found) + "\n", encoding="utf-8")


def test_repeat_stops_when_same_cause_continues(tmp_path, capsys, last_result):
    """#2844: 同じ落ちたテストの集まりで CI が続けて落ちたら、上限（supervise.step-rerun の same_cause）を超えた回で fix_limit。

    別の原因が挟まれば数え直す。summary の文はキーに入れない。
    """
    res, log = tmp_path / "ci.json", tmp_path / "causes.jsonl"
    args = ["repeat", "--result", str(res), "--log", str(log)]
    _stopped_ci(res, ["tests/a.py::t1"])
    assert route.main(args) == 0
    assert route.main(args) == 0
    assert route.main(args) != 0
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "fix_limit"
    _stopped_ci(res, ["tests/b.py::t2"])
    assert route.main(args) == 0
    assert last_result(capsys.readouterr().out)["metrics"]["streak"] == 1


def test_repeat_refuses_result_that_did_not_stop(tmp_path, capsys, last_result):
    res = tmp_path / "ci.json"
    res.write_text(json.dumps({"tool": "ci", "status": "ok", "summary": "", "items": [], "metrics": {}}) + "\n", encoding="utf-8")
    route.main(["repeat", "--result", str(res), "--log", str(tmp_path / "l.jsonl")])
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "invalid"


def test_cause_key_ignores_summary_and_test_order():
    a = {"status": "stopped", "summary": "x", "cause": {"kind": "test_failed"}, "metrics": {"failed_tests": ["b", "a"]}}
    b = {**a, "summary": "y", "metrics": {"failed_tests": ["a", "b"]}}
    assert route.cause_key(a) == route.cause_key(b)
    assert route.repeated([route.cause_key(a)] * 2, route.cause_key(a), 2)
    assert not route.repeated([route.cause_key(a)], route.cause_key(a), 2)


def test_omitted_worktree_is_under_submodule_worktree_not_git_modules(tmp_path):
    """#2054: サブモジュールのリポジトリで省いた作業場所は、サブモジュールのworktreeの根の下（.git/modules の下でない）。"""
    sub = tmp_path / "sub"
    sub.mkdir()
    git(sub, "init", "-q", "-b", "main")
    (sub / "a.txt").write_text("a\n", encoding="utf-8")
    git(sub, "add", ".")
    git(sub, "commit", "-q", "-m", "a")
    sup = tmp_path / "super"
    sup.mkdir()
    git(sup, "init", "-q", "-b", "main")
    git(sup, "submodule", "add", "-q", str(sub), "mod")
    inner = sup / "mod"
    t = route.choose("immediate_fix")
    got = route.derived(t, {"branch": "fix/x", "base": "main"}, inner)
    assert Path(got["repo"]).resolve() == inner.resolve()
    assert Path(got["worktree"]).resolve() == (inner / ".worktrees" / "fix" / "x").resolve()
    assert ".git/modules" not in str(got["worktree"])


# 現状固定（I-005）: repeat が結果を読めないとき・止まった結果でないときの拒否。どちらも invalid で、記録へ書かない。
def test_repeat_refuses_unreadable_or_not_stopped_result_without_logging(tmp_path, capsys, last_result):
    log = tmp_path / "l.jsonl"
    missing = tmp_path / "none.json"
    assert route.main(["repeat", "--result", str(missing), "--log", str(log)]) == 2
    found = last_result(capsys.readouterr().out)
    assert (found["tool"], found["status"], found["cause"]["kind"]) == ("route", "stopped", "invalid")
    assert found["cause"]["detail"] == f"結果 {missing} を読めない: No such file or directory"
    assert found["summary"] == found["cause"]["detail"]
    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    assert route.main(["repeat", "--result", str(empty), "--log", str(log)]) == 2
    found = last_result(capsys.readouterr().out)
    assert found["cause"]["kind"] == "invalid"
    assert found["cause"]["detail"] == f"結果 {empty} は止まった結果でない"
    assert not log.exists()


# 現状固定（I-006）: repeat の数え方の出力（ok と fix_limit の文・metrics・instance・終了コード）と、記録の書き方。
def test_repeat_outputs_and_log_lines_are_fixed(tmp_path, capsys, last_result):
    res, log = tmp_path / "ci.json", tmp_path / "sub" / "causes.jsonl"
    args = ["repeat", "--result", str(res), "--log", str(log)]
    _stopped_ci(res, ["b", "a"])
    limit = int(route.limits.value(route.REPEAT_LIMIT, "same_cause"))
    key = json.dumps(["test_failed", ["a", "b"]], ensure_ascii=False)
    for n in range(1, limit + 1):
        assert route.main(args) == 0
        found = last_result(capsys.readouterr().out)
        assert (found["tool"], found["status"]) == ("route", "ok")
        assert found["summary"] == f"同じ原因は {n} 回目（上限 {limit}）"
        assert found["metrics"] == {"streak": n, "same_cause": limit}
    assert route.main(args) == 1
    found = last_result(capsys.readouterr().out)
    detail = f"同じ原因が {limit + 1} 回続いた（上限 {limit}）: {key}"
    assert (found["status"], found["summary"]) == ("stopped", detail)
    assert found["cause"] == {"kind": "fix_limit", "detail": detail, "instance": key}
    assert found["metrics"] == {"streak": limit + 1, "same_cause": limit}
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines == [json.dumps({"key": key}, ensure_ascii=False)] * (limit + 1)


def test_render_stops_when_omitted_worktree_is_not_usable(tmp_path, monkeypatch, capsys, last_result):
    """省いた作業場所が無ければプランを書かずに止まる（ランナーがリポジトリの根で worker・push を打たないため）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    monkeypatch.chdir(repo)
    out = tmp_path / "fix.json"
    args = _fix_args(out, repo)[:-4]  # repo と worktree を省く
    assert route.main(["render", *args]) == 2
    found = last_result(capsys.readouterr().out)
    assert found["cause"]["kind"] == "invalid"
    assert str(repo / ".worktrees" / "fix" / "x") in found["cause"]["detail"]
    assert not out.exists()


@pytest.mark.parametrize("kind", strategy_fixture.KINDS)
def test_omitted_base_is_the_sprint_pr_target_of_the_strategy(tmp_path, kind):
    """base を省けば、ブランチ戦略の設定のスプリントの PR のマージ先にする。5 種の設定で 10 系と同じ値（#3681 の受け入れ条件 1）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    strategy_fixture.write(repo, kind)
    t = route.choose("immediate_fix")
    given = {"repo": str(repo), "worktree": str(repo), "branch": "fix/x"}
    assert route.derived(t, given, repo)["base"] == strategy_fixture.EXPECTED[kind][1]
    assert route.derived(t, {**given, "base": "release"}, repo)["base"] == "release"


def test_omitted_base_stops_without_the_strategy_or_with_legacy_keys(tmp_path):
    """設定が無ければ origin の既定ブランチへ倒さずに止まり、旧いキーがあれば位置と移行のコマンドを出す。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "a")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    t = route.choose("immediate_fix")
    given = {"repo": str(repo), "worktree": str(repo), "branch": "fix/x"}
    with pytest.raises(route.RouteError, match="ndf project branch-strategy init"):
        route.derived(t, given, repo)
    strategy_fixture.write(repo, "web-develop")
    (repo / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": "develop"}), encoding="utf-8")
    with pytest.raises(route.RouteError, match="`/base_branch` は旧い.*ndf project branch-strategy migrate"):
        route.derived(t, given, repo)


def _fake_hub(seen: list[tuple[str, str]]):
    """許された方式に merge・rebase を返す偽の GitHub（読んだリポジトリと基底のブランチを `seen` へ足す）。"""

    class FakeHub:
        def __init__(self, repo, _client):
            self.repo = repo

        def allowed_merge_methods(self, branch):
            seen.append((self.repo, branch))
            return ["merge", "rebase"]

    return FakeHub


def test_omitted_merge_method_is_first_allowed_on_base(tmp_path, monkeypatch):
    """merge_method を省けば、base のブランチで許された方式の先頭にする（squash に固定しない）。読めなければ足さない。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    t = route.choose("immediate_fix")
    given = {"repo": str(repo), "worktree": str(repo), "branch": "fix/x", "base": "main"}
    assert "merge_method" not in route.derived(t, given, repo)  # origin が無い
    git(repo, "remote", "add", "origin", "https://github.com/o/r.git")
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(route.github, "GitHub", _fake_hub(seen))
    monkeypatch.setattr(route.github, "client", lambda: None)
    assert route.derived(t, given, repo)["merge_method"] == "merge"
    assert seen == [("o/r", "main")]
    assert route.derived(t, {**given, "merge_method": "squash"}, repo)["merge_method"] == "squash"
