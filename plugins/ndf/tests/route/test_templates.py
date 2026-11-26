"""テンプレートの読み込み・選び方・差し込みの埋め方と、テンプレートの検査。"""

from __future__ import annotations

import json
import os
import shlex
import subprocess

import pytest

from ndf.core import route
from ndf.core.plan import assemble_decide, model

from ._flow import fake_bin, plan_of, run_step, step_of, values, walk


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


def _run_refactor_wrap(tmp_path, gh_body: str) -> list[str]:
    """偽の gh と偽のエントリポイントで `{{refactor}}` の包みを打ち、エントリポイントが受けた引数を返す。"""

    def script(name: str, body: str):
        path = tmp_path / "bin" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    script("gh", gh_body)
    entry = script("entry", 'printf "%s\\n" "$@" > "$ARGS_FILE"')
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "base")
    git("branch", "start")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "a.py")
    git("commit", "-q", "-m", "add")
    argv = route.refactor_launch("sprint/s1", "start", ["test"])
    argv[argv.index("ndf")] = str(entry)
    args = tmp_path / "args"
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}", "ARGS_FILE": str(args)}
    subprocess.run(argv, cwd=repo, env=env, check=True, capture_output=True)
    return args.read_text(encoding="utf-8").splitlines()


def test_refactor_wrap_passes_only_the_number_when_gh_writes_to_stderr(tmp_path):
    # gh は AI_AGENT・CLAUDECODE があると成功しても標準エラーに hint を出す。番号に混ぜない
    got = _run_refactor_wrap(tmp_path, "echo 'hint: use gh event listen' >&2\necho 42")
    assert got[got.index("--pr") + 1 :] == ["42", "--scope", "./a.py"]


def test_refactor_wrap_keeps_the_gh_message_in_the_stopped_result_without_a_pr(tmp_path):
    got = _run_refactor_wrap(tmp_path, "echo 'no pull requests found' >&2\nexit 1")
    assert got[:6] == ["contracts", "emit", "--tool", "refactor", "--status", "stopped"]
    assert got[-1] == "gh: no pull requests found"


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


# --- PR の番号の保存とフォークの除外（#4369） ---

_PR_URL = "https://github.com/o/r/pull/12"
_PR_LIST = [{"number": 7, "body": "b", "isCrossRepository": True}, {"number": 12, "body": "b", "isCrossRepository": False}]


def _state(tmp_path, number: str | None = None):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    if number is not None:
        (state / "pr-number").write_text(f"{number}\n", encoding="utf-8")
    return state


def _recording_gh(tmp_path, body: str):
    """受けた引数を `gh-args` に 1 行 1 引数で足してから `body` を実行する偽の gh。"""
    return fake_bin(tmp_path, "gh", f'printf "%s\\n" "$@" >> {shlex.quote(str(tmp_path / "gh-args"))}\n{body}')


def _listing_gh(tmp_path, prs):
    """`pr list` の `--jq` を本物の jq で `prs` に当てる偽の gh。"""
    return _recording_gh(
        tmp_path,
        f'p=; for a in "$@"; do [ "$p" = --jq ] && q=$a; p=$a; done; printf %s {shlex.quote(json.dumps(prs))} | jq -r "$q"',
    )


def _probe_of(step, tmp_path, bindir, state):
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
    args = assemble_decide.fill(step.probe, {"state_dir": str(state)})
    return subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True, check=False, timeout=60)


def test_pr_step_saves_the_number_from_the_url_of_the_created_pr(tmp_path):
    step = step_of(plan_of("implement", branch="u1", base="sprint/x"), "pr")
    state = _state(tmp_path)
    gh = _recording_gh(tmp_path, f"echo {_PR_URL}")
    got = run_step(step, tmp_path, gh, {"state_dir": str(state)})
    assert (got["status"], got["prs"]) == ("ok", [12])
    assert (state / "pr-number").read_text(encoding="utf-8") == "12\n"
    # 作成の後にブランチ名で番号を引き直さない
    assert "view" not in (tmp_path / "gh-args").read_text(encoding="utf-8").split()


def test_pr_step_fails_without_a_number_at_the_end_of_the_url(tmp_path):
    step = step_of(plan_of("implement"), "pr")
    state = _state(tmp_path)
    got = run_step(step, tmp_path, _recording_gh(tmp_path, "echo https://github.com/o/r/pull/new"), {"state_dir": str(state)})
    assert got["status"] == "stopped"
    assert not (state / "pr-number").exists()


def test_pr_probe_skips_fork_prs_and_saves_the_found_number(tmp_path):
    step = step_of(plan_of("implement", branch="u1", base="sprint/x"), "pr")
    state = _state(tmp_path)
    done = _probe_of(step, tmp_path, _listing_gh(tmp_path, _PR_LIST), state)
    assert done.returncode == 0 and json.loads(done.stdout)["items"] == [{"kind": "pr", "number": 12}]
    assert (state / "pr-number").read_text(encoding="utf-8") == "12\n"
    args = (tmp_path / "gh-args").read_text(encoding="utf-8").splitlines()
    assert args[args.index("--head") + 1] == "u1" and args[args.index("--base") + 1] == "sprint/x"


def test_pr_probe_does_not_take_a_pr_of_a_fork_alone(tmp_path):
    step = step_of(plan_of("implement"), "pr")
    state = _state(tmp_path)
    done = _probe_of(step, tmp_path, _listing_gh(tmp_path, _PR_LIST[:1]), state)
    assert (done.returncode, done.stdout) == (1, "")
    assert not (state / "pr-number").exists()


def test_pull_request_template_probe_skips_fork_prs(tmp_path):
    step = step_of(plan_of("pull_request", refs=[]), "pr")
    done = _probe_of(step, tmp_path, _listing_gh(tmp_path, _PR_LIST), _state(tmp_path))
    assert done.returncode == 0 and json.loads(done.stdout)["outputs"] == {"pr": 12}
    alone = _probe_of(step, tmp_path, _listing_gh(tmp_path, _PR_LIST[:1]), _state(tmp_path))
    assert (alone.returncode, alone.stdout) == (1, "")


def test_impl_conflict_check_and_ci_use_the_saved_pr_number(tmp_path):
    plan = plan_of("implement", branch="u1", ci_budget="5", required=["test"], tests=[])
    state = _state(tmp_path, "12")
    fill = {"state_dir": str(state)}
    gh = _recording_gh(tmp_path, "echo CLEAN")
    assert run_step(step_of(plan, "conflict-check"), tmp_path, gh, fill)["status"] == "ok"
    assert (tmp_path / "gh-args").read_text(encoding="utf-8").splitlines()[:3] == ["pr", "view", "12"]
    dirty = fake_bin(tmp_path / "d", "gh", "echo DIRTY")
    assert run_step(step_of(plan, "conflict-check"), tmp_path, dirty, fill)["kind"] == "base_conflict"
    seen = tmp_path / "ndf-args"
    ndf = fake_bin(tmp_path / "n", "ndf", f'printf "%s\\n" "$@" > {shlex.quote(str(seen))}')
    assert run_step(step_of(plan, "ci"), tmp_path, ndf, fill)["status"] == "ok"
    assert seen.read_text(encoding="utf-8").splitlines()[:4] == ["ci", "watch", "--pr", "12"]


@pytest.mark.parametrize(("step_id", "code"), [("conflict-check", 2), ("ci", 1)])
@pytest.mark.parametrize("number", [None, ""])
def test_impl_conflict_check_and_ci_stop_without_a_pr_number(tmp_path, step_id, code, number):
    step = step_of(plan_of("implement"), step_id)
    state = _state(tmp_path, number)
    seen = tmp_path / "called"
    bindir = fake_bin(tmp_path, "gh", f"touch {shlex.quote(str(seen))}")
    fake_bin(tmp_path, "ndf", f"touch {shlex.quote(str(seen))}")
    got = run_step(step, tmp_path, bindir, {"state_dir": str(state)})
    assert (got["status"], got["code"]) == ("stopped", code)
    assert not seen.exists()


@pytest.mark.parametrize(
    ("review", "restored"),
    [
        (route.review_launch("impl", "u1", "origin/develop"), True),
        (route.unit_review_launch("o/r", ["my-review"]), True),
        (["my-review"], False),  # 設定で差し替えた起動は番号を読まない
    ],
)
def test_impl_review_steps_restore_the_pr_number_only_when_the_launch_reads_it(review, restored):
    found = model.parse(plan_of("implement", review=review))
    kept = {s.id for s in found.steps if s.restore is not None}
    assert kept == {"conflict-check", "ci"} | ({"review", "review-delta"} if restored else set())


def test_impl_default_review_reads_the_saved_pr_number(tmp_path):
    got = route.review_launch("impl", "u1", "origin/develop")
    assert "u1" not in got and "{state_dir}/pr-number" in got
    state = _state(tmp_path, "12")
    argv = assemble_decide.fill([*got[:4], "echo", "run"], {"state_dir": str(state)})
    assert subprocess.run(argv, capture_output=True, text=True, check=True).stdout == "run --pr 12\n"
    (state / "pr-number").unlink()
    assert subprocess.run(argv, capture_output=True, check=False).returncode == 1


def test_impl_plan_carries_the_unit_file_which_is_empty_by_default():
    given = values("implement")
    del given["unit_file"]
    t = route.choose("implement")
    assert route.render(t, given)["unit_file"] == ""
    assert route.render(t, {**given, "unit_file": "/s/unit.md"})["unit_file"] == "/s/unit.md"


# --- 単位の経路のテンプレートとレビューの起動（#4369） ---

_CI_ONLY_REACHABLE = ["ci", "repeat-check", "ci-fix", "ci-local", "push-fixed"]


def _targets(step: dict) -> set[str]:
    found = {step[k] for k in route.TARGET_KEYS if k in step}
    return found.union(*(set(step[k].values()) for k in route.TARGET_TABLE_KEYS if k in step))


def test_unit_template_drops_the_done_check_and_gives_the_repository_to_ci():
    t = route.unit_template(route.choose("implement"), "o/r", ["done-check"])
    plan = route.render(t, values("implement"))
    ids = [s["id"] for s in plan["steps"]]
    assert ids[0] == "work" and "done-check" not in ids
    assert next(s for s in plan["steps"] if s["id"] == "ci")["run"][-2:] == ["--repo", "o/r"]
    # 元のテンプレートは変えない
    assert [s["id"] for s in plan_of("implement")["steps"]][0] == "done-check"
    assert "--repo" not in next(s for s in plan_of("implement")["steps"] if s["id"] == "ci")["run"]


@pytest.mark.parametrize("local_tests", [True, False])
def test_unit_template_without_ci_needs_no_ci_values_and_keeps_no_route_to_it(local_tests):
    t = route.unit_template(route.choose("implement"), "o/r", ["done-check", *_CI_ONLY_REACHABLE])
    given = {k: v for k, v in values("implement").items() if k not in ("required", "ci_budget", "tests")}
    plan = route.render(t, given, local_tests=local_tests)
    model.parse(plan)
    gone = {"done-check", *_CI_ONLY_REACHABLE}
    assert not gone & {s["id"] for s in plan["steps"]}
    assert not gone & set().union(*(_targets(s) for s in plan["steps"]))
    assert not {"required", "ci_budget", "tests"} & set(route.needed(t, local_tests=local_tests))
    assert "ci-fix" not in route.kinds_of(t, local_tests)
    visited, _ = walk(plan, {})
    assert visited[visited.index("review-head") + 1] == "head-reviewed"


def _fake_review(tmp_path):
    """受けた引数を `review-args` に書き、`--reviewed-out` を書き、`WROTE` があれば `--reviewed-pr-out` へ書き、`CODE` で終える。"""
    body = """
printf '%s\\n' "$@" > "$ARGS_FILE"
p=; for a in "$@"; do
  [ "$p" = --reviewed-out ] && echo abc > "$a"
  [ "$p" = --reviewed-pr-out ] && [ -n "$WROTE" ] && printf '%s\\n' "$WROTE" > "$a"
  p=$a
done
exit "${CODE:-0}"
"""
    return fake_bin(tmp_path, "review", body) / "review"


def _run_unit_review(tmp_path, monkeypatch, *, wrote: str, code: int = 0, number: str | None = "12", extra=()):
    state = _state(tmp_path, number)
    for name, value in (("ARGS_FILE", str(tmp_path / "review-args")), ("WROTE", wrote), ("CODE", str(code))):
        monkeypatch.setenv(name, value)
    argv = [*route.unit_review_launch("o/r", [str(_fake_review(tmp_path))]), *extra]
    argv += ["--reviewed-out", str(state / "reviewed-head")]
    return subprocess.run(assemble_decide.fill(argv, {"state_dir": str(state)}), capture_output=True, text=True, check=False), state


@pytest.mark.parametrize("code", [0, 6])
def test_unit_review_keeps_the_exit_code_when_the_reviewed_pr_matches(tmp_path, monkeypatch, code):
    done, state = _run_unit_review(tmp_path, monkeypatch, wrote="o/r#12", code=code, extra=["--base", "abc", "--state-dir", "d"])
    assert (done.returncode, done.stdout) == (code, "")
    assert (state / "reviewed-head").exists()
    args = (tmp_path / "review-args").read_text(encoding="utf-8").splitlines()
    assert args == [
        "--base", "abc", "--state-dir", "d", "--reviewed-out", str(state / "reviewed-head"),
        "--repo", "o/r", "--pr", "12", "--stop-on-hold", "--reviewed-pr-out", str(state / "reviewed-pr"),
    ]  # fmt: skip


@pytest.mark.parametrize("wrote", ["o/r#13", "o/other#12", ""])
@pytest.mark.parametrize("code", [0, 6])
def test_unit_review_stops_when_the_reviewed_pr_differs_or_is_not_written(tmp_path, monkeypatch, wrote, code):
    # 前の起動が残した reviewed-pr は、起動の前に消すので確かめに使わない
    (_state(tmp_path) / "reviewed-pr").write_text("o/r#12\n", encoding="utf-8")
    done, state = _run_unit_review(tmp_path, monkeypatch, wrote=wrote, code=code)
    assert done.returncode == 3
    found = json.loads(done.stdout.splitlines()[-1])
    assert (found["status"], found["cause"]["kind"]) == ("stopped", "precondition")
    assert not (state / "reviewed-head").exists()


def test_unit_review_keeps_the_exit_code_of_a_failed_launch_and_removes_the_reviewed_head(tmp_path, monkeypatch):
    done, state = _run_unit_review(tmp_path, monkeypatch, wrote="o/r#12", code=4)
    assert (done.returncode, done.stdout) == (4, "")
    assert not (state / "reviewed-head").exists()


@pytest.mark.parametrize("number", [None, ""])
def test_unit_review_does_not_launch_without_a_pr_number(tmp_path, monkeypatch, number):
    done, _ = _run_unit_review(tmp_path, monkeypatch, wrote="o/r#12", number=number)
    assert done.returncode == 2
    assert not (tmp_path / "review-args").exists()


@pytest.mark.parametrize("step_id", ["review", "review-delta"])
def test_impl_review_steps_with_the_unit_launch_stop_on_another_pr(tmp_path, monkeypatch, step_id):
    """初回と差分のレビューのステップに単位の起動を入れても、後ろに足した引数が届き、違う PR を読めば止まる。"""
    review = route.unit_review_launch("o/r", [str(_fake_review(tmp_path))])
    step = step_of(plan_of("implement", review=review), step_id)
    state = _state(tmp_path, "12")
    (state / "reviewed-head").write_text("abc\n", encoding="utf-8")
    monkeypatch.setenv("ARGS_FILE", str(tmp_path / "review-args"))
    monkeypatch.setenv("WROTE", "o/r#13")
    got = run_step(step, tmp_path, fill={"state_dir": str(state)})
    assert (got["status"], got["kind"]) == ("stopped", "precondition")
    assert not (state / "reviewed-head").exists()
    monkeypatch.setenv("WROTE", "o/r#12")
    (state / "reviewed-head").write_text("abc\n", encoding="utf-8")
    assert run_step(step, tmp_path, fill={"state_dir": str(state)})["status"] == "ok"
    assert "--reviewed-out" in (tmp_path / "review-args").read_text(encoding="utf-8").splitlines()
