"""承認の回答を経た欠けの失敗理由（`progress/run.py` の `_step_gap_detail` と `_finish_stage`。#4513）。

ステップの出力の規約に合わない失敗と、工程の出力の規約に合わない失敗の理由の文を、作り直しの前の振る舞いのまま固定する。
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

from ndf.contracts import result
from ndf.progress import definition, run, stages

WAITED = "ステップ release が承認ゲートで止まった: 作業 release の worker が判断を求めた: バージョンを返せない"
DELIVERY = {"target": "t", "kind": "auto", "trigger": "cd", "channel": "production", "versioned": True}


def _deploy():
    return definition.load_definition("standard").stage("デプロイ")


def _release_step():
    return next(st for st in _deploy().steps if st.id == "release")


def _report(outputs=None, waited=None):
    metrics = {} if waited is None else {"waited": waited}
    return result.result("plan", result.OK, "終わった", metrics=metrics, outputs=outputs)


def _errs(step, report):
    return definition.validate_io(step.output_ref, report)


# --- _step_gap_detail ---------------------------------------------------------------------------


def test_a_step_answered_without_a_required_output_names_it_with_the_waited_summary():
    step = _release_step()
    rep = _report({"delivery": DELIVERY}, WAITED)
    errs = _errs(step, rep)
    assert errs
    assert run._step_gap_detail(step, rep, errs) == definition.answered_gap_detail(["product_version"], WAITED)


def test_a_step_not_answered_keeps_the_plain_contract_message():
    step = _release_step()
    rep = _report({"delivery": DELIVERY})
    errs = _errs(step, rep)
    assert run._step_gap_detail(step, rep, errs) == f"ステップ release の出力が規約に合わない: {'; '.join(errs)}"


def test_an_empty_waited_summary_still_counts_as_answered():
    step = _release_step()
    rep = _report({}, "")
    got = run._step_gap_detail(step, rep, _errs(step, rep))
    assert got == definition.answered_gap_detail(["product_version"], "")
    assert "（要約なし）" in got


def test_a_waited_that_is_not_a_string_is_not_answered():
    step = _release_step()
    rep = {**_report({}), "metrics": {"waited": 3}}
    assert run._step_gap_detail(step, rep, ["x"]) == "ステップ release の出力が規約に合わない: x"


def test_outputs_that_are_not_an_object_count_every_required_output_as_missing():
    step = _release_step()
    rep = {**_report(None, WAITED), "outputs": ["product_version"]}
    assert run._step_gap_detail(step, rep, ["x"]) == definition.answered_gap_detail(["product_version"], WAITED)


def test_an_answered_step_with_every_required_output_keeps_the_plain_message():
    # 欠けた名前が無ければ、承認の回答を経ていても規約の誤りの文のまま
    step = _release_step()
    rep = _report({"product_version": "1.2.3"}, WAITED)
    assert run._step_gap_detail(step, rep, ["a", "b"]) == "ステップ release の出力が規約に合わない: a; b"


def test_an_answered_step_without_an_output_contract_keeps_the_plain_message():
    step = dataclasses.replace(_release_step(), output_ref=None)
    rep = _report({}, WAITED)
    assert run._step_gap_detail(step, rep, ["a"]) == "ステップ release の出力が規約に合わない: a"


def test_step_draft_fails_with_the_answered_gap_detail():
    s = _deploy()
    step = _release_step()
    rep = _report({"delivery": DELIVERY}, WAITED)
    res = SimpleNamespace(status=result.OK, report=rep, cause=None, warnings=())
    prog = SimpleNamespace(generation=0)
    (draft,) = run._step_draft(SimpleNamespace(rev=7), s, step, prog, res)
    assert draft.kind == "failed" and draft.row_key == "デプロイ" and draft.rev == 7
    assert draft.payload["step"] == "release"
    assert draft.payload["cause"]["kind"] == "bad_report"
    assert draft.payload["cause"]["detail"] == definition.answered_gap_detail(["product_version"], WAITED)


# --- _finish_stage --------------------------------------------------------------------------------


def _finish(monkeypatch, done):
    s = _deploy()
    monkeypatch.setattr(run, "step_progress", lambda v, name: SimpleNamespace(done=done))
    written: list = []
    d = SimpleNamespace(write=lambda *drafts: written.extend(drafts), warnings=[])
    run._finish_stage(d, SimpleNamespace(rev=5), s)
    return s, written, d


def _done(release_report, gate_waited=None):
    """デプロイの 4 ステップの結果。`release` の結果だけを差し替える。"""
    base = {st.id: _report({}) for st in _deploy().steps}
    base["release-mvv"] = _report({"mvv_gate": {"gate": "release", "skip": False, "verdict": None, "why": ["x"]}})
    if gate_waited is not None:
        base["release-gate"] = _report({}, gate_waited)
    base["release"] = release_report
    return base


def _stage_errs(s, done):
    out, _ = run._stage_output(s, SimpleNamespace(done=done))
    return definition.check_stage_output(s, out)


def test_a_stage_answered_without_its_outputs_names_the_missing_keys(monkeypatch):
    done = _done(_report({"delivery": DELIVERY}, WAITED))
    s, written, _ = _finish(monkeypatch, done)
    errs, gaps = _stage_errs(s, done)
    assert errs and [k for k, _ in gaps] == ["product_version"]
    (draft,) = written
    assert draft.kind == "failed" and draft.row_key == "デプロイ" and draft.rev == 5 and draft.payload["step"] is None
    assert draft.payload["cause"]["kind"] == "bad_report"
    assert draft.payload["cause"]["detail"] == definition.answered_gap_detail(["product_version"], WAITED)


def test_a_stage_takes_the_first_waited_summary_in_step_order(monkeypatch):
    done = _done(_report({"delivery": DELIVERY}, WAITED), gate_waited="先のステップの要約")
    _, written, _ = _finish(monkeypatch, done)
    (draft,) = written
    assert draft.payload["cause"]["detail"] == definition.answered_gap_detail(["product_version"], "先のステップの要約")


def test_a_stage_not_answered_keeps_the_plain_contract_message(monkeypatch):
    done = _done(_report({"delivery": DELIVERY}))
    s, written, _ = _finish(monkeypatch, done)
    errs, _ = _stage_errs(s, done)
    (draft,) = written
    assert draft.payload["cause"]["detail"] == f"工程 デプロイ の出力が規約に合わない: {'; '.join(errs)}"


def test_a_stage_answered_with_every_key_but_a_contract_error_keeps_the_plain_message(monkeypatch):
    # 欠けた文脈のキーが無ければ、承認の回答を経ていても規約の誤りの文のまま
    values = {"delivery": DELIVERY, "product_version": "1.2.3"}
    done = _done(_report(values, WAITED))
    done["release"] = {**done["release"], "status": "どれでもない"}
    s, written, _ = _finish(monkeypatch, done)
    errs, gaps = _stage_errs(s, done)
    assert errs and gaps == []
    (draft,) = written
    assert draft.payload["cause"]["detail"] == f"工程 デプロイ の出力が規約に合わない: {'; '.join(errs)}"


def test_a_stage_with_every_output_writes_the_outputs_and_finishes(monkeypatch):
    values = {"delivery": DELIVERY, "product_version": "1.2.3"}
    s, written, d = _finish(monkeypatch, _done(_report(values, WAITED)))
    assert [w.kind for w in written] == [stages.OUTPUTS_EVENT, "finished"]
    assert written[0].payload["outputs"]["product_version"] == "1.2.3"
    assert written[1].payload == {"steps": len(s.steps)} and written[1].reason == f"デプロイ の {len(s.steps)} ステップが終わった"
    assert d.warnings == []
