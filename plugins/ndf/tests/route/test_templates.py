"""テンプレートの読み込み・選び方・差し込みの埋め方と、テンプレートの検査。"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from ndf.core import route
from ndf.core.plan import model

from ._flow import fake_bin, ndf_bin, plan_of, repo_with_base, values


def test_all_templates_render_into_valid_plans():
    found = route.templates()
    assert route.template_errors(found.values()) == []
    for t in found.values():
        model.parse(route.render(t, values(t.purpose)))


def test_choose_unknown_purpose_is_refused():
    with pytest.raises(route.RouteError, match="テンプレートが無い"):
        route.choose("no_such_purpose")


def test_missing_value_and_unknown_name_and_shape_are_refused():
    t = route.choose("immediate_fix")
    given = values("immediate_fix")
    del given["branch"]
    with pytest.raises(route.RouteError, match="値が無い: branch"):
        route.render(t, given)
    with pytest.raises(route.RouteError, match="無い差し込み: worker"):
        route.render(t, values("immediate_fix", worker=["w"]))
    with pytest.raises(route.RouteError, match="並びで渡す"):
        route.render(t, values("immediate_fix", required="test"))


def test_defaults_fill_omitted_values():
    t = route.choose("immediate_fix")
    given = values("immediate_fix")
    for name in ("ci_budget", "tests"):
        del given[name]
    plan = route.render(t, given)
    ci = next(s for s in plan["steps"] if s["id"] == "ci")
    assert ci["run"][ci["run"].index("--budget") + 1] == "3600"
    assert not any(a.startswith("--test=") for a in ci["run"])


def test_merge_method_has_no_fixed_default():
    """merge_method を省いて決められなければ、squash へ倒さずに無い差し込みとして止まる。"""
    t = route.choose("immediate_fix")
    given = values("immediate_fix")
    del given["merge_method"]
    with pytest.raises(route.RouteError, match="差し込みの値が無い: merge_method"):
        route.render(t, given)


def test_base_has_no_fixed_default():
    """base を省いて決められなければ、特定のブランチへ倒さずに無い差し込みとして止まる。"""
    t = route.choose("immediate_fix")
    given = values("immediate_fix")
    del given["base"]
    with pytest.raises(route.RouteError, match="差し込みの値が無い: base"):
        route.render(t, given)


def _repo(tmp_path, ndf_settings=None):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    if ndf_settings is not None:
        (tmp_path / ".ndf").mkdir()
        (tmp_path / ".ndf" / "worktree.json").write_text(json.dumps(ndf_settings), encoding="utf-8")
    return tmp_path


def test_omitted_remote_reads_the_ndf_settings_then_the_default(tmp_path):
    """remote を省けば設定の remote、無ければスキーマの既定（#3617 の受け入れ条件 8・9）。"""
    t = route.choose("immediate_fix")
    plain = _repo(tmp_path / "plain")
    given = {"repo": str(plain), "worktree": str(plain), "branch": "fix/x", "base": "main"}
    assert route.derived(t, given, plain)["remote"] == "origin"
    fork = _repo(tmp_path / "fork", {"version": 1, "remote": "upstream"})
    plan = route.render(t, {**values("immediate_fix"), **route.derived(t, {**given, "repo": str(fork)}, fork)})
    push = next(s for s in plan["steps"] if s["id"] == "push")
    assert push["run"][:4] == ["git", "push", "--", "upstream"]


def test_broken_remote_ndf_settings_is_refused(tmp_path):
    t = route.choose("immediate_fix")
    repo = _repo(tmp_path, {"version": 1, "remote": "-x"})
    with pytest.raises(route.RouteError, match="remote"):
        route.derived(t, {"repo": str(repo), "worktree": str(repo), "branch": "fix/x", "base": "main"}, repo)


def test_list_values_expand_per_element_and_vanish_when_empty():
    plan = plan_of("immediate_fix", required=["test", "lint"], tests=[])
    ci = next(s for s in plan["steps"] if s["id"] == "ci")
    assert "--required=test" in ci["run"] and "--required=lint" in ci["run"]
    assert not any(a.startswith("--test=") for a in ci["run"])
    # 実行のときの差し込みは埋めずに残す
    assert "{state_dir}/ci.json" in ci["run"]


def test_whole_element_list_is_spliced():
    plan = plan_of("implement", worker=["claude", "-p", "--agent", "worker"])
    work = next(s for s in plan["steps"] if s["id"] == "work")
    assert work["run"] == ["claude", "-p", "--agent", "worker", "impl"]


def test_null_override_removes_shared_key():
    plan = plan_of("immediate_fix")
    push = next(s for s in plan["steps"] if s["id"] == "push")
    assert "on_fail" not in push


def test_list_inside_plain_text_is_refused():
    t = route.Template("x", "x_purpose", "見本", ("required",), {"name": "{{required}}", "steps": [{"id": "a", "run": ["true"]}]})
    with pytest.raises(route.RouteError, match="引数の並びの中にだけ"):
        route.render(t, {"required": ["a"]})


def test_template_errors_find_duplicated_purpose_and_unused_or_undeclared_names():
    plan = {"name": "p-{{branch}}", "steps": [{"id": "a", "run": ["echo", "{{title}}"]}]}
    a = route.Template("a", "same", "見本", ("branch", "title"), plan)
    b = route.Template("b", "same", "見本", ("branch", "title", "base"), plan)
    c = route.Template("c", "other", "見本", ("branch",), plan)
    errs = route.template_errors([a, b, c])
    assert "リクエストの種類が重なる: same" in errs
    assert "テンプレート b が使わない差し込みを params に持つ: base" in errs
    assert "テンプレート c が params に無い差し込みを使う: title" in errs


def test_unknown_shared_step_is_refused():
    t = route.Template("x", "x_purpose", "見本", (), {"name": "x", "steps": [{"use": "no-such"}]})
    with pytest.raises(route.RouteError, match="共有のステップに no-such が無い"):
        route.expanded(t)


# 現状固定（I-008）: worker を起動するステップの展開結果（差し込みは埋めない）。
@pytest.mark.parametrize(
    ("template", "step", "expected"),
    [
        ("impl", "fix", {"id": "fix", "run": ["{{worker}}", "fix"], "next": "test"}),
        ("check", "fix", {"id": "fix", "run": ["{{worker}}", "fix"], "next": "test-all"}),
        ("design", "design", {"id": "design", "run": ["{{worker}}", "design"]}),
        ("close", "spec", {"id": "spec", "run": ["{{worker}}", "plan-to-spec"]}),
        ("impl", "work", {"id": "work", "run": ["{{worker}}", "impl"]}),
    ],
)
def test_worker_steps_expand_to_fixed_shape(template, step, expected):
    found = [s for s in route.expanded(route.templates()[template])["steps"] if s["id"] == step]
    assert found == [expected]


def test_omitted_remote_in_a_linked_worktree_reads_the_main_ndf_settings(tmp_path):
    """linked worktree から打っても、メインディレクトリの設定の remote を使う（作業場所の古い設定を読まない）。"""
    t = route.choose("immediate_fix")
    main = _repo(tmp_path / "main", {"version": 1, "remote": "origin"})
    commit = ["git", "-C", str(main), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "a"]
    subprocess.run(commit, check=True)
    linked = tmp_path / "linked"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", "--detach", str(linked)], check=True)
    (linked / ".ndf").mkdir(exist_ok=True)
    (linked / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "remote": "origin"}), encoding="utf-8")
    (main / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "remote": "upstream"}), encoding="utf-8")
    given = {"worktree": str(linked), "branch": "fix/x", "base": "main"}
    assert route.derived(t, given, linked)["remote"] == "upstream"


# --- 実装のテンプレートの単位の PR のレビュー（#3879 構成要素 1） ---


def _impl_steps(**given) -> dict[str, dict]:
    return {s["id"]: s for s in plan_of("implement", **given)["steps"]}


def test_impl_reviews_the_unit_pr_between_pr_and_ci_and_checks_the_reviewed_head_after_ci():
    ids = [s["id"] for s in plan_of("implement")["steps"]]
    at = ids.index("pr")
    assert ids[at : at + 10] == [
        "pr", "conflict-check", "base-fetch", "review", "push-reviewed", "review-head", "ci", "head-reviewed", "resolved",
        ids[at + 9],
    ]  # fmt: skip
    steps = _impl_steps()
    assert steps["base-fetch"]["run"] == ["git", "fetch", "<remote>", "<base>"]
    assert steps["review"]["on_cause"] == {"incomplete": "mark-unresolved"}
    assert steps["review"]["run"][3:8] == [
        "{state_dir}/reviewed-head", "{state_dir}/unresolved", "{state_dir}/unresolved-delta", "{state_dir}/delta-recheck",
        "{state_dir}/delta-base",
    ]  # fmt: skip
    assert steps["review-head"]["on_cause"] == {"unreviewed": "review-delta"}
    assert steps["ci"]["next"] == "head-reviewed"
    assert steps["head-reviewed"]["on_cause"] == {"unreviewed": "review-delta"}
    assert (steps["resolved"]["exit_cause"], steps["resolved"]["next"]) == ({"1": "incomplete"}, "end")
    assert steps["review-delta"]["next"] == "push-reviewed"
    assert steps["review-delta"]["on_cause"] == {"incomplete": "mark-delta-unresolved"}
    assert steps["mark-unresolved"]["next"] == "push-reviewed"
    assert steps["mark-delta-unresolved"]["next"] == "push-reviewed"


def test_impl_acceptance_defaults_to_the_issue():
    t = route.choose("implement")
    given = values("implement", issue="12")
    del given["acceptance"]
    plan = route.render(t, {**given, **route.launch_values(t, given)})
    step = next(s for s in plan["steps"] if s["id"] == "acceptance")
    assert step["run"] == ["ndf", "loop", "acceptance", "check", "--issue", "12"]


def test_refactor_launch_wraps_the_refactor_run_with_the_branch_and_the_base():
    # #3908 の決定 7: 設定が無いときの {{refactor}}。PR の番号と触るファイルは打つ時点に読むので、包みの位置引数に
    # ブランチ（$0）と起点（$1）を渡し、$2 以降を ndf refactor run の argv にする
    got = route.refactor_launch("sprint/s1", "origin/develop", ["test", "lint"])
    assert got[:5] == ["bash", "-c", route.REFACTOR_WITH_PR, "sprint/s1", "origin/develop"]
    assert got[5:] == ["ndf", "refactor", "run", "--branch", "sprint/s1", "--base", "origin/develop", "--required", "test", "lint"]


# gh は AI エージェントの環境（AI_AGENT・CLAUDECODE など）で、成功しても標準エラーに複数行の案内を出す
_GH_HINT = "printf 'hint: line1\\nhint: line2\\n' >&2"


def _run_refactor_launch(repo, *bindirs):
    """`refactor_launch` の argv を、`bindirs` を先頭に置いた PATH で打つ。"""
    tmpdir = repo.parent / "tmp"
    tmpdir.mkdir()
    env = {**os.environ, "PATH": os.pathsep.join([*map(str, bindirs), os.environ["PATH"]]), "TMPDIR": str(tmpdir)}
    argv = route.refactor_launch("feat/x", "origin/develop", ["test"])
    done = subprocess.run(argv, cwd=repo, env=env, capture_output=True, text=True, check=False, timeout=60)
    assert list(tmpdir.glob("ndf-gh.*")) == []  # gh の標準エラーを受けた一時ファイルは残さない
    return done


def test_refactor_launch_takes_only_the_stdout_of_gh_as_the_pr_number(tmp_path):
    repo = repo_with_base(tmp_path / "a", {"a.py": "a = 1\n"})
    gh = fake_bin(tmp_path / "gh", "gh", f"{_GH_HINT}\necho 7")
    ndf = fake_bin(tmp_path / "ndf", "ndf", "printf '%s\\n' \"$@\"")
    done = _run_refactor_launch(repo, gh, ndf)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[-4:] == ["--pr", "7", "--scope", "./a.py"]


def test_refactor_launch_without_a_pr_stops_with_the_reason_of_gh(tmp_path):
    repo = repo_with_base(tmp_path / "a", {"a.py": "a = 1\n"})
    gh = fake_bin(tmp_path / "gh", "gh", f"{_GH_HINT}\necho 'no pull requests found' >&2\nexit 1")
    done = _run_refactor_launch(repo, gh, ndf_bin(tmp_path / "ndf"))
    out = json.loads(done.stdout.splitlines()[-1])
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "precondition")
    assert "no pull requests found" in done.stdout


def test_impl_acceptance_of_several_issues_repeats_the_issue_flag():
    # #3908 の決定 7: 課題をまとめた単位は、課題ごとの --issue で全部の課題の条件を検証する（代表の課題だけにしない）
    t = route.choose("implement")
    given = values("implement", issue="12")
    del given["acceptance"]
    got = route.launch_values(t, given, issues=["30", "12"])
    assert got["acceptance"] == ["--issue", "12", "--issue", "30"]
    route.check_acceptance(got)


def test_impl_acceptance_takes_the_list_as_given():
    acceptance = ["--issue", "1", "--criteria", "1:1", "--criteria", "1:2"]
    step = _impl_steps(acceptance=acceptance)["acceptance"]
    assert step["run"] == ["ndf", "loop", "acceptance", "check", *acceptance]
