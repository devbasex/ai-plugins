"""読み手役のステップの無い古い定義のスプリントでの `ndf progress reread`（`ndf document-rebuild gate` の直打ち。#4446）。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from ndf.contracts import result
from ndf.progress import definition, gate, invoke, run, stages


@pytest.fixture
def old_table(monkeypatch, new_table):
    """読み手役のステップを除いた定義でステートマシンを作り、ID を返す。"""
    load = definition.load_definition

    def without_reading(mode):
        snap = load(mode)
        kept = tuple(replace(s, steps=tuple(st for st in s.steps if st.kind != stages.READING_KIND)) for s in snap.stages)
        return replace(snap, stages=kept)

    monkeypatch.setattr(definition, "load_definition", without_reading)
    return new_table


def _reading_env(env, report, calls):
    """直打ちの読み手役（ステップ `reading`）だけ `report` を返して呼び出しを残し、ほかは既定の偽の作業を流す。"""

    def invoker(r, cwd, work_dir, answer):
        if r.step != "reading":
            return env.invoker(r, cwd, work_dir, answer)
        calls.append((r, cwd, work_dir, answer))
        return invoke.finished(r, report)

    return run.Env(**{**vars(env), "invoker": invoker})


def _expected_argv(work_dir, *extra):
    return [invoke.PACKAGE, "document-rebuild", "gate", "--gate", "design", "--pr", "7",
            "--state-dir", str(work_dir), "--out", str(work_dir / stages.READING_OUT), *extra]  # fmt: skip


def _question(st, tid):
    q = gate.waiting(run.read(st, tid))
    assert q is not None
    return q


def test_reread_without_a_reading_step_runs_the_gate_command_directly(st, env, old_table, flow):
    tid = old_table()
    flow.turn(tid)
    before = _question(st, tid)
    calls = []
    done = result.result("script", "ok", "読み手役が済んだ")
    out = gate.reread(st, tid, flow.repo, _reading_env(env, done, calls))
    after = _question(st, tid)
    assert out["status"] == "gate" and after.id != before.id and len(calls) == 1
    call, cwd, work_dir, answer = calls[0]
    # 保存先は出し直した後の質問の id ごとで、打つ前に作る
    assert work_dir.name == f"reading-{after.id}" and work_dir.is_dir()
    assert work_dir.parent.parent == run.work_root(st.root, tid)
    assert cwd == flow.repo and answer is None
    assert (call.kind, call.name, call.step, call.range) == (invoke.SCRIPT, invoke.PACKAGE, "reading", "none")
    assert call.args == {"argv": _expected_argv(work_dir)}


def test_a_failed_direct_reading_returns_its_report_and_keeps_waiting(st, env, old_table, flow):
    tid = old_table()
    flow.turn(tid)
    failed = result.stopped("script", "transient", "読めない")
    out = gate.reread(st, tid, flow.repo, _reading_env(env, failed, []))
    assert out == failed and gate.waiting(run.read(st, tid)) is not None


def test_a_pushed_fix_reruns_the_review_and_the_next_direct_reading_gets_the_record_arguments(st, env, old_table, flow):
    # review_again なら設計のレビューから流し直し、次の直打ちは工程の記録から作る引数（--reviewed・--no-doc-fix）を末尾に足す
    tid = old_table()
    flow.turn(tid)
    pushed = "c" * 40
    again = {"commit": pushed, "docs": ["issues/issue-9999-design.md"]}
    fixed = result.result("script", "ok", "文書を直した", outputs={"review_again": again})
    out = gate.reread(st, tid, flow.repo, _reading_env(env, fixed, []))
    assert out["status"] == "ok" and out["next"]["argv"][2] == "wait" and gate.waiting(run.read(st, tid)) is None
    assert run.decide(run.read(st, tid)).step.id == "design-review"

    run.work(st, tid, flow.repo, env)
    calls = []
    done = result.result("script", "ok", "読み手役が済んだ")
    assert gate.reread(st, tid, flow.repo, _reading_env(env, done, calls))["status"] == "gate"
    call, _cwd, work_dir, _answer = calls[0]
    assert call.args["argv"] == _expected_argv(work_dir, "--reviewed", pushed, "--no-doc-fix")
