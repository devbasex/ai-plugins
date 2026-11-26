"""工程の定義（contracts/workflow/）の読み込みと検査（受け入れ条件 1・3・8・12。#1516・#2935・SF5）。"""

from __future__ import annotations

import copy
import dataclasses

import pytest

from ndf import contracts
from ndf.progress import definition, invoke, model, run, stages

import scenes

E = "file:///workflow/stage-io.schema.json#/$defs/"
# .ndf/project.json の delivery の 1 行の形
DELIVERY = {"target": "t", "kind": "auto", "trigger": "cd", "channel": "production", "versioned": True}
MVV_GATE = {"gate": "release", "skip": False, "verdict": None, "why": ["進め方 normal は承認ゲートを MVV 判定で省かない"]}


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


def _all_modes_pass_every_check(mode):
    snap = definition.load_definition(mode)
    assert snap.stages and len(snap.sha256) == 64
    assert snap.stages[-1].name == "棚卸し"
    # 工程とステップのすべてに入出力の規約があり、参照の指す先がある（受け入れ条件 8）
    for stage in snap.stages:
        assert definition.ref_target(stage.input_ref) is not None
        assert definition.ref_target(stage.output_ref) is not None
        assert stage.steps and all(definition.ref_target(st.output_ref) is not None for st in stage.steps)


def _refactor_stage_takes_the_issues_to_read_their_implementation_plans(mode):
    # #3879 構成要素 2: 実装計画のスプリントの終わりの候補を読むため、工程「リファクタリング」は課題の一覧を受ける
    assert definition.load_definition(mode).stage("リファクタリング").inputs == ("worktree", "branch", "issues")


def test_standard_rows_run_from_requirements_to_backlog_and_hold_the_closing_stages():
    names = [s.name for s in definition.load_definition("standard").stages]
    assert names[0] == "要求と受け入れ条件" and names[-1] == "棚卸し"
    assert len(names) == 18
    # まとめの工程（確定仕様化・リリース後テスト・振り返り・棚卸し）も行にある
    closing = [s.name for s in definition.load_definition("standard").stages if s.phase == "close"]
    assert closing == ["確定仕様化", "リリース後テスト", "振り返り", "棚卸し"]


def test_modes_are_ordered_by_height():
    heights = definition.mode_heights()
    assert sorted(heights, key=heights.__getitem__) == [
        "light",
        "operation",
        "legacy-refactor",
        "standard",
        "documentation",
        "release-bump",
    ]


def test_gate_steps_name_which_gate_they_are():
    # 承認ゲートの工程は承認ゲートを 1 つと数え、before はゲートの直前のステップを返す（#4446 の Task 1）
    for name, gate in (("設計", "design"), ("デプロイ", "release")):
        stage = definition.load_definition("standard").stage(name)
        ids = [st.id for st in stage.steps]
        at = next(st.id for st in stage.steps if st.kind == "gate")
        assert stage.gates == (gate,) and stage.before(at) == ids[ids.index(at) - 1] and stage.before(ids[0]) is None
    # #1516: 設計の承認ゲートと本番の承認ゲートを書き分ける語が、ステップの定義から引ける
    snap = definition.load_definition("standard")
    gates = {st.gate: model.gate_label(st.gate) for st in snap.steps() if st.kind == "gate"}
    assert set(gates) == {"design", "release"}
    assert gates["design"] != gates["release"]
    assert snap.stage("設計").steps[[st.kind for st in snap.stage("設計").steps].index("gate")].gate == "design"


def test_design_hands_the_review_result_to_the_gate_without_a_final_judge():
    # SF5: 設計の工程はレビューの結果をそのまま設計承認ゲートへ渡し、最後の判定を置かない。
    # #2935: 承認は承認ゲートのステップの回答（gate-answer）で受け、ラベルの有無をステップの入力にしない
    # #3745・#4446: レビューと承認ゲートの間に置くのは MVV 判定（ndf records mvv gate）と読み手役（ndf document-rebuild gate）だけ
    steps = definition.load_definition("standard").stage("設計").steps
    kinds = [st.kind for st in steps]
    between = steps[kinds.index("review") + 1 : kinds.index("gate")]
    assert [st.args["argv"][1:4] for st in between] == [["records", "mvv", "gate"], ["document-rebuild", "gate", "--gate"]]
    assert kinds[kinds.index("gate") + 1 :] == ["merge"]
    gate = steps[kinds.index("gate")]
    assert gate.output_ref.endswith("#/$defs/gate-answer") and not gate.args


@pytest.mark.parametrize("mode", ["standard", "light"])
def test_reading_step_sits_between_the_mvv_judge_and_the_gate_and_is_not_counted_as_a_gate(mode):
    # #4446 の決定 2: design-reading は design-mvv と design-gate の間、release-reading は release-mvv と release-gate の間。
    # 承認ゲートは 1 つと数え、MVV 判定は読み手役のステップを飛ばして探す。設計承認ゲートの無いモードには読み手役も無い
    snap = definition.load_definition(mode)
    for name, gate, mvv in (("設計", "design", "design-mvv"), ("デプロイ", "release", "release-mvv")):
        if name not in [s.name for s in snap.stages]:
            continue
        stage = snap.stage(name)
        ids = [st.id for st in stage.steps]
        if f"{gate}-gate" not in ids:
            assert stage.reading_for(gate) is None and all(st.kind != "reading" for st in stage.steps)
            continue
        assert ids[ids.index(mvv) : ids.index(f"{gate}-gate") + 1] == [mvv, f"{gate}-reading", f"{gate}-gate"]
        assert stage.gates == (gate,) and stage.mvv_before(f"{gate}-gate") == mvv
        assert stage.reading_for(gate).id == f"{gate}-reading" and stage.reading_for(gate).args["argv"][-1] == gate
    # 読み手役のステップの無い古い定義でも、MVV 判定は承認ゲートの直前のステップ
    old = dataclasses.replace(snap.stage("デプロイ"), steps=tuple(st for st in snap.stage("デプロイ").steps if st.kind != "reading"))
    assert old.mvv_before("release-gate") == "release-mvv" and old.reading_for("release") is None
    # 読み手役のステップを承認ゲートの後ろへ入れ替えると、定義の検査で落ちる
    swapped = _stage_doc("x", "行", [_step("g", "gate", "none", gate="release"), _step("r", "reading", "none", gate="release")])
    assert any("I11:" in e for e in _errors(_mode_doc([("行", "x")]), {"x": swapped}))
    # #4446 の決定 9: 版上げのスプリントは資料の提示を読み手役と承認ゲートの間に置き、承認されなくても記録のステップへ進む
    bump = definition.load_definition("release-bump").stage("版上げ")
    assert [st.id for st in bump.steps] == [
        "bump-assemble", "release-mvv", "release-reading", "bump-present", "release-gate", "release", "bump-record"
    ]  # fmt: skip
    assert bump.mvv_before("release-gate") == "release-mvv" and bump.gates == ("release",)
    assert next(st for st in bump.steps if st.id == "release-gate").on_reject == "bump-record"
    back = _step("g", "gate", "none", gate="release")
    back["g"]["metadata"]["ndf"]["on_reject"] = "r"
    wrong = _stage_doc("x", "行", [_step("r", "record", "none"), back])
    assert any("I12:" in e for e in _errors(_mode_doc([("行", "x")]), {"x": wrong}))


def test_refactoring_suspension_is_not_a_code_constant():
    # 受け入れ条件 3: リファクタリングの停止はコードの定数でなく設定で表す
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
        definition.validate_stage_output(
            stage, {"steps": [], "summary": "出した", "outputs": {"delivery": DELIVERY, "product_version": "1.2.3", "mvv_gate": MVV_GATE}}
        )
        == []
    )
    bad = {"steps": [], "summary": "出した", "outputs": {"delivery": {**DELIVERY, "channel": "prod"}, "product_version": "1.2.3"}}
    assert any("delivery/channel" in e for e in definition.validate_stage_output(stage, bad))
    errs = definition.validate_stage_output(stage, {"steps": [], "outputs": {"product_version": 3}})
    assert any("summary" in e for e in errs)
    assert any("product_version" in e for e in errs)
    assert any("delivery" in e for e in errs)


def test_input_needs_the_declared_context_keys():
    # #3959 受け入れ条件 9: 工程の入力は、プランが組むのと同じ平らな object（文脈のキーを並べたもの）
    stage = definition.load_definition("standard").stage("作業場所の用意")
    assert definition.validate_stage_input(stage, {"issues": [1]}) == []
    assert definition.validate_stage_input(stage, {}) == ["文脈のキー issues が無い"]


DESIGN_INPUT = {"issues": [1], "worktree": "/work/a", "mode": "standard", "pace": "normal", "mvv_ref": "a" * 64}


def test_a_nested_context_is_both_a_schema_error_and_missing_keys():
    stage = definition.load_definition("standard").stage("設計")
    got = definition.stage_input_errors(stage, {"context": DESIGN_INPUT})
    assert got.schema and got.missing
    assert definition.validate_stage_input(stage, {"context": DESIGN_INPUT}) == got.schema + got.missing


def test_the_flat_design_input_passes_and_a_key_not_declared_by_the_stage_is_refused():
    stage = definition.load_definition("standard").stage("設計")
    assert definition.stage_input_errors(stage, DESIGN_INPUT) == ([], [])
    # 文脈のスキーマにあるキーでも、工程の inputs・optional_inputs に無ければ拒む（resolve_inputs が渡さないキー）
    got = definition.stage_input_errors(stage, {**DESIGN_INPUT, "design_pr": 4})
    assert got.schema == ["工程 設計 の入力の定義に無いキー design_pr"] and got.missing == []


def test_a_non_object_input_is_only_a_schema_error():
    stage = definition.load_definition("standard").stage("設計")
    got = definition.stage_input_errors(stage, [DESIGN_INPUT])
    assert got.schema and got.missing == []


@pytest.mark.parametrize(("name", "mode"), [("design", "standard"), ("deploy", "standard")])
def test_stage_io_reads_the_inputs_of_the_stage_without_the_mode(name, mode):
    loaded = next(s for s in definition.load_definition(mode).stages if s.id == name)
    got = definition.stage_io(name)
    assert (got.name, got.inputs, got.optional_inputs, got.input_ref, got.steps) == (
        loaded.name,
        loaded.inputs,
        loaded.optional_inputs,
        loaded.input_ref,
        (),
    )


@pytest.mark.parametrize("key", ["delivery", "mvv_gate"])
def test_context_source_names_where_to_take_the_value(key):
    source = definition.context_source(key)
    assert isinstance(source, str) and source


@pytest.mark.parametrize("key", ["issues", "存在しないキー"])
def test_context_source_is_none_without_x_source_or_outside_the_schema(key):
    assert definition.context_source(key) is None


def test_missing_key_line_carries_the_source_only_when_the_key_has_one():
    stage = definition.load_definition("standard").stage("デプロイ")
    errs = definition.validate_stage_output(stage, {"steps": [], "summary": "出した", "outputs": {"product_version": "1.2.3"}})
    assert f"outputs: 文脈のキー delivery が無い（取り場所: {definition.context_source('delivery')}）" in errs
    design = definition.load_definition("standard").stage("設計")
    assert "outputs: 文脈のキー design_pr が無い" in definition.validate_stage_output(design, {"steps": [], "summary": "x"})


def test_missing_key_lines_of_an_output_that_is_not_a_dict_still_list_every_key():
    stage = definition.load_definition("standard").stage("デプロイ")
    errs = definition.validate_stage_output(stage, {"steps": [], "summary": "出した", "outputs": [1]})
    assert [e for e in errs if "文脈のキー" in e] == [
        f"outputs: 文脈のキー {k} が無い（取り場所: {definition.context_source(k)}）" for k in stage.outputs
    ]


def test_unknown_metadata_words_and_if_are_refused():
    step = _step("a", "work", "none", speed="fast")
    step["a"]["if"] = "${ .x }"
    doc = _stage_doc("x", "行", [step], inputs=["nope"])
    errs = _errors(_mode_doc([("行", "x")]), {"x": doc})
    assert any("要素の if は使わない" in e for e in errs)
    assert any("speed" in e for e in errs)
    assert any("nope はスプリントの文脈のキーに無い" in e for e in errs)


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


def test_current_order_errors_name_the_step_and_the_ranges():
    # 現状固定（#3879 Task 8）: I9 は差分の範囲の語をすべて挙げ、I8a は後ろのステップとリリースのステップと範囲を挙げる
    def st(i: str, kind: str, rng: str) -> stages.StepDef:
        return stages.StepDef(i, "工程", "ndf.plan", kind, rng)

    i9 = stages.check_order([st("t", "test", "lane")])[0]
    assert i9.startswith("I9: 工程/t の test の範囲 lane が差分（") and i9.endswith("）でない")
    assert all(r in i9 for r in stages.DIFF_RANGES)
    i8a = stages.check_order([st("r", "release", "sprint_diff"), st("t", "test", "sprint_diff")])
    assert i8a == ["I8a: 工程/t の test が、同じ範囲 sprint_diff をリリースする 工程/r より後ろにある"]


def test_current_unknown_range_and_when_words_are_refused():
    # 現状固定（#3879 Task 8）: 範囲（range）と工程の条件（when）に知らない語を書いた工程の定義は、語彙の検査で断られる
    doc = _stage_doc("x", "行", [_step("a", "review", "no_such_range", subject="code")], when=["no_such_word"])
    errs = _errors(_mode_doc([("行", "x")]), {"x": doc})
    assert any("no_such_range" in e for e in errs) and any("no_such_word" in e for e in errs)


def _seam_steps(review_after_deploy: bool) -> list[stages.StepDef]:
    """つなぎ目（unreviewed_diff）のレビューと、スプリントの差分（sprint_diff）をリリースするデプロイの並び。"""
    review = stages.StepDef("code-review", "コードレビュー", "ndf.plan", "review", "unreviewed_diff", "code")
    deploy = stages.StepDef("prod", "デプロイ", "ndf.plan", "release", "sprint_diff")
    return [deploy, review] if review_after_deploy else [review, deploy]


def test_seam_review_before_the_deploy_passes_and_after_it_is_refused_by_i8a():
    # #3879 構成要素 6: unreviewed_diff は sprint_diff の一部で、同じリリースで届く
    assert stages.check_order(_seam_steps(review_after_deploy=False)) == []
    errs = stages.check_order(_seam_steps(review_after_deploy=True))
    assert errs and errs[0].startswith("I8a: コードレビュー/code-review の review が")


def test_optional_inputs_must_be_keys_of_the_sprint_context():
    # #3879 構成要素 6: 誤記した任意の入力は読み込みで断る（resolve_inputs が黙って落とすと全差分を読む方へ倒れる）
    bad = _stage_doc("x", "行", [_step("a", "work", "none")], optional_inputs=["gathred"])
    errs = _errors(_mode_doc([("行", "x")]), {"x": bad})
    assert any("optional_inputs" in e and "gathred" in e for e in errs)
    good = _stage_doc("x", "行", [_step("a", "work", "none")], optional_inputs=["gathered"])
    assert definition.build(_mode_doc([("行", "x")]), {"x": good}).stage("行").optional_inputs == ("gathered",)


def _code_review_reviews_the_seam_after_gathering(mode):
    # #3879 構成要素 2: 実装が gathered を書き、コードレビューはそれを任意の入力に取り、つなぎ目にコードがあるときだけ流す
    snap = definition.load_definition(mode)
    stage = snap.stage("コードレビュー")
    assert stage.optional_inputs == ("gathered",) and stage.when == ("code_in_unreviewed_diff",)
    assert [st.range for st in stage.steps] == ["unreviewed_diff"]
    assert "gathered" in snap.stage("実装").outputs


def test_context_schema_holds_the_closed_vocabulary():
    keys = set(contracts.load_json(definition.CONTEXT)["properties"])
    assert keys == {
        "release_requests",
        "release_plan",
        "material",
        "issues",
        "mode",
        "pace",
        "mvv_ref",
        "mvv_gate",
        "worktree",
        "branch",
        "design_pr",
        "pr",
        "delivery",
        "version",
        "product_version",
        "release_pr",
        "gathered",
    }
    modes = [m for name in ("mvv_mode", "gated_mode") for m in contracts.schema_enum(definition.CONTEXT, f"/$defs/{name}")]
    assert sorted(modes) == sorted(definition.modes())


def test_snapshot_round_trips_through_json():
    snap = definition.load_definition("documentation")
    assert stages.snapshot_from(stages.snapshot_dict(snap)) == snap


def test_gate_stage_with_a_condition_is_refused():
    gated = _stage_doc("g", "行", [_step("gate", "gate", "none", gate="design")], when=["code_in_diff"])
    errs = _errors(_mode_doc([("行", "g")]), {"g": gated})
    assert any("承認ゲート" in e and "行" in e for e in errs)


# --- 承認ゲートの回答の選択肢（現状固定。I-018 の前に run.choices をエントリポイントとして固定する） ---------------------


def _listen_steps(mode: str) -> list[stages.StepDef]:
    return [st for st in definition.load_definition(mode).steps() if st.call in invoke.CALLS and invoke.is_listen(st)]


def _current_listen_steps_offer_the_answer_enum_of_their_output_contract(mode):
    # 現状固定: 承認ゲートのステップの選択肢は出力の規約（gate-answer）の metrics.answer の enum の順
    listen = _listen_steps(mode)
    assert listen
    for st in listen:
        assert run.choices(run.Move(run.Action.GATE, None, st, run.StepProgress())) == ["approved", "rejected", "request_changes"]


def test_current_listen_step_without_output_contract_offers_nothing():
    # standard は両方の承認ゲートの listen のステップを持ち、出力の規約の無い listen のステップは選択肢を出さない
    assert {st.gate for st in _listen_steps("standard")} == {"design", "release"}
    st = dataclasses.replace(_listen_steps("standard")[0], output_ref=None)
    assert run.choices(run.Move(run.Action.GATE, None, st, run.StepProgress())) == []


@pytest.mark.parametrize(
    "ref",
    [
        E + "release-result",  # answer の enum を持たない規約
        E + "no-such-def",  # 指す先が無い参照
        "https://example.invalid/x.json#/a",  # 規約の外の参照
    ],
)
def test_current_listen_step_whose_contract_has_no_answer_enum_offers_nothing(ref):
    st = dataclasses.replace(_listen_steps("standard")[0], output_ref=ref)
    assert run.choices(run.Move(run.Action.GATE, None, st, run.StepProgress())) == []


def test_current_non_listen_step_offers_the_known_routes_of_the_waiting_report():
    # 現状固定: プランの承認ゲートは待っている結果の routes のキーのうち、回答の語彙にあるものをキーの順に返す
    st = next(s for s in definition.load_definition("standard").steps() if s.call in invoke.CALLS and not invoke.is_listen(s))
    routes = {"request_changes": {}, "bogus": {}, "approved": {}}
    prog = run.StepProgress(waiting={"report": {"routes": routes}})
    assert run.choices(run.Move(run.Action.GATE, None, st, prog)) == ["request_changes", "approved"]
    prog = run.StepProgress(waiting={"report": {"routes": ["approved"]}})
    assert run.choices(run.Move(run.Action.GATE, None, st, prog)) == []
    prog = run.StepProgress(waiting={"report": {}})
    assert run.choices(run.Move(run.Action.GATE, None, st, prog)) == []


# --- 現状固定: スキーマの木から性質の enum を探す（I-018。入れ子・配列・候補が複数・enum の無いセクション・空の enum） -----

_find_enum = definition.find_enum


def _current_find_enum_reads_the_own_property_before_the_children():
    tree = {"properties": {"answer": {"enum": ["a"]}, "inner": {"properties": {"answer": {"enum": ["b"]}}}}}
    assert _find_enum(tree, "answer") == ["a"]


def _current_find_enum_walks_nested_objects_and_arrays_in_order_and_takes_the_first():
    tree = {
        "allOf": [
            {"properties": {"other": {"enum": ["x"]}}},
            {"properties": {"metrics": {"properties": {"answer": {"enum": ["first", "second"]}}}}},
        ],
        "$defs": {"later": {"properties": {"answer": {"enum": ["later"]}}}},
    }
    assert _find_enum(tree, "answer") == ["first", "second"]


def _current_find_enum_without_the_property_or_its_enum_is_none():
    assert _find_enum({"properties": {"answer": {"type": "string"}}}, "answer") is None
    assert _find_enum({"properties": {"answer": "not a node"}, "items": [1, "s", None]}, "answer") is None
    assert _find_enum({"properties": ["answer"]}, "answer") is None
    assert _find_enum(None, "answer") is None and _find_enum([], "answer") is None


def _current_find_enum_keeps_an_empty_enum_and_stops_there():
    tree = {"items": [{"properties": {"answer": {"enum": []}}}, {"properties": {"answer": {"enum": ["later"]}}}]}
    assert _find_enum(tree, "answer") == []


def _cleanup_runs_right_after_deploy(mode):
    """#4409 の決定 6: 5 つのモードで、クリーンアップはデプロイの直後にある（スプリントの PR はデプロイでマージされる）。"""
    ids = [s.id for s in definition.load_definition(mode).stages]
    assert ids.index("cleanup") == ids.index("deploy") + 1


@pytest.mark.parametrize(
    scenes.ARGS,
    [
        *[
            scenes.scene(_all_modes_pass_every_check, id=scenes.label("all_modes_pass_every_check", i, (v,)), mode=v)
            for i, v in enumerate(["light", "operation", "legacy-refactor", "standard", "documentation"])
        ],
        *[
            scenes.scene(
                _refactor_stage_takes_the_issues_to_read_their_implementation_plans,
                id=scenes.label("refactor_stage_takes_the_issues_to_read_their_implementation_plans", i, (v,)),
                mode=v,
            )
            for i, v in enumerate(["legacy-refactor", "standard"])
        ],
        *[
            scenes.scene(
                _code_review_reviews_the_seam_after_gathering,
                id=scenes.label("code_review_reviews_the_seam_after_gathering", i, (v,)),
                mode=v,
            )
            for i, v in enumerate(["light", "operation", "legacy-refactor", "standard", "documentation"])
        ],
        *[
            scenes.scene(
                _current_listen_steps_offer_the_answer_enum_of_their_output_contract,
                id=scenes.label("current_listen_steps_offer_the_answer_enum_of_their_output_contract", i, (v,)),
                mode=v,
            )
            for i, v in enumerate(["light", "operation", "legacy-refactor", "standard", "documentation"])
        ],
        *[
            scenes.scene(_cleanup_runs_right_after_deploy, id=scenes.label("cleanup_runs_right_after_deploy", i, (v,)), mode=v)
            for i, v in enumerate(["standard", "light", "operation", "documentation", "legacy-refactor"])
        ],
    ],
)
def test_mode_definition_scenes(request, scene, values):
    """モードごとの工程の定義の場面（検査・リファクタリングの入力・境目のレビュー・承認ゲートの回答・クリーンアップの位置）。"""
    scenes.play(request, scene, values)


@pytest.mark.parametrize(
    scenes.ARGS,
    [
        scenes.scene(
            _current_find_enum_reads_the_own_property_before_the_children, id="current_find_enum_reads_the_own_property_before_the_children"
        ),
        scenes.scene(
            _current_find_enum_walks_nested_objects_and_arrays_in_order_and_takes_the_first,
            id="current_find_enum_walks_nested_objects_and_arrays_in_order_and_takes_the_first",
        ),
        scenes.scene(
            _current_find_enum_without_the_property_or_its_enum_is_none, id="current_find_enum_without_the_property_or_its_enum_is_none"
        ),
        scenes.scene(_current_find_enum_keeps_an_empty_enum_and_stops_there, id="current_find_enum_keeps_an_empty_enum_and_stops_there"),
    ],
)
def test_find_enum_scenes(request, scene, values):
    """出力のコントラクトの木から回答の enum を探す _find_enum の場面。"""
    scenes.play(request, scene, values)
