"""工程の定義の読み込みと検査（lib/workflow_def.py・scripts/contracts_lib/workflow_defs.py。#3092 の I7〜I11・決定 5・8）。"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import workflow_def as wd  # noqa: E402

REPO = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("workflow_defs_under_test", REPO / "scripts/contracts_lib/workflow_defs.py")
wdefs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wdefs)


def A(stage, aid, kind, rng, subject=None):
    return wd.ActivityDef(id=aid, stage=stage, call="ndf.plan", kind=kind, range=rng, subject=subject)


@pytest.fixture
def defs(tmp_path):
    """工程の定義の写し（壊して検査を確かめる）。"""
    dst = tmp_path / "workflow"
    shutil.copytree(wd.WORKFLOW_DIR, dst)
    return dst


def _edit(path: Path, fn):
    doc = json.loads(path.read_text())
    fn(doc)
    path.write_text(json.dumps(doc, ensure_ascii=False))


# --- 受け入れ条件 1・22・I11: 5 つのモードの工程が工程表の列と同じ ---


def test_five_modes_match_the_stage_table():
    text = (REPO / wdefs.SKILL_MD).read_text()
    columns = wdefs.stage_columns(text)
    snaps = {m: wd.load_mode_definition(m) for m in wd.MODES}
    assert wdefs.check_table_match(snaps, columns) == []
    assert snaps["standard"].stage_names[-3:] == ["振り返り", "組み換え", "棚卸し"]
    assert "組み換え" not in snaps["light"].stage_names
    assert wdefs.check_workflow_defs(REPO) == []


def test_table_match_catches_missing_reordered_and_extra_stage():
    snaps = {"standard": wd.load_mode_definition("standard")}
    names = snaps["standard"].stage_names
    assert any("定義に無い" in v["detail"] for v in wdefs.check_table_match(snaps, {"standard": names + ["新しい工程"]}))
    assert any("工程表に無い" in v["detail"] for v in wdefs.check_table_match(snaps, {"standard": names[1:]}))
    swapped = names[:]
    swapped[0], swapped[1] = swapped[1], swapped[0]
    assert any("順" in v["detail"] for v in wdefs.check_table_match(snaps, {"standard": swapped}))


def test_table_columns_treat_conditional_cells_as_present():
    text = (REPO / wdefs.SKILL_MD).read_text()
    cols = wdefs.stage_columns(text)
    assert "作業場所の用意" in cols["light"]  # 「（…なら不要）」の付いた条件付きの行
    assert "詳細設計" not in cols["light"]


# --- 受け入れ条件 8・I7: 規約の参照 ---


def test_missing_contract_reference_is_refused(defs):
    _edit(defs / "stages/review.json", lambda d: d["do"][0]["review"].pop("output"))
    with pytest.raises(wd.WorkflowDefError) as e:
        wd.load_mode_definition("standard", defs)
    assert any("output の規約の参照が無い" in x for x in e.value.errors)


def test_missing_contract_file_is_refused(defs):
    (defs / "schemas/answer.schema.json").unlink()
    with pytest.raises(wd.WorkflowDefError) as e:
        wd.load_mode_definition("standard", defs)
    assert any("answer.schema.json が無い" in x for x in e.value.errors)


def test_output_not_matching_contract_is_reported():
    snap = wd.load_mode_definition("standard")
    ref = snap.stage("組み換え").activities[0].output_ref
    good = {
        "tool": "reshuffle",
        "status": "ok",
        "summary": "変更なし",
        "decision": "unchanged",
        "lane_changes": [],
        "requests": [],
        "refused": [],
    }
    assert wd.validate_io(snap, ref, good) == []
    assert wd.validate_io(snap, ref, {"tool": "reshuffle", "status": "ok", "summary": "x"})
    assert wd.validate_io(snap, ref, {**good, "status": "done"})


# --- 決定 5: 語彙と要素 ---


def test_vocabulary_refuses_if_unknown_words_and_review_without_subject():
    doc = {
        "do": [
            {"a": {"call": "ndf.plan", "if": "${ true }", "metadata": {"ndf": {"kind": "review", "range": "sprint_diff"}}}},
            {"b": {"call": "ndf.plan", "metadata": {"ndf": {"kind": "work", "range": "none", "subject": "code"}}}},
            {"c": {"call": "ndf.plan", "metadata": {"ndf": {"kind": "fly", "range": "none"}}}},
            {"d": {"set": {"x": 1}}},
        ]
    }
    errs = wd.check_vocabulary(doc)
    assert any("if は使わない" in e for e in errs)
    assert any("subject が無い" in e for e in errs)
    assert any("subject は書かない" in e for e in errs)
    assert any("知らない値 fly" in e for e in errs)
    assert any("5 種類" in e for e in errs)


def test_vocabulary_accepts_the_five_kinds():
    doc = {
        "do": [
            {"a": {"call": "ndf.plan", "metadata": {"ndf": {"kind": "review", "range": "sprint_diff", "subject": "code"}}}},
            {"b": {"listen": {"to": {"one": {"with": {"type": "x"}}}}, "metadata": {"ndf": {"kind": "gate", "range": "none"}}}},
            {"c": {"do": [{"d": {"run": {"workflow": {"namespace": "n", "name": "x", "version": "1.0.0"}}}}]}},
        ]
    }
    assert wd.check_vocabulary(doc) == []


def test_borrowed_schema_refuses_unknown_shape():
    schema = wd.dsl_body(json.loads((wd.WORKFLOW_DIR / wd.DSL_SCHEMA).read_text()))
    assert wd.check_dsl({"document": {"dsl": "1.0.3", "namespace": "ndf", "name": "x", "version": "1.0.0"}, "do": []}, schema) == []
    assert wd.check_dsl({"document": {"dsl": "1.0.3"}, "do": []}, schema)


def test_borrowed_schema_hash_is_checked(tmp_path):
    root = tmp_path / "repo"
    for rel in (wdefs.WORKFLOW, "plugins/ndf/skills/development-workflow", "plugins/ndf/scripts/lib"):
        shutil.copytree(REPO / rel, root / rel)
    p = root / wdefs.WORKFLOW / wd.DSL_SCHEMA
    _edit(p, lambda d: d.update(description="書き換えた"))
    assert any("sha256" in v["detail"] for v in wdefs.check_workflow_defs(root))


# --- 受け入れ条件 12・I8a・I8b・I9・I10: 順序の決まり ---


def test_five_modes_pass_the_order_rules():
    for m in wd.MODES:
        assert wd.check_order(wd.load_mode_definition(m).activities()) == []


def test_close_plan_of_2026_11_12_is_refused():
    """まとめのプラン（最後の検査が本番のリリースの後。#3126）を定義に写すと断られる。"""
    acts = [
        A("実装レビュー", "review", "review", "sprint_diff", "code"),
        A("デプロイ", "release", "release", "sprint_diff"),
        A("まとめ", "last-check", "review", "sprint_diff", "code"),  # 本番の後・同じ範囲の 2 度目
        A("まとめ", "base-check", "review", "base_branch_all", "code"),  # 範囲がベースブランチ
    ]
    errs = wd.check_order(acts)
    assert any("I8a" in e and "last-check" in e for e in errs)
    assert any("I10" in e and "last-check" in e for e in errs)
    assert any("I9" in e and "base-check" in e for e in errs)


def test_order_rules_test_twice_and_verify_before_release():
    assert any("I10" in e for e in wd.check_order([A("x", "t1", "test", "sprint_diff"), A("y", "t2", "test", "sprint_diff")]))
    assert any("I8b" in e for e in wd.check_order([A("x", "v", "verify", "released"), A("y", "r", "release", "sprint_diff")]))
    assert any("I8b" in e for e in wd.check_order([A("x", "v", "verify", "released")]))
    assert any("I9" in e for e in wd.check_order([A("y", "r", "release", "sprint_diff"), A("x", "v", "verify", "sprint_diff")]))


def test_code_and_docs_review_on_same_range_pass():
    acts = [
        A("a", "r1", "review", "sprint_diff", "code"),
        A("b", "r2", "review", "sprint_diff", "docs"),
        A("c", "rel", "release", "sprint_diff"),
    ]
    assert wd.check_order(acts) == []


def test_release_last_passes():
    assert wd.check_order([A("a", "t", "test", "sprint_diff"), A("b", "rel", "release", "sprint_diff")]) == []


# --- 受け入れ条件 3・決定 8: 有効と無効の 3 つの順位 ---

FACTS = {w: True for w in wd.VOCABULARY["when"]}


def test_enable_resolution_three_ranks():
    snap = wd.load_mode_definition("standard")
    facts = {**FACTS, "ci_declared": False}
    r = wd.resolve_enabled(snap, facts, {}, {})
    assert r["構造改善"] == wd.Enablement(False, "ci_declared", "definition")
    r = wd.resolve_enabled(snap, facts, {"構造改善": {"enabled": True}}, {})
    assert r["構造改善"].enabled and r["構造改善"].source == "project"
    r = wd.resolve_enabled(snap, FACTS, {"構造改善": {"enabled": False, "reason": "停止中"}}, {})
    assert r["構造改善"] == wd.Enablement(False, "停止中", "project")
    r = wd.resolve_enabled(snap, FACTS, {"構造改善": {"enabled": False, "reason": "停止中"}}, {"構造改善": None})
    assert r["構造改善"].enabled and r["構造改善"].source == "sprint"
    r = wd.resolve_enabled(snap, FACTS, {}, {"確定仕様化": "文書だけ"})
    assert r["確定仕様化"] == wd.Enablement(False, "文書だけ", "sprint")


def test_reshuffle_follows_retro():
    snap = wd.load_mode_definition("standard")
    r = wd.resolve_enabled(snap, FACTS, {}, {"振り返り": "要らない"})
    assert not r["組み換え"].enabled and r["組み換え"].reason == "retro_enabled"
    op = wd.load_mode_definition("operation")
    r = wd.resolve_enabled(op, {**FACTS, "retro_enabled": False}, {}, {})
    assert not r["振り返り"].enabled and not r["組み換え"].enabled


def test_missing_fact_is_an_error():
    with pytest.raises(wd.WorkflowDefError):
        wd.resolve_enabled(wd.load_mode_definition("standard"), {}, {}, {})


def test_snapshot_roundtrip():
    snap = wd.load_mode_definition("documentation")
    back = wd.Snapshot.from_dict(json.loads(json.dumps(snap.to_dict())))
    assert back == snap


def test_mode_heights_from_the_single_source(tmp_path):
    assert wd.mode_heights() == {"light": 1, "operation": 2, "legacy-refactor": 3, "standard": 4, "documentation": 5}
    bad = tmp_path / "w.sh"
    bad.write_text("WF_MODE_HEIGHT=$'light\\t1'\n")
    with pytest.raises(wd.WorkflowDefError):
        wd.mode_heights(bad)


def test_unknown_mode_and_broken_json(defs):
    with pytest.raises(wd.WorkflowDefError):
        wd.load_mode_definition("heavy")
    (defs / "stages/plan.json").write_text("{")
    with pytest.raises(wd.WorkflowDefError) as e:
        wd.load_mode_definition("standard", defs)
    assert any("JSON の誤り" in x for x in e.value.errors)
