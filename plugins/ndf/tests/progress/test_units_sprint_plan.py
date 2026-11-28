"""工程「実装」の呼び出しに持たせるスプリントの保存した状態（`run.sprint_plan`。#3702 の決定 11）。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ndf.progress import design_record, model, run, run_view, stages


def _view(gated: bool, optional: tuple[str, ...], per_unit: bool = True) -> tuple[run_view.View, stages.StageDef]:
    def step(sid: str, stage: str, gate=None) -> stages.StepDef:
        kind = stages.GATE_KIND if gate else "record"
        return stages.StepDef(id=sid, stage=stage, call="ndf.script", kind=kind, range="none", gate=gate)

    review_steps = [step("units-check", "ドキュメントレビュー")]
    if gated:
        review_steps.append(step("design-gate", "ドキュメントレビュー", "design"))
    review = stages.StageDef(id="document-review", name="ドキュメントレビュー", phase="design", steps=tuple(review_steps))
    impl = stages.StageDef(
        id="implement",
        name="実装",
        phase="implement",
        steps=(step("implement", "実装"),),
        per_unit=per_unit,
        optional_inputs=optional,
    )
    snap = stages.Snapshot("standard", "1", "0" * 64, 4, (review, impl))
    answered = model.EventDraft(
        run_view.ANSWERED,
        run_view.CONDUCTOR,
        "ドキュメントレビュー",
        payload={"answer": "approved", "kind": "listen", design_record.TARGET_KEY: {"version": 1}},
        rev=1,
    )
    events = [model.Event(1, datetime(2026, 11, 28, tzinfo=timezone.utc), answered)]
    return run_view.View("sprint:x:y", 1, snap, {}, events), impl


@pytest.fixture(autouse=True)
def context(monkeypatch):
    """スプリントの文脈。`plan_omitted` は工程「計画」の出力で、文脈の組み立てはここでは確かめない。"""
    monkeypatch.setattr(run_view.View, "context", lambda self: {"issues": [3, 4], "plan_omitted": [4]})


@pytest.mark.parametrize(("gated", "record"), [(True, {"version": 1}), (False, None)], ids=["承認が要る", "承認が要らない"])
def test_the_implement_stage_gets_the_approval_and_the_omitted_plans(tmp_path, gated, record):
    view, impl = _view(gated, ("branch", "plan_omitted"))

    got = run.sprint_plan(view, impl, tmp_path)

    assert got is not None
    assert (got.required, got.record, got.omitted, got.new_definition) == (gated, record, (4,), True)
    assert got.work_root == run.work_root(Path(tmp_path), "sprint:x:y")
    assert (got.differences is not None) is gated


def test_an_older_definition_is_told_apart_and_other_stages_get_nothing(tmp_path):
    view, impl = _view(False, ("branch",))

    assert run.sprint_plan(view, impl, tmp_path).new_definition is False
    view, impl = _view(False, ("branch",), per_unit=False)
    assert run.sprint_plan(view, replace(impl, id="plan"), tmp_path) is None
    # `per_unit` の無い定義の工程「実装」にも、変換を持たせる
    assert run.sprint_plan(view, impl, tmp_path).convert is not None
    view, impl = _view(False, ("branch", "plan_omitted"))
    assert run.sprint_plan(view, impl, tmp_path).convert is None
