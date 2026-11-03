"""レーンの判定（#2436 の I12）と、レーンの `queue --max` の既定（プランの本数）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import parallel_lane as pl  # noqa: E402
import parallel_plan as pp  # noqa: E402

PLAN = {
    "version": 1,
    "name": "p1",
    "rules": [],
    "lanes": [{"name": "la", "input": {"kind": "sprint", "issues": [1]}, "plans": 2, "touches": ["a/"]}],
}


def _main(tmp_path: Path) -> Path:
    d = pp.plan_dir(tmp_path, "p1")
    d.mkdir(parents=True)
    (d / "plan.json").write_text(json.dumps(PLAN))
    return tmp_path


def test_lane_only_while_plan_is_running(tmp_path):
    main = _main(tmp_path)
    running = lambda _m, _n: "PENDING"  # noqa: E731
    assert pl.lane_of(main, "la", status=running)[1].plans == 2
    assert pl.lane_of(main, "lb", status=running) is None
    assert pl.lane_of(main, "la", status=lambda _m, _n: "SUCCESS") is None
    assert pl.lane_of(main, "la", status=lambda _m, _n: None) is None


def test_queue_max_default(tmp_path, monkeypatch):
    import supervise

    main = _main(tmp_path)
    monkeypatch.setattr(pl, "lane_plans", lambda m, lane=None: 2 if Path(m) == main else None)
    assert supervise.default_max(str(main)) == 2
    assert supervise.default_max(str(tmp_path / "other")) == 3


def test_non_lane_sprint_keeps_prod_plan(monkeypatch):
    from supervise_lib import sprint

    monkeypatch.setattr(sprint, "parallel_lane", lambda repo: None)
    assert list(sprint.prod_plans(None, "/r", lambda: {"x": 1})) == ["release-prod"]
    monkeypatch.setattr(sprint, "parallel_lane", lambda repo: ("p1", "la"))
    import parallel_release

    monkeypatch.setattr(
        parallel_release,
        "plan_release_request",
        lambda a, repo, plan, lane, prs=None: {"フェーズ": "配布の依頼", "plan": plan, "lane": lane},
    )
    got = sprint.prod_plans(None, "/r", lambda: {"x": 1})
    assert got == {"release-request": {"フェーズ": "配布の依頼", "plan": "p1", "lane": "la"}}


def test_release_request_plan_has_one_gate_step():
    import argparse

    import supervise_lib  # noqa: F401  lib/ と supervise_lib を読めるようにする
    import parallel_release

    plan = parallel_release.plan_release_request(argparse.Namespace(issue=[2436], mode="standard"), "/r", "p1", "la", ["{queue_pr:check}"])
    assert plan["フェーズ"] == "配布の依頼" and [s["id"] for s in plan["steps"]] == ["parallel-request"]
    assert "request p1 --lane la --prs {queue_pr:check} --issues 2436" in plan["steps"][0]["cmd"]


def test_present_gate_puts_approve_after_mvv(tmp_path):
    import supervise_lib  # noqa: F401
    import parallel_release

    steps = parallel_release.present_gate_steps(str(tmp_path / "a.json"), "/r", "issues/x.md", "1 2", "standard", "/f", "/a")
    ids = [s["id"] for s in steps]
    assert ids[:3] == ["mvv", "note", "approve"] and steps[1]["next"] == "approve" and steps[2]["next"] == "end"


def test_lane_request_without_prs_uses_previous_stage_prs(monkeypatch):
    from supervise_lib import sprint

    import parallel_release

    seen = {}
    monkeypatch.setattr(sprint, "parallel_lane", lambda repo: ("p1", "la"))
    monkeypatch.setattr(parallel_release, "plan_release_request", lambda a, repo, plan, lane, prs=None: seen.setdefault("prs", prs))
    sprint.prod_plans(None, "/r", lambda: {})
    assert seen["prs"] == ["{queue_prs}"]


def test_closes_by_pr_reads_in_bulk(tmp_path):
    import supervise_lib  # noqa: F401
    import parallel_release

    calls = []

    def bulk(prs, cwd=None):
        calls.append(list(prs))
        return {1: {"closingIssuesReferences": [{"number": 7}]}, 2: None}, ""

    def per_pr(*a, **k):
        raise AssertionError("PR ごとに gh を起動しない")

    assert parallel_release.closes_by_pr(tmp_path, [1, 2], run=per_pr, bulk=bulk) == {1: [7], 2: []}
    assert calls == [[1, 2]]


def test_present_plan_starts_and_collects_range_only(tmp_path):
    import supervise_lib  # noqa: F401
    import parallel_release
    from supervise_lib import limits

    d = tmp_path / "releases" / "1" / "1"
    present, _ = parallel_release.plan_parallel_release(SCRIPTS.parents[2], "p1", d, "1.2.3")
    by = {s["id"]: s for s in present["steps"]}
    assert limits.run_error(present) is None  # on_fail が無いステップを指さない
    assert not any(by[i].get("on_fail") for i in ("facts", "explain", "others"))
    assert by["facts"].get("gate_as_ok") is True  # facts の 10 を関門で残さない
    assert "--prs 0" not in json.dumps(present) and "--prs  --approval" in by["facts"]["cmd"]


def test_release_plan_keeps_base_branch_and_takes_approved_commit_for_worktree(tmp_path):
    import supervise_lib  # noqa: F401
    import parallel_release
    from supervise_lib import paths

    d = tmp_path / "releases" / "1" / "1"
    _, release = parallel_release.plan_parallel_release(SCRIPTS.parents[2], "p1", d, "1.2.3")
    assert release["起点"].startswith("origin/") and release["起点のコミット"] == "{queue_approved:present}"
    plan = {**release, "起点のコミット": "abc123"}
    assert paths._resolve_base(plan, str(tmp_path)) == ("abc123", None)  # 作業ツリーは承認したコミットから


def test_answer_state_carries_approved_project_mvv(tmp_path, monkeypatch):
    import parallel_release
    import project_mvv

    approved = project_mvv.ProjectMvv(status="approved", version=4, sha256="s")
    monkeypatch.setattr(project_mvv, "load_mvv", lambda root: approved)
    assert parallel_release.answer_state(tmp_path) == {"gates": [], "project_mvv": {"version": 4, "sha256": "s"}}
    monkeypatch.setattr(project_mvv, "load_mvv", lambda root: project_mvv.ProjectMvv(status="none"))
    assert parallel_release.answer_state(tmp_path) == {"gates": []}


def test_present_writes_to_present_plan_state_dir(tmp_path, monkeypatch):
    import supervise_lib  # noqa: F401
    import approved_commit
    import parallel_release
    from release_lib import included
    from supervise_lib.paths import state_dir_of

    monkeypatch.setattr(approved_commit, "material_sha", lambda p: "abc")
    monkeypatch.setattr(included, "read_included", lambda p: [5])
    plan = pp.ParallelPlan.model_validate(PLAN, context={"max_plans": 10, "max_named": 10})
    d = tmp_path / "releases" / "1" / "1"
    parallel_release.present(tmp_path, plan, tmp_path / "a.md", [], d, closes_of=lambda r, prs: {})
    work = state_dir_of(str(d / "present.json")) / "work"
    assert (work / "prs.txt").read_text().strip() == "5" and (work / "approved.txt").read_text().strip() == "abc"


def test_lane_prod_stage_chains_after_release(monkeypatch, tmp_path):
    import argparse

    from supervise_lib import sprint

    monkeypatch.setattr(sprint, "parallel_lane", lambda repo: ("p1", "la"))
    monkeypatch.setattr(sprint, "has_release_template", lambda a: True)
    monkeypatch.setattr(sprint, "has_prod_branch", lambda a: True)
    monkeypatch.setattr(sprint, "design_read", lambda a: False)
    monkeypatch.setattr(sprint, "advises", lambda a: False)
    for f in ("plan_sprint_branch", "plan_sprint_impl", "plan_sprint_check", "plan_sprint_release"):
        monkeypatch.setattr(sprint, f, lambda *a, **k: {})
    monkeypatch.setattr(sprint, "prod_plans", lambda a, repo, build, prs=None: {"release-request": {}})
    a = argparse.Namespace(worktree=str(tmp_path), design=[], issue=[1], version=None, pace="normal")
    waves = sprint.sprint_plans(a)
    assert waves[-1]["name"] == "本番" and waves[-1]["then_of"] == waves[0]["name"]  # 先頭の command へ --then でつなぐ


def test_merged_on_production_checks_the_production_branch(tmp_path, monkeypatch):
    """配布済みの照合は origin/main 固定でなく、本番チャネル（repo.production_branch）の先を見る。"""
    import subprocess

    import repo

    monkeypatch.setattr(repo, "production_branch", lambda _root, decl=None: ("live", "宣言"))
    seen = []

    def run(cmd, **_kw):
        seen.append(cmd)
        out = "abc\n" if cmd[:2] == ["gh", "pr"] else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    assert pl.merged_on_production(tmp_path, [7], run=run) is True
    assert seen[-1][-1] == "origin/live"
    monkeypatch.setattr(repo, "production_branch", lambda _root, decl=None: (None, "無い"))
    assert pl.merged_on_production(tmp_path, [7], run=run) is False
