"""承認の記録（`ndf.progress.design_record`）のテスト（issues/issue-3702-design-detail.md の「承認の記録」）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import git_sandbox
import pytest

from ndf.adapters.git import Git
from ndf.core.plan import units_decide as ud
from ndf.core.plan import units_files as uf
from ndf.progress import design_record as dr
from ndf.progress import model, run_view, stages

LIMITS = ud.Limits(target=400, max=1000)
REVIEWED = {"repo": "o/r", "pr": 12, "head": "a" * 40, "base": "main"}


def _design_units(head: str = "a" * 40, **extra) -> dict:
    return {
        "head": head,
        "branch": "sprint/7",
        "issues": [7, 8],
        "handling": {"7": {"handling": "実装する", "into": None}, "8": {"handling": "実装する", "into": None}},
        "omitted": [8],
        "limits": {"target": 400, "max": 1000},
        "criteria": {"7": 1, "8": 0},
        "units": [{"kind": "unit", "name": "A"}],
        **extra,
    }


def _step(sid: str, kind: str = "record", gate=None, stage: str = "ドキュメントレビュー") -> stages.StepDef:
    return stages.StepDef(id=sid, stage=stage, call="ndf.script", kind=kind, range="none", gate=gate)


def _snap(gated: bool = True) -> stages.Snapshot:
    steps = [_step("design-pr-review", "review"), _step("units-check")]
    if gated:
        steps += [_step("design-gate", stages.GATE_KIND, "design"), _step("design-sync")]
    review = stages.StageDef(id="document-review", name="ドキュメントレビュー", phase="design", steps=tuple(steps))
    impl = stages.StageDef(id="implement", name="実装", phase="implement", steps=(_step("impl", "work", stage="実装"),))
    return stages.Snapshot("standard", "1", "0" * 64, 4, (review, impl))


def _view(*drafts: model.EventDraft, gated: bool = True) -> run_view.View:
    at = datetime(2026, 11, 28, tzinfo=timezone.utc)
    events = [model.Event(seq, at, draft) for seq, draft in enumerate(drafts, 1)]
    return run_view.View("sprint:x:y", 1, _snap(gated), {}, events)


def _answer(answer: str, stage: str = "ドキュメントレビュー", **extra) -> model.EventDraft:
    payload = {"answer": answer, "step": "design-gate", "kind": "listen", "notes": None, **extra}
    return model.EventDraft(run_view.ANSWERED, run_view.CONDUCTOR, stage, payload=payload, rev=1)


# --- required・build・stale -------------------------------------------------------------------


def test_required_only_when_the_document_review_has_the_design_gate():
    assert dr.required(_snap()) is True
    assert dr.required(_snap(gated=False)) is False


def test_build_combines_the_two_step_outputs():
    record = dr.build(REVIEWED, _design_units())

    assert record == {
        "version": 1,
        "review": REVIEWED,
        "sprint_branch": "sprint/7",
        "targets": {"issues": [7, 8], "handling": _design_units()["handling"], "omitted": [8]},
        "limits": {"target": 400, "max": 1000},
        "criteria": {"7": 1, "8": 0},
        "units": [{"kind": "unit", "name": "A"}],
    }


@pytest.mark.parametrize(
    ("reviewed", "units", "word"),
    [
        (None, _design_units(), "reviewed"),
        (REVIEWED, None, "design_units"),
        (REVIEWED, _design_units(head="b" * 40), "design_units.head"),
    ],
)
def test_build_refuses_missing_or_disagreeing_outputs(reviewed, units, word):
    with pytest.raises(model.Refused) as refused:
        dr.build(reviewed, units)
    assert word in refused.value.reason


@pytest.mark.parametrize(
    ("change", "word"),
    [({}, None), ({"head": "b" * 40}, "head"), ({"base": "sprint/y"}, "base"), ({"pr": 13}, "pr"), ({"repo": "o/x"}, "repo")],
)
def test_stale_names_what_differs_from_the_pr_now(change, word):
    record = dr.build(REVIEWED, _design_units())

    found = dr.stale(record, **{**REVIEWED, **change})

    assert [line.split(" ")[0] for line in found] == ([] if word is None else [word])


# --- carried_values -----------------------------------------------------------------------------------


def test_carried_takes_the_two_keys_only_from_their_steps_in_this_stage():
    stage = _snap().stages[0]
    done = {
        "design-pr-review": {"outputs": {"reviewed": REVIEWED, "design_units": "別のステップの値"}},
        "units-check": {"outputs": {"design_units": {"head": "x"}, "reviewed": None}},
    }
    context = {"issues": [7], "reviewed": "前の値", "design_units": "前の値"}

    assert stages.carried_values(stage, done) == {"reviewed": REVIEWED, "design_units": {"head": "x"}}
    assert stages.carried_inputs(stage, context, done) == {"issues": [7], "reviewed": REVIEWED, "design_units": {"head": "x"}}
    assert stages.carried_inputs(stage, context, {}) == {"issues": [7]}


def test_carried_keeps_a_null_reviewed():
    stage = _snap().stages[0]

    assert stages.carried_values(stage, {"design-pr-review": {"outputs": {"reviewed": None}}}) == {"reviewed": None}


# --- 事象 ---------------------------------------------------------------------------------------


def test_reset_drafts_returns_the_steps_up_to_the_gate_to_unfinished():
    done = [
        model.EventDraft(
            run_view.STEP_EVENT,
            run_view.ACTOR,
            "ドキュメントレビュー",
            payload={"step": step, "status": run_view.FINISHED, "report": {}, "generation": 0},
            rev=1,
        )
        for step in ("design-pr-review", "units-check")
    ]
    v = _view(*done)

    drafts = dr.reset_drafts(v, v.snap.stages[0], "head が変わった")

    assert [(d.payload["step"], d.payload["status"], d.payload["reason"]) for d in drafts] == [
        (step, stages.RESET, "head が変わった") for step in ("design-pr-review", "units-check", "design-gate")
    ]
    assert all((d.kind, d.actor, d.row_key, d.rev) == (run_view.STEP_EVENT, run_view.ACTOR, "ドキュメントレビュー", 1) for d in drafts)


@pytest.mark.parametrize(
    ("drafts", "found"),
    [
        ([_answer("approved", design_target={"n": 1})], {"n": 1}),
        ([_answer("approved", design_target={"n": 1}), _answer("request_changes")], None),
        ([_answer("approved", by=model.MVV_SERVICE)], None),
        ([], None),
    ],
)
def test_approved_of_reads_the_latest_gate_answer(drafts, found):
    assert dr.approved_of(_view(*drafts)) == found


def test_reapproved_of_reads_the_implement_stage_only():
    approved = _answer("approved", stage="実装", design_target={"n": 2})

    assert dr.reapproved_of(_view(approved)) == {"n": 2}
    assert dr.approved_of(_view(approved)) is None
    assert dr.reapproved_of(_view(_answer("approved", design_target={"n": 1}))) is None


# --- mismatches・without・converted -------------------------------------------------------------


def _unit_doc(issue: int, lines: int = 10, **extra) -> dict:
    task = {"id": "T1", "title": "作業", "files": [f"{issue}.py"], "lines": lines, "criteria": [], "after": []}
    return {"version": 1, "issues": [issue], "tasks": [task], "units": [{"name": f"単位{issue}", "tasks": ["T1"], "after": [], **extra}]}


def _put(root, name: str, content) -> None:
    path = root / "issues" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False), encoding="utf-8")


def _design(root, *rows: str) -> None:
    _put(root, "issue-7-8-9-design.md", "\n".join(["## 設計の結果", "", "| 課題 | 扱い | 統合先 |", "| --- | --- | --- |", *rows, ""]))


def _commit(root) -> str:
    git_sandbox.sh(root, "add", "-A", identity=True)
    git_sandbox.sh(root, "commit", "-q", "--allow-empty", "-m", "c", identity=True)
    return Git(root).run("rev-parse", "HEAD").strip()


CONTEXT = {"issues": [7, 8, 9], "plan_omitted": [9]}


@pytest.fixture
def work(tmp_path):
    """#7・#8 は単位のファイルを持ち、#9 は実装計画を省いた作業場所。"""
    root = tmp_path / "repo"
    root.mkdir()
    git_sandbox.sh(root, "init", "-q", identity=True)
    _put(root, "issue-7-plan.units.json", _unit_doc(7))
    _put(root, "issue-8-plan.units.json", _unit_doc(8))
    _design(root, "| #7 | 実装する | — |")
    _commit(root)
    return root


def _record(root, context=CONTEXT) -> dict:
    head = Git(root).run("rev-parse", "HEAD").strip()
    report = uf.check(uf.Tree(root, head), context["issues"], context["plan_omitted"], LIMITS, {7: 0, 8: 0, 9: 0}, docs=False)
    return dr.converted("o/r", head, "sprint/7", context, LIMITS, report)


def _mismatches(root, record, context=CONTEXT) -> list[str]:
    return dr.mismatches(record, uf.Tree(root, "HEAD"), context).differences


def test_converted_has_the_shape_of_a_built_record_without_a_pr(work):
    record = _record(work)

    head = Git(work).run("rev-parse", "HEAD").strip()
    assert record["review"] == {"repo": "o/r", "pr": None, "head": head, "base": "sprint/7"}
    assert (record["version"], record["sprint_branch"]) == (1, "sprint/7")
    assert record["targets"]["issues"] == [7, 8, 9]
    assert record["targets"]["omitted"] == [9]
    assert record["criteria"] == {"7": 0, "8": 0, "9": 0}
    assert [u["name"] for u in record["units"]][:2] == ["単位7", "単位8"]


def test_mismatches_ignores_actual_lines_only(work):
    record = _record(work)
    _put(work, "issue-7-plan.units.json", _unit_doc(7, actual_lines=33))
    _commit(work)

    compared = dr.mismatches(record, uf.Tree(work, "HEAD"), CONTEXT)
    assert compared.differences == []
    # 比較が読んで解析した対象は、検査が読み直して組む対象と同じ
    assert compared.targets is not None
    assert compared.targets.found == uf.collect(uf.Tree(work, "HEAD"), CONTEXT["issues"], CONTEXT["plan_omitted"])


@pytest.mark.parametrize(
    ("change", "context", "word"),
    [
        (lambda r: _put(r, "issue-7-plan.units.json", _unit_doc(7, lines=11)), CONTEXT, "issue-7-plan.units.json"),
        (lambda r: (r / "issues" / "issue-7-plan.units.json").unlink(), CONTEXT, "issue-7-plan.units.json"),
        (lambda r: _put(r, "issue-9-plan.units.json", _unit_doc(9)), CONTEXT, "issue-9-plan.units.json"),
        (lambda r: _put(r, "issue-7-plan.units.json", "壊れた"), CONTEXT, "issue-7-plan.units.json"),
        (lambda r: _design(r, "| #7 | 閉じる | — |"), CONTEXT, "扱い"),
        (lambda r: None, {**CONTEXT, "issues": [7, 8, 9, 10]}, "課題"),
        (lambda r: None, {**CONTEXT, "plan_omitted": []}, "省いた"),
    ],
)
def test_mismatches_names_each_difference_from_the_approval(work, change, context, word):
    record = _record(work)
    change(work)
    _commit(work)

    found = _mismatches(work, record, context)

    assert found and word in "\n".join(found)


def test_mismatches_reports_a_record_head_that_cannot_be_fetched(work):
    record = _record(work)
    record["review"]["head"] = "f" * 40

    found = _mismatches(work, record)

    assert len(found) == 1 and "f" * 40 in found[0]


def _files(root, record) -> list[ud.UnitFile]:
    tree = uf.Tree(root, record["review"]["head"])
    return [uf.load(tree, path) for path in sorted(uf.find(tree, record["targets"]["issues"]).values())]


@pytest.mark.parametrize(("drop", "left"), [([8], [7, 9]), ([9], [7, 8])])
def test_without_narrows_the_record_so_that_it_still_matches(work, drop, left):
    record = _record(work)

    narrowed = dr.without_issues(record, drop, _files(work, record))

    assert narrowed["targets"]["issues"] == left
    assert sorted(narrowed["criteria"]) == sorted(narrowed["targets"]["handling"]) == [str(n) for n in left]
    assert [u["issues"] for u in narrowed["units"]] == [[n] for n in left]
    context = {"issues": left, "plan_omitted": CONTEXT["plan_omitted"]}
    assert _mismatches(work, narrowed, context) == []


def test_narrowed_record_matches_while_stale_files_of_an_omitted_issue_remain(tmp_path, monkeypatch):
    _put(tmp_path, "issue-7-plan.units.json", _unit_doc(7))
    _put(tmp_path, "issue-8-plan.units.json", _unit_doc(8))
    # 実装計画を省いた #9 に、承認の記録に載らない古い単位のファイルが残っている（壊れていて、2 つある）
    _put(tmp_path, "issue-9-plan.units.json", "壊れた")
    _put(tmp_path, "issue-9-10-plan.units.json", "壊れた")
    tree = uf.Tree(tmp_path)
    report = uf.check(tree, CONTEXT["issues"], CONTEXT["plan_omitted"], LIMITS, {7: 0, 8: 0, 9: 0}, docs=False)
    record = dr.converted("o/r", "a" * 40, "sprint/7", CONTEXT, LIMITS, report)
    # コミットの代わりに作業場所を読む
    monkeypatch.setattr(dr, "_fetched", lambda g, head: True)
    monkeypatch.setattr(uf, "Tree", lambda root, head: tree)

    narrowed = dr.narrowed(record, [8], tmp_path)

    assert narrowed["targets"]["issues"] == [7, 9]
    assert dr.mismatches(narrowed, tree, {"issues": [7, 9], "plan_omitted": [9]}).differences == []


def test_without_refuses_to_split_a_file_of_two_issues(work):
    doc = _unit_doc(7)
    both = {**doc, "issues": [7, 8], "tasks": [{**doc["tasks"][0], "issue": 7}]}
    (work / "issues" / "issue-7-plan.units.json").unlink()
    (work / "issues" / "issue-8-plan.units.json").unlink()
    _put(work, "issue-7-8-plan.units.json", both)
    _commit(work)
    record = _record(work)

    with pytest.raises(model.Refused) as refused:
        dr.without_issues(record, [8], _files(work, record))
    assert "変更を求めて実装計画を分け直す" in refused.value.reason


@pytest.mark.parametrize("drop", [[7], [8]])
def test_without_refuses_to_drop_one_side_of_a_merged_issue(work, drop):
    (work / "issues" / "issue-8-plan.units.json").unlink()
    _design(work, "| #7 | 実装する | — |", "| #8 | マージする | #7 |")
    _commit(work)
    record = _record(work)

    with pytest.raises(model.Refused):
        dr.without_issues(record, drop, _files(work, record))
    assert dr.without_issues(record, [7, 8], _files(work, record))["targets"]["issues"] == [9]


def test_without_refuses_when_a_remaining_unit_needs_a_dropped_one(work):
    after = [{"unit": "単位7", "issue": 7, "reason": "形を使う"}]
    _put(work, "issue-8-plan.units.json", _unit_doc(8, after=after))
    _commit(work)
    record = _record(work)

    with pytest.raises(model.Refused):
        dr.without_issues(record, [7], _files(work, record))
