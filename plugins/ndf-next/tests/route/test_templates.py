"""テンプレートの読み込み・選び方・差し込みの埋め方と、テンプレートの検査。"""

from __future__ import annotations

import json
import subprocess

import pytest

from ndf.core import route
from ndf.core.plan import model

from ._flow import plan_of, values


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
