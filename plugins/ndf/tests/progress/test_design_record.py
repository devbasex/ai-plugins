"""承認の記録（`ndf.progress.design_record`）のテスト（issues/issue-3702-design-detail.md の「承認の記録」）。

`build` の入力は `ndf plan units check` が保存先に書く `checked.json` そのもので作る。単位のファイルと記録の比較は、
一時の git のリポジトリのコミットとworktreeで確かめる。
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import git_sandbox
import pytest

from ndf.core.plan import cli
from ndf.core.plan import units_decide as ud
from ndf.core.plan import units_files as uf
from ndf.progress import design_record, folding, model, stages
from ndf.progress.run_view import ANSWERED, STEP_EVENT, View

LIMITS = ud.Limits(target=400, max=1000)
REPO = "acme/app"
BRANCH = "sprint/s1"
REV = 1
REVIEW = "ドキュメントレビュー"
IMPLEMENT = "実装"
BODY = "## 受け入れ条件\n\n- [ ] 1. 読める\n- [ ] 2. 書ける\n"


# --- 単位のファイルのあるリポジトリ ---------------------------------------------------------------


def _task(tid: str, files=(), criteria=(), after=()):
    return {"id": tid, "title": tid, "files": list(files), "lines": 10, "criteria": list(criteria), "after": list(after)}


def _put(root: Path, name: str, text: str) -> None:
    (root / "issues").mkdir(exist_ok=True)
    (root / "issues" / name).write_text(text, encoding="utf-8")


def _units_file(root: Path, *issues: int, units=None, tasks=None) -> None:
    key = "-".join(map(str, issues))
    tasks = tasks if tasks is not None else [_task("T1", files=[f"{key}.py"])]
    if len(issues) > 1:
        tasks = [{**t, "issue": t.get("issue", issues[0])} for t in tasks]
    units = units if units is not None else [{"name": f"U{key}", "tasks": [t["id"] for t in tasks], "after": []}]
    doc = {"version": 1, "issues": list(issues), "tasks": tasks, "units": units}
    _put(root, f"issue-{key}-plan.units.json", json.dumps(doc, ensure_ascii=False))


def _design(root: Path, key: str, *rows: str) -> None:
    table = "\n".join(["| 課題 | 扱い | 統合先 |", "| --- | --- | --- |", *rows])
    _put(root, f"issue-{key}-design.md", f"# 設計\n\n## 設計の結果\n\n{table}\n")


def _commit(root: Path) -> str:
    git_sandbox.sh(root, "add", "-A", identity=True)
    git_sandbox.sh(root, "commit", "-q", "--allow-empty", "-m", "c", identity=True)
    return git_sandbox.sh(root, "rev-parse", "HEAD", strip=True)


@pytest.fixture
def work(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git_sandbox.sh(root, "init", "-q")
    return root


def _context(*issues: int, omitted=()) -> dict[str, Any]:
    return {"issues": list(issues), **({"plan_omitted": list(omitted)} if omitted else {})}


def _record(root: Path, *issues: int, omitted=()) -> dict[str, Any]:
    """今の中身をコミットし、そのコミットを対象にした記録を作る。"""
    head = _commit(root)
    context = _context(*issues, omitted=omitted)
    report = uf.check(uf.Tree(root, head), issues, omitted, LIMITS, {n: 2 for n in issues}, False)
    assert not report.judgement.violations and not report.missing
    return design_record.converted(REPO, head, BRANCH, context, LIMITS, design_record.Checked(report, {n: 2 for n in issues}))


def _mismatches(record: dict[str, Any], root: Path, context: dict[str, Any]) -> list[str]:
    return design_record.mismatches(record, uf.Tree(root), context)


def _files(record: dict[str, Any], root: Path) -> tuple[ud.UnitFile, ...]:
    tree = uf.Tree(root, record["review"]["head"])
    return uf.collect(tree, record["targets"]["issues"], record["targets"]["omitted"]).files


# --- converted ---------------------------------------------------------------------------------


def test_converted_has_the_shape_of_the_design_target(work):
    _units_file(work, 7)

    record = _record(work, 7, 8, omitted=[8])

    head = git_sandbox.sh(work, "rev-parse", "HEAD", strip=True)
    assert record["version"] == 1
    assert record["review"] == {"repo": REPO, "pr": None, "head": head, "base": None}
    assert record["sprint_branch"] == BRANCH
    assert record["targets"] == {
        "issues": [7, 8],
        "handling": {"7": {"handling": "実装する", "into": None}, "8": {"handling": "実装する", "into": None}},
        "omitted": [8],
    }
    assert record["limits"] == {"target": 400, "max": 1000}
    assert record["criteria"] == {"7": 2, "8": 2}
    assert [u["name"] for u in record["units"]] == ["U7", "issue-8"]


# --- build -------------------------------------------------------------------------------------


class _Hub:
    def nodes(self, numbers, node, fields):
        return {n: {"body": BODY} if node == "issue" else {} for n in numbers}


@pytest.fixture
def checked(work, tmp_path, monkeypatch, capsys):
    """`ndf plan units check` がレビューの記録を読んで `checked.json` を書いた保存先と、レビューした HEAD。"""
    monkeypatch.setattr(cli, "_hub", lambda root: _Hub())
    monkeypatch.chdir(work)
    _units_file(work, 7)
    _put(work, "issue-7-plan.md", "# 実装計画\n\n## タスク分解\n\n### Task 1: 作業\n")
    monkeypatch.delenv("NDF_STAGE_INPUT", raising=False)
    monkeypatch.delenv("NDF_DESIGN_RECORD_DIR", raising=False)
    assert cli.main(["units", "render", "--issue", "7"]) == 0
    head = _commit(work)
    record_dir = tmp_path / "record"
    record_dir.mkdir()
    (record_dir / "reviewed-head").write_text(head + "\n", encoding="utf-8")
    (record_dir / "reviewed-pr").write_text(f"{REPO}#12\n", encoding="utf-8")
    (record_dir / "reviewed-base").write_text("main\n", encoding="utf-8")
    monkeypatch.setenv("NDF_DESIGN_RECORD_DIR", str(record_dir))
    monkeypatch.setenv("NDF_STAGE_INPUT", json.dumps({"issues": [7], "worktree": str(work), "branch": BRANCH}))
    code = cli.main(["units", "check"])
    assert code == 0, capsys.readouterr().out
    return record_dir, head


def test_build_joins_the_review_files_and_the_check_and_writes_the_record(checked, work):
    record_dir, head = checked

    record = design_record.build(record_dir)

    assert record["version"] == 1
    assert record["review"] == {"repo": REPO, "pr": 12, "head": head, "base": "main"}
    assert record["sprint_branch"] == BRANCH
    assert record["targets"] == {"issues": [7], "handling": {"7": {"handling": "実装する", "into": None}}, "omitted": []}
    assert (record["limits"], record["criteria"]) == ({"target": 400, "max": 1000}, {"7": 2})
    assert [u["name"] for u in record["units"]] == ["U7"]
    written = json.loads((record_dir / "design-target.json").read_text(encoding="utf-8"))
    assert written.pop("ndf_digest")
    assert written == {"ndf_record": "design-target", **record}
    assert design_record.mismatches(record, uf.Tree(work), _context(7)) == []


def test_build_replaces_the_record_it_wrote_before(checked):
    record_dir, _ = checked

    first = design_record.build(record_dir)

    assert design_record.build(record_dir) == first


@pytest.mark.parametrize("held", [b"{}", b"memo", b"\xff"])
def test_build_keeps_a_target_file_that_ndf_did_not_write(checked, held):
    record_dir, _ = checked
    (record_dir / "design-target.json").write_bytes(held)
    (record_dir / "design-target.tmp").write_text("keep", encoding="utf-8")

    with pytest.raises(design_record.RecordUnreadable, match="design-target.json"):
        design_record.build(record_dir)

    assert (record_dir / "design-target.json").read_bytes() == held
    assert (record_dir / "design-target.tmp").read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("name", ["reviewed-pr", "reviewed-head", "reviewed-base", "checked.json"])
def test_build_names_the_file_that_is_missing(checked, name):
    record_dir, _ = checked
    (record_dir / name).unlink()

    with pytest.raises(design_record.RecordUnreadable, match=name):
        design_record.build(record_dir)

    assert not (record_dir / "design-target.json").exists()


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("reviewed-pr", "12"),
        ("reviewed-head", ""),
        ("checked.json", "{"),
        ("checked.json", "{}"),
        ("checked.json", '{"ndf_record": "design-units-check"}'),
    ],
)
def test_build_refuses_a_broken_file(checked, name, content):
    record_dir, _ = checked
    (record_dir / name).write_text(content, encoding="utf-8")

    with pytest.raises(design_record.RecordUnreadable, match=name):
        design_record.build(record_dir)


def test_build_refuses_a_check_of_another_head(checked):
    record_dir, _ = checked
    (record_dir / "reviewed-head").write_text("0" * 40, encoding="utf-8")

    with pytest.raises(design_record.RecordUnreadable, match="checked.json"):
        design_record.build(record_dir)


def test_build_does_not_follow_a_linked_input_or_quote_it(tmp_path):
    secret = tmp_path / "credentials"
    secret.write_text("secret-token", encoding="utf-8")
    record_dir = tmp_path / "record"
    record_dir.mkdir()
    (record_dir / "reviewed-head").symlink_to(secret)

    with pytest.raises(design_record.RecordUnreadable, match="reviewed-head") as caught:
        design_record.build(record_dir)

    assert "secret-token" not in str(caught.value)


def _append(path: Path, **changed: Any) -> bytes:
    held = json.loads(path.read_text(encoding="utf-8")) | changed
    path.write_text(json.dumps(held), encoding="utf-8")
    return path.read_bytes()


def test_build_keeps_a_target_file_changed_after_ndf_wrote_it(checked):
    record_dir, _ = checked
    design_record.build(record_dir)
    held = _append(record_dir / "design-target.json", memo="keep")

    with pytest.raises(design_record.RecordUnreadable, match="design-target.json"):
        design_record.build(record_dir)

    assert (record_dir / "design-target.json").read_bytes() == held


def test_build_refuses_a_check_whose_units_changed_after_it_was_written(checked):
    record_dir, _ = checked
    _append(record_dir / "checked.json", units=[])

    with pytest.raises(design_record.RecordUnreadable, match="中身が変わっている"):
        design_record.build(record_dir)

    assert not (record_dir / "design-target.json").exists()


def test_build_refuses_a_check_that_others_can_write(checked):
    record_dir, _ = checked
    (record_dir / "checked.json").chmod(0o620)

    with pytest.raises(design_record.RecordUnreadable, match="checked.json を読めない"):
        design_record.build(record_dir)

    assert not (record_dir / "design-target.json").exists()


def _check_record(**changed: Any) -> dict[str, Any]:
    unit = {"kind": "unit", "file": "", "name": "issue-7", "order": 1, "merge": 1, "after": [], "issues": [7]}
    unit |= {"tasks": [{"id": "T1", "issue": 7}], "criteria": [], "lines": None, "touches": []}
    held = {"ndf_record": "design-units-check", "ndf_digest": "0" * 64, "head": "a" * 40, "branch": BRANCH, "issues": [7]}
    held |= {"handling": {"7": {"handling": "実装する", "into": None}}, "omitted": [7]}
    held |= {"limits": {"target": 400, "max": 1000}, "criteria": {"7": 2}, "units": [unit]}
    return held | changed


@pytest.mark.parametrize(
    "changed",
    [
        {"units": {}},
        {"units": [{"kind": "unit"}]},
        {"handling": {}},
        {"handling": {"7": "実装する"}},
        {"omitted": [8]},
        {"limits": {"target": "400", "max": 1000}},
        {"criteria": {"7": "2"}},
        {"criteria": {}},
        {"ndf_digest": "A" * 64},
    ],
)
def test_build_refuses_a_check_that_breaks_the_record_contract(tmp_path, changed):
    (tmp_path / "reviewed-head").write_text("a" * 40, encoding="utf-8")
    (tmp_path / "reviewed-pr").write_text(f"{REPO}#12", encoding="utf-8")
    (tmp_path / "reviewed-base").write_text("main", encoding="utf-8")
    (tmp_path / "checked.json").write_text(json.dumps(_check_record(**changed)), encoding="utf-8")
    (tmp_path / "checked.json").chmod(0o600)

    with pytest.raises(design_record.RecordUnreadable, match="checked.json の形が違う"):
        design_record.build(tmp_path)

    assert not (tmp_path / "design-target.json").exists()


# --- stale -------------------------------------------------------------------------------------


def _reviewed() -> dict[str, Any]:
    return {"review": {"repo": REPO, "pr": 12, "head": "a" * 40, "base": "main"}}


def test_stale_is_empty_for_the_reviewed_pull_request():
    assert design_record.stale(_reviewed(), "Acme/App", 12, "a" * 40, "main") == []


@pytest.mark.parametrize(
    ("changed", "word"),
    [({"repo": "acme/other"}, "repo"), ({"pr": 13}, "pr"), ({"head": "b" * 40}, "head"), ({"base": "release"}, "base")],
)
def test_stale_names_only_the_changed_value(changed, word):
    now = {**_reviewed()["review"], **changed}

    [found] = design_record.stale(_reviewed(), now["repo"], now["pr"], now["head"], now["base"])

    assert found.startswith(word)


def test_stale_treats_an_unread_value_as_changed():
    found = design_record.stale(_reviewed(), None, None, None, None)

    assert len(found) == 4


# --- required・reset_drafts・approved_of --------------------------------------------------------


def _step(stage: str, sid: str, kind: str = "record", gate: Optional[str] = None) -> stages.StepDef:
    return stages.StepDef(sid, stage, "ndf.plan", kind, "none", gate=gate)


def _review_stage(gate: Optional[str] = "design") -> stages.StageDef:
    steps = [_step(REVIEW, "design-pr-review", "review"), _step(REVIEW, "units-check")]
    if gate is not None:
        steps.append(_step(REVIEW, "design-gate", stages.GATE_KIND, gate))
    steps.append(_step(REVIEW, "design-merge-approved", "merge"))
    return stages.StageDef("document-review", REVIEW, "design", tuple(steps))


def _snap(*defs: stages.StageDef) -> stages.Snapshot:
    return stages.Snapshot("standard", "1", "x", 1, defs)


def _view(*drafts: model.EventDraft, snap: Optional[stages.Snapshot] = None) -> View:
    events = [model.Event(n, datetime.now(timezone.utc), d) for n, d in enumerate(drafts, 1)]
    return View("m", REV, snap or _snap(_review_stage(), stages.StageDef("implement", IMPLEMENT, "impl", ())), {}, events)


def _answered(answer: str, stage: str = REVIEW, **extra) -> model.EventDraft:
    payload = {"answer": answer, "gate": "design", **extra}
    return model.EventDraft(ANSWERED, "conductor", stage, payload=payload, rev=REV)


def test_required_when_the_document_review_has_the_design_gate():
    assert design_record.required(_snap(_review_stage())) is True


@pytest.mark.parametrize(
    "stage",
    [
        _review_stage(gate=None),
        # 古い定義は工程「設計」が設計承認ゲートを持つ
        stages.StageDef("design", "詳細設計", "design", (_step("詳細設計", "design-gate", stages.GATE_KIND, "design"),)),
    ],
)
def test_not_required_without_the_gate_in_the_document_review(stage):
    assert design_record.required(_snap(stage)) is False


def test_reset_drafts_returns_the_steps_up_to_the_gate_to_unfinished():
    stage = _review_stage()
    done = [
        model.EventDraft(
            STEP_EVENT, "workflow", REVIEW, payload={"step": s.id, "status": "finished", "report": {}, "generation": 0}, rev=REV
        )
        for s in stage.steps[:2]
    ]

    drafts = design_record.reset_drafts(_view(*done), stage, "head が変わった")

    assert [(d.kind, d.row_key, d.rev) for d in drafts] == [(STEP_EVENT, REVIEW, REV)] * 3
    assert [d.payload["step"] for d in drafts] == ["design-pr-review", "units-check", "design-gate"]
    assert {(d.payload["status"], d.payload["reason"], d.payload["generation"]) for d in drafts} == {(stages.RESET, "head が変わった", 0)}
    assert folding.step_progress(_view(*done, *drafts), REVIEW).done == {}


def test_approved_of_returns_the_target_of_the_latest_approval():
    first, last = {"version": 1, "n": 1}, {"version": 1, "n": 2}

    v = _view(_answered("approved", design_target=first), _answered("approved", design_target=last))

    assert design_record.approved_of(v) == last


def test_approved_of_reads_the_answer_of_the_mvv_judgement():
    v = _view(_answered("approved", by=model.MVV_SERVICE, mvv_gate={"gate": "design", "holds": []}, design_target={"n": 1}))

    assert design_record.approved_of(v) == {"n": 1}


@pytest.mark.parametrize(
    "later",
    [[], [_answered("request_changes")], [_answered("rejected")]],
)
def test_approved_of_is_none_after_a_later_answer_or_without_the_target(later):
    held = [_answered("approved", design_target={"n": 1}), *later] if later else [_answered("approved")]

    assert design_record.approved_of(_view(*held)) is None


def test_approved_of_does_not_go_back_past_a_void_mvv_answer():
    void = _answered("approved", by=model.MVV_SERVICE, design_target={"n": 2})

    assert design_record.approved_of(_view(_answered("approved", design_target={"n": 1}), void)) is None


def test_approved_of_is_none_after_the_gate_returns_to_unfinished():
    approved = _answered("approved", design_target={"n": 1})
    drafts = design_record.reset_drafts(_view(approved), _review_stage(), "head が変わった")

    assert design_record.approved_of(_view(approved, *drafts)) is None
    again = _answered("approved", design_target={"n": 2})
    assert design_record.approved_of(_view(approved, *drafts, again)) == {"n": 2}


def test_approved_of_ignores_answers_to_other_waits_of_the_stage():
    other = model.EventDraft(ANSWERED, "conductor", REVIEW, payload={"answer": "continue"}, rev=REV)

    assert design_record.approved_of(_view(_answered("approved", design_target={"n": 1}), other)) == {"n": 1}


def test_reapproved_of_reads_the_latest_answer_of_the_implement_stage():
    approved = _answered("approved", stage=IMPLEMENT, design_target={"n": 3})

    assert design_record.reapproved_of(_view(approved)) == {"n": 3}
    assert design_record.reapproved_of(_view(approved, _answered("rejected", stage=IMPLEMENT))) is None
    assert design_record.reapproved_of(_view(_answered("approved", design_target={"n": 1}))) is None


# --- mismatches --------------------------------------------------------------------------------


def test_mismatches_ignores_only_the_actual_lines(work):
    _units_file(work, 7)
    record = _record(work, 7)

    _units_file(work, 7, units=[{"name": "U7", "tasks": ["T1"], "after": [], "actual_lines": 33}])

    assert _mismatches(record, work, _context(7)) == []


def test_mismatches_reports_a_changed_or_removed_units_file(work):
    _units_file(work, 7)
    record = _record(work, 7)

    _units_file(work, 7, units=[{"name": "別の名前", "tasks": ["T1"], "after": []}])
    [changed] = _mismatches(record, work, _context(7))
    assert "issue-7-plan.units.json" in changed

    (work / "issues" / "issue-7-plan.units.json").unlink()
    [removed] = _mismatches(record, work, _context(7))
    assert "issue-7-plan.units.json" in removed


def test_mismatches_reports_a_units_file_moved_to_another_name_of_the_same_issue(work):
    _units_file(work, 7)
    record = _record(work, 7)

    (work / "issues" / "issue-7-plan.units.json").rename(work / "issues" / "issue-07-plan.units.json")

    [found] = _mismatches(record, work, _context(7))
    assert "issue-07-plan.units.json" in found and "issue-7-plan.units.json" in found


def test_mismatches_reports_an_unreadable_units_file(work):
    _units_file(work, 7)
    record = _record(work, 7)

    _put(work, "issue-7-plan.units.json", "{")

    found = _mismatches(record, work, _context(7))
    assert found and all("issue-7-plan.units.json" in m for m in found)


def test_mismatches_reports_an_issue_added_to_the_sprint_after_the_approval(work):
    _units_file(work, 7)
    record = _record(work, 7)

    [found] = _mismatches(record, work, _context(7, 9))

    assert "課題" in found and "9" in found


def test_mismatches_reports_a_changed_handling_and_merge_target(work):
    _units_file(work, 7)
    _units_file(work, 8)
    _design(work, "7-8", "| #7 | 実装する | |", "| #8 | 実装する | |")
    record = _record(work, 7, 8)

    _design(work, "7-8", "| #7 | 実装する | |", "| #8 | 閉じる | |")
    [found] = _mismatches(record, work, _context(7, 8))
    assert "#8" in found and "閉じる" in found

    _design(work, "7-8", "| #7 | 実装する | |", "| #8 | マージする | #7 |")
    [found] = _mismatches(record, work, _context(7, 8))
    assert "#8" in found and "マージする" in found


def test_mismatches_reports_a_changed_omitted_issue(work):
    _units_file(work, 7)
    record = _record(work, 7, 8, omitted=[8])

    assert _mismatches(record, work, _context(7, 8, omitted=[8])) == []
    found = _mismatches(record, work, _context(7, 8))
    assert any("省いた" in m for m in found)


def test_mismatches_fetches_the_recorded_head_once_and_names_it_when_it_is_missing(work, tmp_path):
    _units_file(work, 7)
    record = _record(work, 7)
    clone = tmp_path / "clone"
    shutil.copytree(work, clone)
    git_sandbox.sh(clone, "remote", "add", "origin", str(work))
    _put(work, "note.md", "x")
    later = _commit(work)
    record["review"]["head"] = later

    assert _mismatches(record, clone, _context(7)) == []

    record["review"]["head"] = "0" * 40
    [found] = _mismatches(record, clone, _context(7))
    assert "0" * 40 in found


# --- without_issues -----------------------------------------------------------------------------------


def _two(work: Path, after=()) -> dict[str, Any]:
    _units_file(work, 7)
    _units_file(work, 8, units=[{"name": "U8", "tasks": ["T1"], "after": list(after)}])
    return _record(work, 7, 8)


def test_without_narrows_the_record_so_that_the_check_passes_after_the_drop(work):
    _units_file(work, 7)
    _units_file(work, 8)
    record = _record(work, 7, 8, 9, omitted=[9])
    before = json.loads(json.dumps(record))

    narrowed = design_record.without_issues(record, [7, 9], _files(record, work))

    assert record == before
    assert narrowed["targets"] == {"issues": [8], "handling": {"8": {"handling": "実装する", "into": None}}, "omitted": []}
    assert narrowed["criteria"] == {"8": 2}
    assert [(u["name"], u["order"], u["merge"]) for u in narrowed["units"]] == [("U8", 1, 1)]
    assert (narrowed["review"], narrowed["limits"], narrowed["sprint_branch"]) == (record["review"], record["limits"], BRANCH)
    # 外した課題の単位のファイルと省略の指定は作業場所と文脈に残っても、比較を通る
    assert _mismatches(narrowed, work, _context(8, omitted=[9])) == []


def test_without_drops_the_merge_source_together_with_its_target(work):
    _units_file(work, 7)
    _units_file(work, 9)
    _design(work, "7-8", "| #7 | 実装する | |", "| #8 | マージする | #7 |")
    record = _record(work, 7, 8, 9)

    narrowed = design_record.without_issues(record, [7, 8], _files(record, work))

    assert narrowed["targets"]["issues"] == [9]


@pytest.mark.parametrize("drop", [[7], [8]])
def test_without_refuses_only_one_side_of_a_merge(work, drop):
    _units_file(work, 7)
    _units_file(work, 9)
    _design(work, "7-8", "| #7 | 実装する | |", "| #8 | マージする | #7 |")
    record = _record(work, 7, 8, 9)

    with pytest.raises(model.Refused, match="分け直す"):
        design_record.without_issues(record, drop, _files(record, work))


def test_without_refuses_a_file_shared_with_a_kept_issue(work):
    _units_file(work, 7, 8)
    _units_file(work, 9)
    record = _record(work, 7, 8, 9)

    with pytest.raises(model.Refused, match="issue-7-8-plan.units.json.*分け直す"):
        design_record.without_issues(record, [7], _files(record, work))


def test_without_refuses_when_a_kept_unit_needs_a_dropped_unit(work):
    record = _two(work, after=[{"unit": "U7", "issue": 7, "reason": "形を使う"}])

    with pytest.raises(model.Refused, match="U1.*分け直す"):
        design_record.without_issues(record, [7], _files(record, work))


def test_without_refuses_files_that_do_not_hold_the_remaining_units(work):
    record = _two(work)

    with pytest.raises(model.Refused, match="issue-8-plan.units.json"):
        design_record.without_issues(record, [7], ())


@pytest.mark.parametrize("linked", ["record", "above_record"])
def test_build_does_not_read_through_a_linked_directory_under_the_git_directory(checked, tmp_path, linked):
    record_dir, _ = checked
    held = tmp_path / "repo" / ".git" / "ndf" / "stage" / "record"
    held.parent.parent.mkdir(parents=True)
    if linked == "record":
        held.parent.mkdir()
        held.symlink_to(record_dir, target_is_directory=True)
    else:
        outside = tmp_path / "outside"
        outside.mkdir()
        record_dir.rename(outside / "record")
        held.parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(design_record.RecordUnreadable):
        design_record.build(held)


def test_build_reads_through_a_link_above_the_git_directory(checked, tmp_path):
    record_dir, head = checked
    real = tmp_path / "home"
    (real / "repo" / ".git").mkdir(parents=True)
    record_dir.rename(real / "repo" / ".git" / "record")
    (tmp_path / "linked-home").symlink_to(real, target_is_directory=True)

    record = design_record.build(tmp_path / "linked-home" / "repo" / ".git" / "record")

    assert record["review"]["head"] == head


def test_build_does_not_quote_an_input_key_of_a_broken_check(checked):
    record_dir, head = checked
    held = json.loads((record_dir / "checked.json").read_text(encoding="utf-8"))
    held["criteria"] = {"secret-key": "x"}
    (record_dir / "checked.json").write_text(json.dumps(held), encoding="utf-8")

    with pytest.raises(design_record.RecordUnreadable, match="checked.json") as caught:
        design_record.build(record_dir)

    assert "secret-key" not in str(caught.value)


def test_build_keeps_a_hard_linked_target_file(checked, tmp_path):
    record_dir, _ = checked
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"ndf_record": "design-target"}), encoding="utf-8")
    os.link(other, record_dir / "design-target.json")

    with pytest.raises(design_record.RecordUnreadable, match="design-target.json"):
        design_record.build(record_dir)

    assert (record_dir / "design-target.json").samefile(other)
