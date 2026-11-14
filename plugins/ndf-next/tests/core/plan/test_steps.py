"""ステップの層（steps）: 結果の読み取り・作業ディレクトリ・前の子のプロセス・秘密を伏せる。"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from ndf.core.plan import model, steps


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "init")
    return path


def _step(**kw) -> model.Step:
    return model.parse({"name": "p", "steps": [{"id": "s", "run": ["true"], **kw}]}).steps[0]


def test_outcome_reads_the_last_result_line():
    line = json.dumps({"tool": "t", "status": "stopped", "summary": "x", "cause": {"kind": "usage_limit", "detail": "d", "retry_after": 30}})
    out = steps.outcome_of(_step(), 1, "log\n" + line + "\n")
    assert (out["status"], out["kind"], out["retry_after"]) == ("stopped", "usage_limit", 30)


def test_outcome_without_json_uses_the_exit_code():
    step = _step(exit_cause={"1": "check_failed"})
    assert steps.outcome_of(step, 0, "no json")["status"] == "ok"
    assert steps.outcome_of(step, 1, "")["kind"] == "check_failed"
    assert steps.outcome_of(step, 2, "")["kind"] == "unknown"


def test_outcome_of_a_gate_keeps_its_routes():
    line = json.dumps({"status": "gate", "summary": "s", "routes": {"approved": "x"}})
    assert steps.outcome_of(_step(), 10, line)["routes"] == {"approved": "x"}


def test_steps_after_the_worktree_is_removed_run_in_the_repo_root(repo, tmp_path):
    """マージで作業場所が消えた・登録が外れた後のステップはリポジトリの根で打つ（#2969・#3022・#3186・#1886）。"""
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(wt))
    plan = model.parse({"name": "p", "repo": str(repo), "worktree": str(wt), "steps": [{"id": "s", "run": ["true"]}]})
    assert steps.workdir(plan, plan.steps[0]) == str(wt)
    shutil.rmtree(repo / ".git" / "worktrees")
    assert steps.workdir(plan, plan.steps[0]) == str(repo)
    shutil.rmtree(wt)
    assert steps.workdir(plan, plan.steps[0]) == str(repo)


def _ctx(tmp_path, **step) -> steps.StepContext:
    plan = model.parse({"name": "p", "steps": [{"id": "s", **step}]})
    return steps.StepContext(plan, tmp_path / "state", {"state_dir": str(tmp_path / "state")})


def test_run_stops_a_child_left_by_the_previous_round(tmp_path):
    """前の回の子が残っていれば止めてから打つ。子が二重に動かない（B10）。"""
    left = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True)
    try:
        steps._write_pid(tmp_path / "state", "s", left.pid)
        ctx = _ctx(tmp_path, run=[sys.executable, "-c", "print('ok')"])
        out = steps.run(ctx, ctx.plan.steps[0])
        assert out["status"] == "ok"
        assert left.wait(timeout=60) is not None
        assert not (tmp_path / "state" / steps.STEPS_DIR / "s.pid.json").exists()
    finally:
        left.kill()


def test_run_refuses_a_child_left_on_another_host(tmp_path):
    path = tmp_path / "state" / steps.STEPS_DIR / "s.pid.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pid": 1, "host": socket.gethostname() + "-other", "started": 0}), encoding="utf-8")
    ctx = _ctx(tmp_path, run=["true"])
    out = steps.run(ctx, ctx.plan.steps[0])
    assert (out["status"], out["kind"]) == ("stopped", "refused")


def test_run_fills_values_and_masks_secrets(tmp_path, monkeypatch):
    """結果の文から秘密の環境変数の値を伏せる（耐久の記録と結果 JSON に入れない。B8）。"""
    monkeypatch.setenv("NDF_TEST_TOKEN", "s3cr3t-value-123")
    code = "import json, os, sys; print(json.dumps({'status': 'ok', 'summary': sys.argv[1] + ' ' + os.environ['NDF_TEST_TOKEN']}))"
    ctx = _ctx(tmp_path, run=[sys.executable, "-c", code, "{state_dir}"])
    out = steps.run(ctx, ctx.plan.steps[0])
    assert out["summary"].startswith(str(tmp_path / "state"))
    assert "s3cr3t-value-123" not in out["summary"]


def test_run_interrupts_a_step_over_its_timeout(tmp_path):
    ctx = _ctx(tmp_path, run=[sys.executable, "-c", "import time; time.sleep(600)"], timeout=0.5)
    out = steps.run(ctx, ctx.plan.steps[0])
    assert (out["status"], out["kind"]) == ("stopped", "interrupted")
