"""手で終えた設計の工程の MVV 判定と、判定が省いた承認を確定する `next`（#4520 の決定 7）。

ステートマシンの DB は本物で、作業（プラン）と GitHub と判定のコマンドは偽物にする。
"""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

import merge_hub
import pytest
from result_json import last_result

from ndf.progress import approval_decide, command, gate, invoke, model, run, store
from ndf.progress.gate import asking, finishing


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manual = _load("test_manual_gate")
env = manual.env  # 同じ偽物の Env（pytest のフィクスチャ）
DESIGN, DETAIL, DESIGN_PR = manual.DESIGN, manual.DETAIL, manual.DESIGN_PR


class Judge:
    """判定のコマンドの偽物。呼び出しを残し、`held` の出力を返す（`stop` なら失敗する。`meanwhile` は判定の間の出来事）。"""

    def __init__(self, held: dict) -> None:
        self.held, self.stop, self.meanwhile = held, None, None
        self.runs: list[invoke.StageRun] = []

    def __call__(self, r, cwd, work_dir, answer=None):
        self.runs.append(r)
        if self.meanwhile is not None:
            self.meanwhile()
        if self.stop is not None:
            return invoke.StageResult(run.result.stopped("script", self.stop, "判定できない"), {})
        return invoke.finished(r, run.result.result("script", run.result.OK, "判定した", outputs={"mvv_gate": self.held}))


def _held(skip: bool, **target) -> dict:
    out = {"gate": "design", "skip": skip, "verdict": None, "why": [] if skip else ["人へ問う"], "holds": []}
    out |= {"pr": DESIGN_PR, "head": merge_hub.HEAD, "noted": True, "note_url": "https://example.invalid/note"}
    if skip:
        bound = {"repo": merge_hub.REPO, "pr": DESIGN_PR, "base": "main", "head": merge_hub.HEAD, "production": True}
        out["target"] = {**bound, **target}
    return out


@pytest.fixture
def judge(env, monkeypatch):
    found = Judge(_held(True))
    monkeypatch.setattr(finishing, "judge_invoker", found)
    return found


def _auto(capsys, repo) -> str:
    command.main(["new", "s1", "--mode", "standard", "--issues", str(manual.ISSUE), "--pace", "auto"])
    tid = last_result(capsys.readouterr().out)["metrics"]["machine_id"]
    manual._before_design(capsys, repo, tid)
    return tid


def _finish_design(capsys, repo, tid: str) -> dict:
    out = manual._finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})
    assert out["status"] == "ok", out
    return out


def _answered(st, tid: str) -> list[str]:
    return [ev.draft.payload.get("by") for ev in run.read(st, tid).row_events(DESIGN) if ev.draft.kind == run.ANSWERED]


def _trail(env) -> list[str]:
    written: list[str] = []
    env.env = replace(env.env, mvv_trail=lambda answered: written.append(answered["by"]))
    return written


def test_finish_judges_the_design_gate_for_any_pace_and_the_material_shows_it(repo, st, env, judge, capsys):
    judge.held = _held(False)
    tid = manual._new(capsys)
    manual._before_design(capsys, repo, tid)

    _finish_design(capsys, repo, tid)

    (called,) = judge.runs
    assert called.args["argv"][-2:] == ["--expect-pr", str(DESIGN_PR)], called.args
    assert called.inputs["worktree"] == str(repo) and called.step == "design-mvv"
    assert run.read(st, tid).context()["mvv_gate"] == judge.held
    out = manual._next(st, tid, repo, env)
    assert out["status"] == "gate", out
    v = run.read(st, tid)
    q = gate.waiting(v)
    assert "人へ問う" in asking.material(v, replace(q, target=gate.Target(head=merge_hub.HEAD)))


def test_a_skipping_verdict_is_approved_by_the_next_calls_and_finishes_after_the_merge(repo, st, env, judge, capsys):
    tid = _auto(capsys, repo)
    out = _finish_design(capsys, repo, tid)
    written = _trail(env)
    assert "MVV" in out["summary"] and _answered(st, tid) == [] and manual._state(st, tid, DESIGN) == "running", out

    first = manual._next(st, tid, repo, env)

    assert first["status"] == "ok" and _answered(st, tid) == [model.MVV_SERVICE] and written == [], first

    stopped = manual._next(st, tid, repo, env)

    assert stopped["status"] == "stopped" and stopped["cause"]["kind"] == "precondition", stopped
    assert written == [model.MVV_SERVICE] and manual._state(st, tid, DETAIL) == "pending"

    manual._merge(env)
    manual._next(st, tid, repo, env)

    assert manual._state(st, tid, DESIGN) == "finished" and manual._state(st, tid, DETAIL) == "finished"
    assert len(judge.runs) == 1 and "design" not in env.calls


def test_the_progress_sync_runs_after_the_answer_lock_is_released(repo, st, env, judge, capsys):
    tid = _auto(capsys, repo)
    _finish_design(capsys, repo, tid)
    _trail(env)
    manual._merge(env)
    manual._next(st, tid, repo, env)  # MVV の承認を記録する
    synced: list[str] = []

    def records(_cwd):
        with store.approval_lock(st, tid):  # 排他が持たれていれば Refused
            synced.append(manual._state(st, tid, DESIGN))
        return manual.lanes._NoRecords()

    env.env = replace(env.env, records=records)

    manual._next(st, tid, repo, env)

    assert synced and synced[0] == "finished", synced


@pytest.mark.parametrize("changed", ["head", "base", "production", "no_target"])
def test_a_verdict_of_another_target_is_not_approved(repo, st, env, judge, capsys, changed):
    tid = _auto(capsys, repo)
    if changed == "no_target":
        del judge.held["target"]
    elif changed != "head":
        judge.held["target"] = {**judge.held["target"], changed: "develop" if changed == "base" else False}
    _finish_design(capsys, repo, tid)
    written = _trail(env)
    if changed == "head":
        env.hub.pulls_of[DESIGN_PR] = merge_hub.pull(DESIGN_PR, head=merge_hub.OTHER)

    out = manual._next(st, tid, repo, env)

    assert out["status"] == "gate" and out["metrics"]["stage"] == DESIGN, out
    assert _answered(st, tid) == [] and written == []
    assert manual._next(st, tid, repo, env)["status"] == "gate" and _answered(st, tid) == []


def test_a_rejection_in_the_pr_trail_keeps_the_hand_finished_stage_waiting(repo, st, env, judge, capsys):
    tid = _auto(capsys, repo)
    _finish_design(capsys, repo, tid)
    manual._merge(env)
    withheld = approval_decide.Withheld("利用者の却下がある", DESIGN_PR, frozenset())
    env.env = replace(env.env, mvv_trail=lambda _answered: withheld)
    manual._next(st, tid, repo, env)

    out = manual._next(st, tid, repo, env)

    assert out["status"] == "gate" and manual._state(st, tid, DESIGN) == "running", out


def test_a_verdict_does_not_skip_while_an_objection_remains(repo, st, env, judge, capsys):
    tid = _auto(capsys, repo)
    payload = {"kind": invoke.LISTEN, "gate": "design", "step": "design-gate", "answer": "request_changes", "late": True}
    run._write(st, tid, model.EventDraft("answered", "conductor", DESIGN, reason="直して", payload=payload, rev=1))

    _finish_design(capsys, repo, tid)
    out = manual._next(st, tid, repo, env)

    assert judge.runs[0].args["argv"][-1] == "--no-skip", judge.runs[0].args
    assert out["status"] == "gate" and model.MVV_SERVICE not in _answered(st, tid), out


def test_a_failed_judgement_waits_for_a_human_with_a_warning(repo, st, env, judge, capsys):
    judge.stop = "not_applicable"
    tid = _auto(capsys, repo)

    out = _finish_design(capsys, repo, tid)

    assert any("判定" in w for w in out["warnings"]), out
    assert "mvv_gate" not in run.read(st, tid).context()
    assert manual._next(st, tid, repo, env)["status"] == "gate" and _answered(st, tid) == []


def test_an_approval_recorded_during_the_judgement_is_used_without_judging_again(repo, st, env, judge, capsys):
    tid = _auto(capsys, repo)
    env.hub.pulls_of[DESIGN_PR] = merge_hub.pull(DESIGN_PR, ref="sprint/s1", merged=True)

    def approve() -> None:
        shown = finishing.ask_question(st, tid, DESIGN, DESIGN_PR, repo)
        assert manual._answer(st, tid, repo, env, shown)["status"] == "ok"

    judge.meanwhile = approve

    _finish_design(capsys, repo, tid)

    assert manual._state(st, tid, DESIGN) == "finished" and len(judge.runs) == 1
    assert "finish_held" not in manual._kinds(st, tid, DESIGN)


def test_a_finish_with_a_standing_approval_does_not_judge(repo, st, env, judge, capsys):
    judge.held = _held(False)
    tid = manual._approved_in_the_flow(st, capsys, repo, env)
    manual._merge(env)

    _finish_design(capsys, repo, tid)

    assert judge.runs == [] and manual._state(st, tid, DESIGN) == "finished"


def test_the_flow_judges_again_after_a_hand_judgement_was_rejected(repo, st, env, judge, capsys):
    """手の判定の記録は、失敗の後に流し直す工程の判定に使わない。"""
    judge.held = _held(False)
    tid = manual._new(capsys)
    manual._before_design(capsys, repo, tid)
    _finish_design(capsys, repo, tid)
    shown = manual._next(st, tid, repo, env)
    manual._answer(st, tid, repo, env, shown, "rejected")
    manual._next(st, tid, repo, env)
    finishing.main(["retry", tid, DESIGN, "--reason", "直した"])
    capsys.readouterr()
    env.calls.clear()

    manual._next(st, tid, repo, env)

    assert "design-mvv" in env.calls, env.calls


def test_a_refused_finish_is_not_judged(repo, st, env, judge, capsys):
    tid = _auto(capsys, repo)
    with store.run_lock(st, tid):
        out = manual._finish(capsys, repo, tid, DESIGN, {"design_pr": DESIGN_PR})
    assert out["cause"]["kind"] == "refused" and judge.runs == [], out
