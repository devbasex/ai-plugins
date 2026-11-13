"""supervise_lib/review_units.py（#3163）: レビューの単位でプランへレビューのステップを差し込む・外す。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import branch_flow  # noqa: E402
from supervise_lib import release_flow, review_units as ru, sprint_close_steps, sprint_waves  # noqa: E402

SELECTED = {"start": "production", "integrate": "check", "doc_prs": "sprint", "promotion": "selected"}
BEHAVIOR_IDS = ["behavior-targets", "behavior", "behavior-verify"]


def args(reviews=None, bundles=None, mode="standard", flow=None, **kw) -> argparse.Namespace:
    decl = flow if flow is not None else {**SELECTED, **({"reviews": reviews} if reviews is not None else {})}
    snap = branch_flow.parse_flow(decl).resolve("develop", "main").as_snapshot("宣言")
    return argparse.Namespace(flow=snap, bundles=bundles, base="develop", production_branch="main", mode=mode, scope=None, **kw)


def impl_plan() -> dict:
    return {"steps": [{"id": "impl", "next": "pr"}, {"id": "pr", "next": "ready"}, {"id": "ready", "next": "merge-gate"}]}


def check_plan() -> dict:
    """検査のプランのレビューの並び（`templates._inspect_steps` と同じ行き先）。"""
    return {
        "steps": [
            {"id": "pr", "next": "assess"},
            {"id": "assess", "skip_to": "review", "on_fail": "refactor", "next": "refactor"},
            {"id": "refactor", "on_exit": {"6": "review"}, "on_fail": "abort", "next": "review"},
            {"id": "review", "on_fail": "abort", "next": "behavior-targets"},
            {"id": "behavior-targets", "skip_to": "base-sync", "next": "behavior"},
            {"id": "behavior", "next": "behavior-verify"},
            {"id": "behavior-verify", "next": "base-sync"},
            {"id": "base-sync", "next": "end"},
        ]
    }


def ids(plan: dict) -> list[str]:
    return [s["id"] for s in plan["steps"]]


def targets(plan: dict) -> set[str]:
    out = set()
    for s in plan["steps"]:
        out |= {s[k] for k in ("next", "on_fail", "skip_to", "gate_next") if k in s}
        out |= set((s.get("on_exit") or {}).values())
    return out


def test_review_row_reads_the_snapshot_and_the_bundles():
    a = args({"impl": {"checks": ["behavior"], "diff": "pr"}, "sprint": {"checks": list(branch_flow.CHECKS), "diff": "unchecked"}})
    assert ru.review_row(a, "impl").checks == ("behavior",)
    assert ru.review_row(args(), "impl").diff == "none"
    assert ru.review_row(args(bundles=[]), "impl").checks == ("cross-review",)
    assert ru.review_row(argparse.Namespace(), "sprint").checks == branch_flow.CHECKS  # スナップショットの無い呼び手は既定値


def test_chosen_checks_keep_the_order_and_drop_refactoring_when_suspended(monkeypatch):
    """I7: リファクタリングを止める条件は宣言より先に効く。"""
    row = branch_flow.ReviewRow("impl", ("cross-review", "cross-refactoring"), "pr")
    assert ru.chosen_checks(args(), row) == ("cross-refactoring", "cross-review")
    assert ru.chosen_checks(args(mode="light"), row) == ("cross-review",)
    monkeypatch.setattr(ru.templates, "refactor_suspended", lambda a: True)
    assert ru.chosen_checks(args(), row) == ("cross-review",)


def test_insert_reviews_places_every_chosen_review_before_ready():
    plan = ru.insert_reviews(args(), impl_plan(), branch_flow.CHECKS)
    at = ids(plan).index("ready")
    assert ids(plan)[2:at] == ["review-push", "assess", "refactor", "review", *BEHAVIOR_IDS, "review-pull"]
    assert plan["steps"][1]["next"] == "review-push" and plan["steps"][2]["next"] == "assess"
    assert {s.get("on_fail") for s in plan["steps"]} - {None} == {"refactor", "stop"}
    assert "base-sync" not in targets(plan)


def test_insert_reviews_with_only_cross_review_matches_the_bundled_impl_plan():
    """既定（まとまりの記録あり）の並び: review-push → review → review-pull → ready。"""
    plan = ru.insert_reviews(args(), impl_plan(), ("cross-review",))
    assert ids(plan) == ["impl", "pr", "review-push", "review", "review-pull", "ready"]
    review = plan["steps"][3]
    assert review["drive"] == "cross-review" and review["next"] == "review-pull" and review["on_fail"] == "stop"


def test_insert_reviews_leaves_the_plan_without_checks_and_needs_ready():
    assert ids(ru.insert_reviews(args(), impl_plan(), ())) == ["impl", "pr", "ready"]
    with pytest.raises(ValueError):
        ru.insert_reviews(args(), {"steps": [{"id": "impl"}]}, ("cross-review",))


def test_drop_unchosen_redirects_to_the_following_step():
    plan = ru.drop_unchosen(check_plan(), ("cross-refactoring", "behavior"))
    assert "review" not in ids(plan) and "review" not in targets(plan)
    assert next(s for s in plan["steps"] if s["id"] == "refactor")["next"] == "behavior-targets"
    bare = ru.drop_unchosen(check_plan(), ())
    assert ids(bare) == ["pr", "base-sync"] and bare["steps"][0]["next"] == "base-sync"


def test_apply_impl_and_apply_check_follow_the_declaration():
    """受け入れ条件 2: 選んだレビューのステップだけが現れる。"""
    a = args(
        {"impl": {"checks": ["cross-review", "behavior"], "diff": "pr"}, "sprint": {"checks": ["cross-refactoring"], "diff": "unchecked"}}
    )
    impl = ru.apply_impl(a, impl_plan())
    assert {"review", *BEHAVIOR_IDS} <= set(ids(impl)) and "assess" not in ids(impl)
    check = ru.apply_check(a, check_plan())
    assert ids(check) == ["pr", "assess", "refactor", "base-sync"]


def test_apply_without_a_declaration_keeps_the_defaults():
    """受け入れ条件 3・I5: まとまりの記録が無ければ実装 PR にレビューを置かず検査は 3 つすべて、有れば実装 PR に cross-review。"""
    assert ids(ru.apply_impl(args(), impl_plan())) == ["impl", "pr", "ready"]
    assert ids(ru.apply_check(args(), check_plan())) == ids(check_plan())
    assert "review" in ids(ru.apply_impl(args(bundles=[]), impl_plan()))
    assert "review" not in ids(ru.apply_check(args(bundles=[]), check_plan()))


def test_design_review_ids_match_the_design_plan():
    """外す id が `_review_steps` の出力と一致する（設計のプランの並びを変えると落ちる）。"""
    assert tuple(s["id"] for s in sprint_waves._review_steps(1, "c", "p"))[:-1] == ru.DESIGN_REVIEW_IDS
    assert sprint_waves._review_steps(1, "c", "p")[-1]["id"] == ru.DESIGN_AFTER_REVIEW


def test_apply_design_drops_the_review_bundle_only_for_none():
    steps = [{"id": "pr", "next": "review"}, *sprint_waves._review_steps(1, "c", "p"), {"id": "gate", "inputs": ["review", "g"]}]
    plan = ru.apply_design(args({"design": {"checks": [], "diff": "none"}}), {"steps": [dict(s) for s in steps]})
    assert ids(plan) == ["pr", "requirements-recheck", "gate"] and plan["steps"][0]["next"] == "requirements-recheck"
    assert plan["steps"][-1]["inputs"] == ["g"]
    kept = ru.apply_design(args(), {"steps": [dict(s) for s in steps]})
    assert len(kept["steps"]) == len(steps)
    with pytest.raises(ValueError):
        ru.apply_design(args({"design": {"checks": [], "diff": "none"}}), {"steps": [{"id": "pr"}]})


def test_spec_pr_gets_a_document_review_only_when_chosen():
    a = args({"spec": {"checks": ["cross-review"], "diff": "pr"}}, name="s1")
    got = sprint_close_steps._spec_pr_steps(a, "#1", "next-step")
    assert [s["id"] for s in got] == ["spec", "pr", "spec-review", "spec-review-pull", "ready"]
    assert got[1]["next"] == "spec-review" and got[2]["drive"] == "cross-review" and got[3]["next"] == "ready"
    assert [s["id"] for s in sprint_close_steps._spec_pr_steps(args(name="s1"), "#1", "x")] == ["spec", "pr", "ready"]


def test_release_review_steps_follow_the_release_row():
    a = args(
        {"release": {"checks": ["cross-review"], "diff": "resolution"}},
    )
    got = release_flow.review_range_steps(a, "/r", "2.0.0")
    assert [s["id"] for s in got] == ["review-open", "review", "review-close"]
    assert got[0]["on_exit"] == {"3": "facts"} and got[0]["pr_from"] == "review_pr" and "--diff resolution" in got[0]["cmd"]
    assert got[1]["drive"] == "cross-review" and got[1]["on_fail"] == "judge" and got[2]["next"] == "facts"
    assert release_flow.review_range_steps(args(), "/r", "2.0.0") == []
    step = release_flow.release_branch_step(a, "/r", "2.0.0", ["sprint/s1"], "review-open")
    assert step["next"] == "review-open"
    assert release_flow.release_branch_step(a, "/r", "2.0.0", ["sprint/s1"])["next"] == "facts"


def test_cleanup_deletes_the_review_branches_only_when_the_release_row_reviews():
    from supervise_lib.release_templates import _cleanup_step

    assert "review/release/v2.0.0-base" in _cleanup_step("2.0.0", "1", "/r", True)["cmd"]
    assert "review/release" not in _cleanup_step("2.0.0", "1", "/r")["cmd"]
