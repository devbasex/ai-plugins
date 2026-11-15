"""本番のプランの形（#3572 の I1・I2・I6・I10・I13）: 雛形から組んだ本番のプランが承認の後に直しのステップを持たず、
`merge` の部品を持つ雛形は本番の PR をマージせずにデプロイの工程へ渡す。"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass

import pytest

from ndf.core import route
from ndf.domains.release import cli as release_cli
from ndf.domains.release import release_plan

from ._fakes import FakeHub, pull


def _expanded(name: str) -> dict:
    return route.expanded(route.templates()[name])


def test_every_release_template_has_no_fix_step_and_starts_with_the_merge() -> None:
    """受け入れ条件 3・5: release: true の雛形はどれも、worker・直しのステップを持たず、最初が本番の PR のマージ。"""
    released = [n for n, t in route.templates().items() if route.expanded(t).get("release") is True]
    assert released == ["release"]
    for name in released:
        assert release_plan.forbidden(_expanded(name)) == []
        assert release_plan.forbidden(route.expanded(route.templates()[name], local_tests=False)) == []


def test_the_merge_plan_marked_as_release_is_refused_for_its_fix_steps() -> None:
    """受け入れ条件 3: 直しのステップを持つ merge の形を本番のプランにすると、ci-fix・push・fix-push と最初のステップを返す。"""
    plan = {**_expanded("merge"), "release": True}
    got = release_plan.forbidden(plan)
    for sid in ("ci-fix", "push", "fix-push"):
        assert any(line.startswith(f"{sid}:") for line in got)
    assert any("最初のステップ" in line for line in got)


@pytest.mark.parametrize(
    "step",
    [
        {"id": "work", "run": ["{{worker}}", "release"]},
        {"use": "push"},
        {"id": "prepare-next", "run": ["ndf", "release-steps", "prepare-next", "--pr", "{{pr}}"]},
    ],
)
def test_a_worker_or_push_step_put_back_into_the_release_plan_is_refused(step: dict) -> None:
    """受け入れ条件 3・I2・I10: worker・push・次の版の用意を本番のプランへ戻すと行を返し、先頭に足せば最初のステップの行も返す。"""
    plan = _expanded("release")
    added = route._expand_step(step) if "use" in step else step
    plan["steps"] = [*plan["steps"], added]
    assert len(release_plan.forbidden(plan)) == 1
    plan["steps"] = [added, *plan["steps"][:-1]]
    assert len(release_plan.forbidden(plan)) == 2


def test_the_rendered_release_plan_merges_the_approved_pr_first() -> None:
    """受け入れ条件 5・7・I2: 組んだ本番のプランは merge → confirm → record → cleanup で、最初のステップが --pr を渡すマージ。"""
    t = route.templates()["release"]
    plan = route.render(t, {"repo": "/r", "pr": "41"})
    ids = [s["id"] for s in plan["steps"]]
    assert ids == ["merge", "confirm", "record", "cleanup"]
    first = plan["steps"][0]["run"]
    assert first[:3] == list(release_plan.merge_run()) and first[first.index("--pr") + 1] == "41"
    assert "worker" not in t.params and "base" not in t.params


@pytest.mark.parametrize("name", ["merge", "check", "close"])
def test_the_merge_part_follows_the_guard_that_hands_the_production_pr_to_the_deploy_stage(name: str) -> None:
    """I6・I13: merge の部品（check は merge-gate）の直前が release-guard で、原因 production_pr の行き先が end。"""
    plan = _expanded(name)
    assert release_plan.guard_before_merge(plan) == []
    ids = [s["id"] for s in plan["steps"]]
    first = min(ids.index(x) for x in release_plan.merge_part_ids() if x in ids)
    guard = plan["steps"][first - 1]
    assert guard["exit_cause"][str(release_plan.GUARD_EXIT)] == release_plan.guard_cause() == "production_pr"
    assert release_plan.GUARD_EXIT == 3


def test_a_plan_without_the_merge_part_needs_no_guard() -> None:
    assert release_plan.guard_before_merge(_expanded("release")) == []


def test_the_guard_check_names_each_broken_part() -> None:
    """I13: release-guard を外す・先頭へ移す・原因を変える・production_pr を ci-fix へ流すと、破れを返す。"""
    plan = _expanded("merge")
    at = [s["id"] for s in plan["steps"]].index(release_plan.GUARD_ID)
    without = copy.deepcopy(plan)
    guard = without["steps"].pop(at)
    assert len(release_plan.guard_before_merge(without)) == 1
    moved = copy.deepcopy(without)
    moved["steps"] = [guard, *moved["steps"]]
    assert len(release_plan.guard_before_merge(moved)) == 1
    other = copy.deepcopy(plan)
    other["steps"][at]["exit_cause"] = {str(release_plan.GUARD_EXIT): "precondition"}
    assert len(release_plan.guard_before_merge(other)) == 1
    routed = copy.deepcopy(plan)
    routed["steps"][at]["on_cause"] = {"production_pr": "ci-fix"}
    assert len(release_plan.guard_before_merge(routed)) == 1


def test_is_production_pr_compares_the_names_as_they_are() -> None:
    assert release_plan.is_production_pr("main", "main")
    assert not release_plan.is_production_pr("develop", "main")
    assert not release_plan.is_production_pr("Main", "main") and not release_plan.is_production_pr(" main", "main")


@dataclass
class _HeadHub(FakeHub):
    """ブランチの開いた PR を `pulls` から読む偽物。"""

    def pulls_by_head(self, branches):
        return {b: [p for p in self.pulls.values() if p.head_ref == b] for b in branches}


def _guard(monkeypatch, capsys, hub: FakeHub, *extra: str) -> tuple[int, dict]:
    monkeypatch.setattr(release_cli, "_hub", lambda args: hub)
    code = release_cli.main(["guard", "--branch", "release/v1.2.4", *extra])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_guard_hands_a_production_pr_to_the_deploy_stage(monkeypatch, capsys) -> None:
    """I6: 本番の PR（main 宛てのスプリントの PR）は production_pr（終了コード 3）で渡し、マージを呼ばない。"""
    hub = _HeadHub(pulls={7: pull(ref="sprint/x")})
    monkeypatch.setattr(release_cli, "_hub", lambda args: hub)
    code = release_cli.main(["guard", "--branch", "sprint/x", "--production", "main"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == release_plan.GUARD_EXIT == 3 and out["cause"]["kind"] == "production_pr" and "#7" in out["summary"]
    assert hub.merged == []


def test_guard_refuses_the_verify_ref(monkeypatch, capsys, tmp_path) -> None:
    """受け入れ条件 14・I9: head が検証用ブランチなら refused（終了コード 1）で止まる。"""
    (tmp_path / ".ndf").mkdir()
    row = {
        "target": "検証版",
        "kind": "auto",
        "trigger": "wf",
        "channel": "verify",
        "versioned": False,
        "verify": {"ref": "verify/next", "marketplace": "x-next"},
    }
    (tmp_path / ".ndf" / "project.json").write_text(json.dumps({"delivery": [row]}), encoding="utf-8")
    hub = _HeadHub(pulls={9: pull(number=9, ref="verify/next")})
    monkeypatch.setattr(release_cli, "_hub", lambda args: hub)
    code = release_cli.main(["guard", "--root", str(tmp_path), "--branch", "verify/next", "--production", "main"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 1 and out["cause"]["kind"] == "refused" and "検証用ブランチは main へ戻さない" in out["cause"]["detail"]


@pytest.mark.parametrize("pulls", [{8: pull(number=8, base="develop")}, {}])
def test_guard_lets_a_base_pr_or_a_branch_without_pr_through(monkeypatch, capsys, pulls) -> None:
    """I6: ベースブランチ宛ての PR と、開いた PR の無いブランチは終了コード 0 で通す。"""
    code, out = _guard(monkeypatch, capsys, _HeadHub(pulls=pulls), "--production", "main")
    assert code == 0 and out["status"] == "ok"


def test_guard_without_a_production_declaration_does_not_stop_the_merge(monkeypatch, capsys, tmp_path) -> None:
    """本番ブランチを宣言しないリポジトリの merge のプランは止めない（確かめられないことを警告に出す）。"""
    code, out = _guard(monkeypatch, capsys, _HeadHub(pulls={7: pull()}), "--root", str(tmp_path))
    assert code == 0 and out["warnings"]
