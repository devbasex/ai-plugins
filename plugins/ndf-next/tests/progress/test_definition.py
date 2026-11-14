"""工程の定義（contracts/workflow/）の読み込みと検査（受け入れ条件 1・3・8・12。#1516・#2935・SF5）。"""

from __future__ import annotations

import copy

import pytest

from ndf import contracts
from ndf.progress import definition, model, stages

E = "file:///workflow/stage-io.schema.json#/$defs/"


def _stage_doc(name: str, row: str, steps: list[dict], **meta) -> dict:
    return {
        "document": {"dsl": "1.0.3", "namespace": "ndf", "name": name, "version": "1.0.0"},
        "input": {"schema": {"resource": {"endpoint": E + "stage-in"}}},
        "output": {"schema": {"resource": {"endpoint": E + "stage-out"}}},
        "metadata": {"ndf": {"stage": row, "phase": "close", **meta}},
        "do": steps,
    }


def _step(name: str, kind: str, rng: str, **meta) -> dict:
    return {
        name: {
            "call": "ndf.plan",
            "metadata": {"ndf": {"kind": kind, "range": rng, **meta}},
            "output": {"schema": {"resource": {"endpoint": E + "report"}}},
        }
    }


def _mode_doc(rows: list[tuple[str, str]]) -> dict:
    do = [{row: {"run": {"workflow": {"namespace": "ndf", "name": ref, "version": "1.0.0"}}}} for row, ref in rows]
    return {
        "document": {"dsl": "1.0.3", "namespace": "ndf", "name": "trial", "version": "1.0.0"},
        "metadata": {"ndf": {"height": 9}},
        "do": do,
    }


def _errors(mode_doc: dict, stage_docs: dict) -> list[str]:
    with pytest.raises(definition.WorkflowDefError) as info:
        definition.build(mode_doc, stage_docs)
    return info.value.errors


@pytest.mark.parametrize("mode", ["light", "operation", "legacy-refactor", "standard", "documentation"])
def test_all_modes_pass_every_check(mode):
    snap = definition.load_definition(mode)
    assert snap.stages and len(snap.sha256) == 64
    assert snap.stages[-1].name == "棚卸し"
    # 工程とステップのすべてに入出力の規約があり、参照の指す先がある（受け入れ条件 8）
    for stage in snap.stages:
        assert definition.ref_target(stage.input_ref) is not None
        assert definition.ref_target(stage.output_ref) is not None
        assert stage.steps and all(definition.ref_target(st.output_ref) is not None for st in stage.steps)


def test_standard_rows_run_from_requirements_to_backlog_and_hold_the_closing_stages():
    names = [s.name for s in definition.load_definition("standard").stages]
    assert names[0] == "要求と受け入れ条件" and names[-1] == "棚卸し"
    assert len(names) == 18
    # まとめの工程（確定仕様化・リリース後テスト・振り返り・棚卸し）も行にある
    closing = [s.name for s in definition.load_definition("standard").stages if s.phase == "close"]
    assert closing == ["確定仕様化", "リリース後テスト", "振り返り", "棚卸し"]


def test_modes_are_ordered_by_height():
    heights = definition.mode_heights()
    assert sorted(heights, key=heights.__getitem__) == ["light", "operation", "legacy-refactor", "standard", "documentation"]


def test_gate_steps_name_which_gate_they_are():
    # #1516: 設計の承認ゲートと本番の承認ゲートを書き分ける語が、ステップの定義から引ける
    snap = definition.load_definition("standard")
    gates = {st.gate: model.gate_label(st.gate) for st in snap.steps() if st.kind == "gate"}
    assert set(gates) == {"design", "release"}
    assert gates["design"] != gates["release"]
    assert snap.stage("設計").steps[[st.kind for st in snap.stage("設計").steps].index("gate")].gate == "design"


def test_design_hands_the_review_result_to_the_gate_without_a_final_judge():
    # SF5: 設計の工程はレビューの結果をそのまま設計承認ゲートへ渡し、最後の判定を置かない。
    # #2935: 承認は承認ゲートのステップの答え（gate-answer）で受け、ラベルの有無をステップの入力にしない
    steps = definition.load_definition("standard").stage("設計").steps
    kinds = [st.kind for st in steps]
    assert kinds.index("gate") == kinds.index("review") + 1
    assert kinds[kinds.index("gate") + 1 :] == ["merge"]
    gate = steps[kinds.index("gate")]
    assert gate.output_ref.endswith("#/$defs/gate-answer") and not gate.args


def test_refactoring_suspension_is_not_a_code_constant():
    # 受け入れ条件 3: リファクタリングの停止はコードの定数でなく宣言で表す
    import ndf.progress.command as command
    import ndf.progress.model as m

    for mod in (command, m, definition):
        assert not [n for n in vars(mod) if "SUSPEND" in n.upper()]


def test_stage_without_a_contract_is_refused():
    doc = _stage_doc("x", "行", [_step("a", "work", "none")])
    del doc["input"]
    errs = _errors(_mode_doc([("行", "x")]), {"x": doc})
    assert any("入力の規約の参照が無い" in e for e in errs)


def test_step_without_an_output_contract_is_refused():
    step = _step("a", "work", "none")
    del step["a"]["output"]
    errs = _errors(_mode_doc([("行", "x")]), {"x": _stage_doc("x", "行", [step])})
    assert any("x/a: 出力の規約の参照が無い" in e for e in errs)


def test_contract_reference_to_nowhere_is_refused():
    doc = _stage_doc("x", "行", [_step("a", "work", "none")])
    doc["output"]["schema"]["resource"]["endpoint"] = E + "no-such"
    errs = _errors(_mode_doc([("行", "x")]), {"x": doc})
    assert any("指す先が無い" in e for e in errs)


def test_output_that_breaks_the_contract_is_caught():
    stage = definition.load_definition("standard").stage("デプロイ")
    assert (
        definition.validate_stage_output(stage, {"steps": [], "summary": "出した", "outputs": {"delivery": {}, "version": "1.2.3"}}) == []
    )
    errs = definition.validate_stage_output(stage, {"steps": [], "outputs": {"version": 3}})
    assert any("summary" in e for e in errs)
    assert any("version" in e for e in errs)
    assert any("delivery" in e for e in errs)


def test_input_needs_the_declared_context_keys():
    stage = definition.load_definition("standard").stage("作業場所の用意")
    assert definition.validate_stage_input(stage, {"context": {"issues": [1]}}) == []
    assert definition.validate_stage_input(stage, {"context": {}}) == ["context: 文脈の鍵 issues が無い"]


def test_unknown_metadata_words_and_if_are_refused():
    step = _step("a", "work", "none", speed="fast")
    step["a"]["if"] = "${ .x }"
    doc = _stage_doc("x", "行", [step], inputs=["nope"])
    errs = _errors(_mode_doc([("行", "x")]), {"x": doc})
    assert any("要素の if は使わない" in e for e in errs)
    assert any("speed" in e for e in errs)
    assert any("nope はスプリントの文脈の鍵に無い" in e for e in errs)


def test_review_needs_a_subject_and_other_steps_refuse_it():
    doc = _stage_doc("x", "行", [_step("a", "review", "sprint_diff"), _step("b", "work", "none", subject="code")])
    errs = _errors(_mode_doc([("行", "x")]), {"x": doc})
    assert any("x/a" in e for e in errs) and any("x/b" in e for e in errs)


def test_document_outside_the_standard_schema_is_refused():
    errs = _errors({"do": []}, {})
    assert errs and all(e.startswith("mode: ") for e in errs)


def test_row_name_must_match_the_stage_document():
    errs = _errors(_mode_doc([("別の名前", "x")]), {"x": _stage_doc("x", "行", [_step("a", "work", "none")])})
    assert any("行名と同じにする" in e for e in errs)


def test_two_stages_with_the_same_name_are_refused():
    docs = {"x": _stage_doc("x", "行", [_step("a", "work", "none")])}
    errs = _errors(_mode_doc([("行", "x"), ("行", "x")]), docs)
    assert any("2 つの工程が受け持つ" in e for e in errs)


def _close_plan_2026_11_12() -> tuple[dict, dict]:
    """2026-11-12 のまとめのプラン（#3126）を定義に写したもの。本番のリリースの後に最後の検査がある。"""
    docs = {
        "release": _stage_doc("release", "デプロイ", [_step("prod", "release", "sprint_diff")]),
        "spec": _stage_doc("spec", "確定仕様化", [_step("spec-pr", "work", "none")]),
        "final": _stage_doc(
            "final",
            "最後の検査",
            [
                _step("cross-review", "review", "sprint_diff", subject="code"),
                _step("base-review", "review", "base", subject="docs"),
            ],
        ),
        "again": _stage_doc("again", "もう一度", [_step("cross-review-2", "review", "sprint_diff", subject="code")]),
    }
    return _mode_doc([("デプロイ", "release"), ("確定仕様化", "spec"), ("最後の検査", "final"), ("もう一度", "again")]), docs


def test_close_plan_of_2026_11_12_is_refused_with_reasons():
    mode, docs = _close_plan_2026_11_12()
    # 範囲 base は語彙に無いので、語彙の検査で先に断られる
    errs = _errors(mode, docs)
    assert any("base" in e for e in errs)
    docs = copy.deepcopy(docs)
    docs["final"]["do"].pop()
    errs = _errors(mode, docs)
    assert any(e.startswith("I8a: 最後の検査/cross-review") for e in errs)
    assert any(e.startswith("I10: 範囲 sprint_diff の対象 code のレビューが 2 つ") for e in errs)


def test_order_rules_are_checked_one_by_one():
    def st(i: str, kind: str, rng: str, subject: str | None = None) -> stages.StepDef:
        return stages.StepDef(i, "工程", "ndf.plan", kind, rng, subject)

    assert stages.check_order([st("t", "test", "sprint_diff"), st("r", "release", "sprint_diff"), st("v", "verify", "released")]) == []
    assert stages.check_order([st("t", "test", "lane")])[0].startswith("I9:")
    assert stages.check_order([st("v", "verify", "sprint_diff"), st("r", "release", "sprint_diff")])[0].startswith("I9:")
    assert stages.check_order([st("v", "verify", "released")])[0].startswith("I8b:")
    assert stages.check_order([st("r", "release", "sprint_diff"), st("t", "test", "sprint_diff")])[0].startswith("I8a:")
    # 範囲が違えば、リリースの後のテストは断らない
    assert stages.check_order([st("r", "release", "sprint_diff"), st("t", "test", "issue_diff")]) == []
    assert stages.check_order([st("a", "test", "sprint_diff"), st("b", "test", "sprint_diff")])[0].startswith("I10:")
    # コードのレビューとドキュメントのレビューは範囲が同じでも通る
    assert stages.check_order([st("a", "review", "sprint_diff", "code"), st("b", "review", "sprint_diff", "docs")]) == []


def test_context_schema_holds_the_closed_vocabulary():
    keys = set(contracts.load_json(definition.CONTEXT)["properties"])
    assert keys == {"issues", "mode", "pace", "worktree", "branch", "design_pr", "pr", "delivery", "version"}
    assert set(contracts.load_json(definition.CONTEXT)["properties"]["mode"]["enum"]) == set(definition.modes())


def test_snapshot_round_trips_through_json():
    snap = definition.load_definition("documentation")
    assert stages.snapshot_from(stages.snapshot_dict(snap)) == snap


def test_gate_stage_with_a_condition_is_refused():
    gated = _stage_doc("g", "行", [_step("gate", "gate", "none", gate="design")], when=["code_in_diff"])
    errs = _errors(_mode_doc([("行", "g")]), {"g": gated})
    assert any("承認ゲート" in e and "行" in e for e in errs)
