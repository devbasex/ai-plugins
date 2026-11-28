"""実装のプランの確かめ（`ndf.progress.units_guard`。#3702 の決定 12、受け入れ条件 3）のテスト。

分け方の承認が要るスプリントでは、単位を流す処理が起動した単位だけを通す。承認が要らないスプリントと単発の実装は通る。
ステートマシンの DB は本物の SQLite で、承認の記録の読み取り（`design_record.approved_of`）だけを差し替える。
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from result_json import last_result

from ndf.core import route
from ndf.progress import command, definition, design_record, gate, model, plan_runs, run, run_view, stages, store, units_cli, units_guard

SPRINT, UNIT, ISSUE = "sprint/s1", "sprint/s1-u3-0123abcd", 3
HEAD = "a" * 40
RECORD = {"review": {"head": HEAD}}


def _gated(snap: stages.Snapshot) -> stages.Snapshot:
    """工程「ドキュメントレビュー」が設計承認ゲートを持つ定義（配る定義にはまだ無い）。"""
    gate_step = next(st for s in snap.stages for st in s.steps if st.id == "design-gate")
    return dataclasses.replace(
        snap,
        stages=tuple(
            dataclasses.replace(s, steps=(*s.steps, dataclasses.replace(gate_step, stage=s.name))) if s.id == "document-review" else s
            for s in snap.stages
        ),
    )


@pytest.fixture
def make(repo, st):
    """スプリントのステートマシンを作り、その ID を返す。`approval` は分け方の承認が要る定義か。"""

    def create(name: str = "s1", *, approval: bool, issues=(ISSUE,), branch: str | None = SPRINT) -> str:
        snap = definition.load_definition("standard")
        snap = _gated(snap) if approval else snap
        context = {"issues": list(issues), "mode": "standard", **({} if branch is None else {"branch": branch})}
        doc = {**stages.snapshot_dict(snap), "context": context, "enablement": {}}
        with store.transaction(st) as conn:
            return store.create_machine(conn, ("sprint", command.repo_id(repo, st.root), name), None, doc, [])

    return create


@pytest.fixture
def approved(monkeypatch):
    """ステートマシンの ID → 今の承認の記録。"""
    held: dict[str, dict] = {}
    monkeypatch.setattr(design_record, "approved_of", lambda v: held.get(v.machine_id))
    return held


@pytest.fixture
def unit_file(tmp_path) -> Path:
    path = tmp_path / "unit.md"
    path.write_text("# 単位\n", encoding="utf-8")
    return path


def _guard(repo, st, *, issues=(ISSUE,), base=SPRINT, branch=UNIT, unit_file=None, review_file=...) -> list[str]:
    read = unit_file if review_file is ... else review_file
    return units_guard.guard(st, command.repo_id(repo, st.root), issues, base, branch, units_guard.Instructions(unit_file, read))


def _launch(st, tid: str, unit_file, record=RECORD, approval=None) -> None:
    approved_as = units_guard.Approval(record, approval)
    units_guard.record_launch(run_view.work_root(st.root, tid), UNIT, SPRINT, approved_as, [ISSUE], unit_file)


def test_a_unit_launched_under_the_current_approval_passes(repo, st, make, approved, unit_file):
    tid = make(approval=True)
    approved[tid] = RECORD
    _launch(st, tid, unit_file)

    assert _guard(repo, st, unit_file=unit_file) == []


def test_a_unit_without_an_instruction_is_launched_with_an_empty_hash(repo, st, make, approved):
    tid = make(approval=True)
    approved[tid] = RECORD
    _launch(st, tid, None)

    assert _guard(repo, st) == []
    saved = next((run_view.work_root(st.root, tid) / "units" / "guard").glob("*.json"))
    assert json.loads(saved.read_text(encoding="utf-8")) == {
        "branch": UNIT, "base": SPRINT, "record_head": HEAD, "approval_seq": None, "issues": [ISSUE], "unit_sha256": "",
    }  # fmt: skip


CHANGES = [
    "issue", "added_issue", "base", "branch", "instruction", "no_instruction", "review_instruction", "approval", "reapproval",
    "never_launched", "unapproved",
]  # fmt: skip


@pytest.mark.parametrize("change", CHANGES)
def test_a_plan_that_differs_from_the_launch_is_stopped(repo, st, make, approved, unit_file, monkeypatch, change):
    tid = make(approval=True, issues=(ISSUE, 4))
    approved[tid] = RECORD
    if change != "never_launched":
        _launch(st, tid, unit_file)
    given: dict = {"unit_file": unit_file}
    if change == "issue":
        given["issues"] = (4,)
    if change == "added_issue":
        given["issues"] = (ISSUE, 4)
    if change == "review_instruction":
        given["review_file"] = None
    if change == "reapproval":
        # 同じ HEAD の承認し直し
        monkeypatch.setattr(design_record, "approval_seq_of", lambda _v: 9)
    if change == "base":
        given["base"] = "main"
    if change == "branch":
        given["branch"] = "feat/x"
    if change == "instruction":
        unit_file.write_text("# 書き換えた指示\n", encoding="utf-8")
    if change == "no_instruction":
        given["unit_file"] = None
    if change == "approval":
        approved[tid] = {"review": {"head": "b" * 40}}
    if change == "unapproved":
        del approved[tid]

    found = _guard(repo, st, **given)

    assert found and f"ndf progress units run {tid}" in found[0]


@pytest.mark.parametrize("given", [{}, {"base": "main"}, {"issues": (99,)}])
def test_a_hand_built_plan_of_a_sprint_without_approval_passes(repo, st, make, given):
    """今の経路（保存した状態にあるスプリント宛ての手で組んだプラン）。"""
    make(approval=False)

    assert _guard(repo, st, **given) == []


def test_an_issue_of_the_acceptance_other_than_the_representative_is_checked(repo, st, make):
    make(approval=True)

    assert _guard(repo, st, issues=(99, ISSUE), base="main", branch="feat/x")


def test_a_sprint_branch_written_after_the_creation_is_found(repo, st, make, monkeypatch):
    tid = make(approval=True, issues=(1,), branch=None)
    written = model.Event(1, None, model.EventDraft(stages.OUTPUTS_EVENT, run_view.ACTOR, rev=1, payload={"outputs": {"branch": SPRINT}}))
    monkeypatch.setattr(store, "read_events_of_kind", lambda *_a: {tid: [written]})

    assert _guard(repo, st)


def test_a_sprint_the_plan_does_not_touch_has_no_events_read(repo, st, make, monkeypatch):
    make(approval=True, issues=(1,), branch="sprint/other")
    monkeypatch.setattr(command, "read_current", lambda *_a: pytest.fail("当たらないスプリントの事象を読んだ"))

    assert _guard(repo, st, base="main") == []


def test_a_single_implementation_passes(repo, st):
    assert _guard(repo, st, base="main", branch="feat/x") == []


def test_an_unregistered_sprint_branch_is_stopped(repo, st, make):
    make(approval=False, issues=(1,), branch="sprint/other")

    found = _guard(repo, st)

    assert found and SPRINT in found[0]


def test_a_finished_sprint_and_another_repository_are_not_read(repo, st, make):
    tid = make(approval=True)
    assert command.main(["cancel", tid, "--reason", "やめた"]) == 0
    snap = _gated(definition.load_definition("standard"))
    doc = {**stages.snapshot_dict(snap), "context": {"issues": [ISSUE]}, "enablement": {}}
    with store.transaction(st) as conn:
        store.create_machine(conn, ("sprint", "other/repo", "s9"), None, doc, [])

    assert _guard(repo, st, base="main") == []


# --- コマンド -------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_inherited_instruction(monkeypatch):
    monkeypatch.delenv("NDF_UNIT_FILE", raising=False)


def _cli(repo, capsys, *, base=SPRINT, unit_file="") -> tuple[int, dict]:
    argv = ["units", "guard", "--issue", str(ISSUE), "--base", base, "--branch", UNIT, f"--unit-file={unit_file}"]
    code = gate.progress_main([*argv, "--cwd", str(repo)])
    return code, last_result(capsys.readouterr().out)


def test_the_command_passes_a_single_implementation(repo, capsys):
    code, got = _cli(repo, capsys, base="main")

    assert (code, got["status"]) == (0, "ok"), got


def test_the_command_refuses_a_plan_that_was_not_launched(repo, st, make, capsys):
    tid = make(approval=True)

    _, got = _cli(repo, capsys)

    assert (got["status"], got["cause"]["kind"]) == ("stopped", "refused"), got
    assert f"ndf progress units run {tid}" in got["summary"]


def test_the_command_stops_when_the_instruction_cannot_be_read(repo, tmp_path, capsys):
    _, got = _cli(repo, capsys, base="main", unit_file=str(tmp_path / "missing.md"))

    assert (got["status"], got["cause"]["kind"]) == ("stopped", "precondition"), got


def test_the_command_stops_when_the_saved_state_cannot_be_read(repo, monkeypatch, capsys):
    def broken(*_a, **_k):
        raise store.StoreUnreachable("DB を使えない")

    monkeypatch.setattr(command, "with_store", broken)

    _, got = _cli(repo, capsys, base="main")

    assert (got["status"], got["cause"]["kind"]) == ("stopped", "precondition"), got


# --- 起動の記録 ------------------------------------------------------------------------------------


def test_the_sprint_plan_records_a_launch_only_under_an_approval(repo, st, make, approved, unit_file):
    tid = make(approval=True)
    approved[tid] = RECORD
    with store.transaction(st) as conn:
        view = run.view(conn, tid)
    stage = dataclasses.replace(next(s for s in view.snap.stages if s.id == "implement"), per_unit=True)

    held = run.sprint_plan(view, stage, st.root)
    assert held is not None and held.launched is not None
    held.launched(UNIT, SPRINT, issues=[ISSUE], unit_file=unit_file)
    assert _guard(repo, st, unit_file=unit_file) == []

    del approved[tid]
    unapproved = run.sprint_plan(view, stage, st.root)
    assert unapproved is not None and unapproved.launched is None
    assert plan_runs.SprintPlan().launched is None


# --- テンプレート ----------------------------------------------------------------------------------


def _render(tmp_path: Path, *extra: str, issue: bool = True) -> dict:
    out = tmp_path / "impl.json"
    args = [
        "implement", "--out", str(out), "--set", f"branch={UNIT}", "--set", f"base={SPRINT}", "--set", "title=単位",
        "--set", "body_file=b.md", "--set", "test_command=pytest -q", "--add", "worker=ndf-worker",
        "--add", "required=test", "--set", f"repo={tmp_path}", "--set", f"worktree={tmp_path}", *extra,
    ]  # fmt: skip
    assert route.main(["render", *args, *(["--set", f"issue={ISSUE}"] if issue else [])]) == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_implement_plan_guards_after_the_done_check_and_passes_the_current_sprint(repo, st, make, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("NDF_UNIT_FILE", raising=False)
    make(approval=False)
    steps = _render(tmp_path)["steps"]
    capsys.readouterr()
    check = steps[1]

    assert [s["id"] for s in steps[:2]] == ["done-check", "guard"]
    assert check["run"] == [
        "ndf", "progress", "units", "guard", "--issue", str(ISSUE), "--issue", str(ISSUE), "--base", SPRINT,
        "--branch", UNIT, "--unit-file=",
    ]  # fmt: skip
    assert gate.progress_main([*check["run"][2:], "--cwd", str(repo)]) == 0


def test_a_bundled_plan_guards_every_issue_of_its_acceptance(tmp_path):
    plan = _render(tmp_path, *(x for v in ("--issue", "5", "--issue", "4") for x in ("--add", f"acceptance={v}")), issue=False)

    assert plan["steps"][1]["run"][4:10] == ["--issue", "4", "--issue", "5", "--issue", "4"]


def test_the_units_command_has_the_guard():
    args = command.parser((units_cli.add_parsers,)).parse_args(
        ["units", "guard", "--issue", "3", "--base", "main", "--branch", "feat/x", "--unit-file="]
    )

    assert (args.units_action, args.issue, args.unit_file) == ("guard", [3], "")
