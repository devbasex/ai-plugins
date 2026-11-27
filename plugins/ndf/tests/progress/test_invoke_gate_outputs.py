"""判断を求めたステップの出力を承認の後に引き継ぎ、値の足りない承認を失敗にする（`progress/invoke.py` の `_no_route`・
`core/plan/runner.py` の `final_result`。#4513 の決定 4、受け入れ条件 6・9）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.contracts import result
from ndf.core import route
from ndf.core.plan import files as plan_files
from ndf.core.plan import records as plan_records
from ndf.core.plan import runner as plan_runner
from ndf.progress import definition, invoke

STEPS = [{"id": "a", "run": ["true"]}, {"id": "b", "run": ["true"]}]
WORKER = "作業 release の worker が判断を求めた: semver の形で返せない"
DELIVERY = {"target": "本番", "kind": "manual", "trigger": "手で出す", "versioned": False}


def _never(*_a):
    pytest.fail("プランを打たない")


def _gate_at(tmp_path: Path, monkeypatch, last: str, outputs=None, missing=None, earlier=None):
    """プランの回がステップ `last` の判断の求めで終わった保存先。`earlier` は先のステップ a が返した値。"""
    t = route.Template("release", "release", "試験のテンプレート", (), {"name": "release", "steps": STEPS})
    monkeypatch.setattr(route, "templates", lambda: {"release": t})
    work = tmp_path / "w"
    work.mkdir()
    run = invoke.StageRun(invoke.PLAN, "release", {}, ("delivery", "product_version"), "none", "release")
    argv = invoke.plan_argv(run, work, None, tmp_path)
    plan = Path(argv[argv.index("next") + 1])
    inv = plan_runner.prepare(tmp_path / "root", plan, None, str(work), work, plan_runner.RunOptions({}))
    plan_runner._record_run(inv)
    if earlier:
        plan_records._add_outputs(work, "a", "ok", earlier)
    plan_records._add_outputs(work, last, "gate", outputs)
    plan_records._keep_step_missing(work, last, "gate", missing)
    final = {"status": "gate", "kind": None, "detail": f"ステップ {last} が承認ゲートで止まった: {WORKER}", "last": last, "count": 2}
    out = plan_runner.final_result(inv, final, "SUCCESS")
    plan_files.write_json(inv.result_path, out)
    return run, work, out


def test_the_gate_result_of_the_plan_carries_the_missing_names_of_the_step_it_stopped_at(tmp_path, monkeypatch):
    _, _, out = _gate_at(tmp_path, monkeypatch, "b", {"delivery": DELIVERY}, ["product_version"])
    assert out["metrics"]["missing_outputs"] == ["product_version"] and out["outputs"] == {"delivery": DELIVERY}
    assert result.errors(out) == []


def test_an_approval_of_a_gate_with_every_value_ends_the_stage(tmp_path, monkeypatch):
    # #4513 の受け入れ条件 9: 出力に product_version がそろった判断の求めへの approved で、今と同じく工程が終わる
    values = {"delivery": DELIVERY, "product_version": "1.2.3"}
    run, work, out = _gate_at(tmp_path, monkeypatch, "b", values)
    assert "missing_outputs" not in out["metrics"]
    got = invoke.invoke(run, tmp_path, work, "approved", runner=_never)
    assert got.status == "ok" and got.report["outputs"] == values and WORKER in got.report["metrics"]["waited"]


@pytest.mark.parametrize("last", ["a", "b"])
def test_an_approval_of_a_gate_without_its_value_fails_even_with_a_continuation_or_an_earlier_value(tmp_path, monkeypatch, last):
    # 判断を求めたステップ自身の欠けは、続きのステップがあっても（a）、先のステップの同じキーがあっても（b）隠れない
    earlier = {"product_version": "先の値"} if last == "b" else None
    run, work, _ = _gate_at(tmp_path, monkeypatch, last, {"delivery": DELIVERY}, ["product_version"], earlier)
    got = invoke.invoke(run, tmp_path, work, "approved", runner=_never)
    # #4513 の受け入れ条件 6: 理由に足りない値の名前と、worker が判断を求めた理由の要約と、次の操作が入る
    detail = got.cause["detail"]
    assert got.status == "stopped" and got.cause["kind"] == "bad_report"
    assert "足りない出力: product_version" in detail and WORKER in detail and "ndf progress retry" in detail


def test_a_rejection_of_a_gate_without_its_value_is_still_a_rejection(tmp_path, monkeypatch):
    run, work, _ = _gate_at(tmp_path, monkeypatch, "b", None, ["delivery", "product_version"])
    got = invoke.invoke(run, tmp_path, work, "rejected", runner=_never)
    assert got.status == "stopped" and got.cause["kind"] == "refused"


def test_a_gate_whose_delivery_does_not_fit_the_context_reaches_the_answer(tmp_path, monkeypatch):
    # 文脈の形に合わない delivery は worker の確かめで outputs に載らず missing に入るので、gate のまま判断の求めが届く
    _, work, out = _gate_at(tmp_path, monkeypatch, "b", {"product_version": "1.2.3"}, ["delivery"])
    assert invoke.finished(invoke.StageRun(invoke.PLAN, "release", {}, (), "none", "release"), out).status == "gate"
    assert json.loads((work / plan_records.STEP_MISSING_FILE).read_text()) == {"b": ["delivery"]}


def test_answered_gap_detail_names_every_missing_value_the_summary_and_the_next_step():
    got = definition.answered_gap_detail(["delivery", "product_version"], "止まった")
    assert got.startswith("足りない出力: delivery, product_version。判断を求めたステップの要約: 止まった。")
    assert "ndf progress retry" in got
