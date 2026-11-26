"""ステップの層（steps）: 結果の読み取り・作業ディレクトリ・前の子のプロセス・秘密を伏せる。"""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import git_sandbox
import pytest

from ndf.core import route
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


# --- PR の番号の書き戻し（#4369） -----------------------------------------------------------


class _Done:
    """通ったことにする子のプロセス（ステップのコマンドは起動しない）。"""

    pid = 1
    returncode = 0

    def communicate(self, timeout=None):
        return "", ""


def _run_impl_step(tmp_path, monkeypatch, step_id: str, got: dict | None, *, writes: bool = True) -> tuple[dict, list[str], list[str]]:
    """テンプレート impl のステップ `step_id` を打ち、（結果、照会したステップ、打ったステップ）を返す。"""
    raw = route.expanded(route.templates()["impl"])
    plan = model.parse({**raw, "name": "impl-sprint/s1"})
    asked: list[str] = []
    launched: list[str] = []

    def probe(ctx, step, timeout):
        asked.append(step.id)
        if writes and got is not None and got["status"] == "ok":
            (tmp_path / "pr-number").write_text("7\n", encoding="utf-8")
        return got

    monkeypatch.setattr(steps, "_probe", probe)
    monkeypatch.setattr(steps, "_launch", lambda ctx, step: launched.append(step.id) or _Done())
    (tmp_path / steps.STEPS_DIR).mkdir(exist_ok=True)
    out = steps.run(steps.StepContext(plan, tmp_path, {"state_dir": str(tmp_path)}), plan.step(step_id))
    return out, asked, launched


@pytest.mark.parametrize(
    ("step_id", "asked"),
    [("head-reviewed", []), ("resolved", []), ("conflict-check", ["pr"]), ("review", ["pr"]), ("ci", ["pr"]), ("review-delta", ["pr"])],
)
def test_the_missing_pr_number_is_asked_only_by_a_step_that_reads_it(tmp_path, monkeypatch, step_id, asked):
    # PR の番号を保存する前に始めた実装。head-reviewed が通る再開と resolved は、照会せずに保存済みの記録で終わる
    out, got_asked, launched = _run_impl_step(tmp_path, monkeypatch, step_id, {"status": "ok"})
    assert (out["status"], got_asked, launched) == ("ok", asked, [step_id])


def test_the_saved_pr_number_is_not_asked_again(tmp_path, monkeypatch):
    (tmp_path / "pr-number").write_text("7\n", encoding="utf-8")
    assert _run_impl_step(tmp_path, monkeypatch, "ci", None)[1] == []


@pytest.mark.parametrize(
    ("got", "reason"),
    [
        (None, "開いた PR が無い"),
        ({"status": "stopped", "kind": "precondition", "summary": "照会できない"}, "照会できない"),
    ],
)
def test_the_step_stops_with_the_reason_when_the_pr_number_is_not_restored(tmp_path, monkeypatch, got, reason):
    out, _, launched = _run_impl_step(tmp_path, monkeypatch, "review-delta", got)
    assert (out["status"], out["kind"], launched) == ("stopped", "precondition", [])
    assert reason in out["summary"]


def test_the_step_stops_when_the_probe_passes_without_writing_the_file(tmp_path, monkeypatch):
    out, _, launched = _run_impl_step(tmp_path, monkeypatch, "ci", {"status": "ok"}, writes=False)
    assert (out["status"], out["kind"], launched) == ("stopped", "precondition", [])


def test_a_relative_restore_file_is_read_from_the_working_directory_of_the_step(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "f").write_text("7\n", encoding="utf-8")
    raw = [{"id": "w", "run": ["true"], "probe": ["true"]}, {"id": "r", "run": ["true"], "restore": "f"}]
    restores = {"f": {"file": "f", "step": "w", "missing": "m"}}
    plan = model.parse({"name": "p", "repo": str(root), "restores": restores, "steps": raw})
    monkeypatch.setattr(steps, "_probe", lambda *a: pytest.fail("打たない"))
    monkeypatch.setattr(steps, "_launch", lambda ctx, step: _Done())
    state = tmp_path / "state"
    (state / steps.STEPS_DIR).mkdir(parents=True)
    assert steps.run(steps.StepContext(plan, state, {}), plan.step("r"))["status"] == "ok"


def test_a_step_without_restore_does_not_run_the_probe_of_another_step(tmp_path, monkeypatch):
    # 同じパスを引数に持つ独自のプランでも、指定の無いステップは指定の順の外で `probe` を打たない
    path = "{state_dir}/pr-number"
    raw = [{"id": "w", "run": ["true", path], "probe": ["true", path]}, {"id": "r", "run": ["true", path]}]
    plan = model.parse({"name": "p", "steps": raw})
    monkeypatch.setattr(steps, "_probe", lambda *a: pytest.fail("打たない"))
    monkeypatch.setattr(steps, "_launch", lambda ctx, step: _Done())
    (tmp_path / steps.STEPS_DIR).mkdir()
    assert steps.run(steps.StepContext(plan, tmp_path, {"state_dir": str(tmp_path)}), plan.step("r"))["status"] == "ok"


@pytest.mark.parametrize(
    ("restore", "name"),
    [
        ({"file": "f", "step": "無い", "missing": "m"}, "f"),
        ({"file": "f", "step": "r", "missing": "m"}, "f"),  # probe を持たないステップ
        ({"file": "f", "step": "w"}, "f"),
        ({"file": "f", "step": "w", "missing": "m"}, "無い"),  # restores に無い名前
    ],
)
def test_a_restore_that_does_not_name_a_probe_step_is_refused(restore, name):
    raw = [{"id": "w", "run": ["true"], "probe": ["true"]}, {"id": "r", "run": ["true"], "restore": name}]
    with pytest.raises(model.PlanError, match="restore"):
        model.parse({"name": "p", "restores": {"f": restore}, "steps": raw})
