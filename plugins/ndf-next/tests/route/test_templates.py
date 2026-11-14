"""雛形の読み込み・選び方・差し込みの埋め方と、雛形の検査。"""

from __future__ import annotations

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
    with pytest.raises(route.RouteError, match="雛形が無い"):
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
    for name in ("merge_method", "ci_budget", "tests"):
        del given[name]
    plan = route.render(t, given)
    merge = next(s for s in plan["steps"] if s["id"] == "merge")
    assert merge["run"][-1] == "--squash"


def test_base_has_no_fixed_default():
    """base を省いて決められなければ、特定のブランチへ倒さずに無い差し込みとして止まる。"""
    t = route.choose("immediate_fix")
    given = values("immediate_fix")
    del given["base"]
    with pytest.raises(route.RouteError, match="差し込みの値が無い: base"):
        route.render(t, given)


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
    assert "依頼の種類が重なる: same" in errs
    assert "雛形 b が使わない差し込みを params に持つ: base" in errs
    assert "雛形 c が params に無い差し込みを使う: title" in errs


def test_unknown_shared_step_is_refused():
    t = route.Template("x", "x_purpose", "見本", (), {"name": "x", "steps": [{"use": "no-such"}]})
    with pytest.raises(route.RouteError, match="共有のステップに no-such が無い"):
        route.expanded(t)


# 現状固定（I-008）: worker を起こすステップの展開結果（差し込みは埋めない）。
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
