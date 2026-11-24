"""ステップの層（steps）: 結果の読み取り・作業ディレクトリ・前の子のプロセス・秘密を伏せる。"""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import git_sandbox
import pytest

from ndf.core.plan import model, steps


@pytest.fixture
def repo(tmp_path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git_sandbox.sh(path, "init", "-q")
    git_sandbox.sh(path, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "init")
    return path


def _step(**kw) -> model.Step:
    return model.parse({"name": "p", "steps": [{"id": "s", "run": ["true"], **kw}]}).steps[0]


def test_outcome_reads_the_last_result_line():
    line = json.dumps(
        {"tool": "t", "status": "stopped", "summary": "x", "cause": {"kind": "usage_limit", "detail": "d", "retry_after": 30}}
    )
    out = steps.outcome_of(_step(), 1, "log\n" + line + "\n")
    assert (out["status"], out["kind"], out["retry_after"]) == ("stopped", "usage_limit", 30)


@pytest.mark.parametrize("status", ["ok", "gate", "まだ無い語"])
def test_outcome_of_a_newer_version_is_stopped_before_its_values_are_used(status):
    line = json.dumps({"version": 2, "tool": "t", "status": status, "summary": "s", "outputs": {"pr": 3}, "routes": {"approved": "x"}})
    out = steps.outcome_of(_step(), 0, line)
    assert (out["status"], out["kind"], out["outputs"], out["routes"]) == ("stopped", "precondition", None, None)


def test_outcome_ignores_the_version_of_json_that_is_not_a_result():
    assert steps.outcome_of(_step(), 0, json.dumps({"version": 2, "name": "x"}))["status"] == "ok"


def test_outcome_without_json_uses_the_exit_code():
    step = _step(exit_cause={"1": "check_failed"})
    assert steps.outcome_of(step, 0, "no json")["status"] == "ok"
    assert steps.outcome_of(step, 1, "")["kind"] == "check_failed"
    assert steps.outcome_of(step, 2, "")["kind"] == "unknown"


def test_outcome_of_a_gate_keeps_its_routes():
    line = json.dumps({"status": "gate", "summary": "s", "routes": {"approved": "x"}})
    assert steps.outcome_of(_step(), 10, line)["routes"] == {"approved": "x"}


def test_outcome_of_a_stopped_step_keeps_its_gaps():
    # PR 4307 の r8-2: worker が返した欠け（incomplete の詳細）をプランの停止結果へ渡す。ok の gaps は拾わない
    gap = {"kind": "output", "name": "branch", "reason": "無い"}
    line = json.dumps({"status": "stopped", "summary": "s", "cause": {"kind": "incomplete", "detail": "d"}, "gaps": [gap]})
    assert steps.outcome_of(_step(), 6, line)["gaps"] == [gap]
    assert steps.outcome_of(_step(), 0, json.dumps({"status": "ok", "summary": "s", "gaps": [gap]}))["gaps"] is None
    assert steps.outcome_of(_step(), 0, "no json")["gaps"] is None


def test_steps_after_the_worktree_is_removed_run_in_the_repo_root(repo, tmp_path):
    """マージで作業場所が消えた・登録が外れた後のステップはリポジトリの根で打つ（#2969・#3022・#3186・#1886）。"""
    wt = tmp_path / "wt"
    git_sandbox.sh(repo, "worktree", "add", "-q", str(wt))
    plan = model.parse({"name": "p", "repo": str(repo), "worktree": str(wt), "steps": [{"id": "s", "run": ["true"]}]})
    assert steps.workdir(plan, plan.steps[0]) == str(wt)
    shutil.rmtree(repo / ".git" / "worktrees")
    assert steps.workdir(plan, plan.steps[0]) == str(repo)
    shutil.rmtree(wt)
    assert steps.workdir(plan, plan.steps[0]) == str(repo)


def _ctx(tmp_path, **step) -> steps.StepContext:
    plan = model.parse({"name": "p", "steps": [{"id": "s", **step}]})
    return steps.StepContext(plan, tmp_path / "state", {"state_dir": str(tmp_path / "state")})


def test_run_refuses_a_child_left_on_another_host(tmp_path):
    path = tmp_path / "state" / steps.STEPS_DIR / "s.pid.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pid": 1, "host": socket.gethostname() + "-other", "started": 0}), encoding="utf-8")
    ctx = _ctx(tmp_path, run=["true"])
    out = steps.run(ctx, ctx.plan.steps[0])
    assert (out["status"], out["kind"]) == ("stopped", "refused")
