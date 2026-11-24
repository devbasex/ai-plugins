"""ステートマシンの ID から判定のステップの入力を組む（`ndf.progress.stage_inputs`。#3959 の受け入れ条件 8・#4055）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.progress import command, definition, invoke, model, stage_inputs, store

SHA = "a" * 64
WORKTREE_OUT = {"worktree": "/work/repo/.worktrees/sprint/s1", "branch": "sprint/s1"}


def _run(capsys, last_result, *argv: str) -> dict:
    assert command.main(list(argv)) == 0
    return last_result(capsys.readouterr().out)


def _machine(repo: Path, capsys, last_result, tmp_path: Path, *, mode: str = "standard") -> str:
    """スプリントを作り、工程「作業場所の用意」を終えて worktree を文脈へ書く。ステートマシンの ID を返す。"""
    (repo / ".ndf").mkdir(exist_ok=True)
    (repo / ".ndf" / "mvv.json").write_text(json.dumps({"body": "mvv.md", "versions": [{"version": 1, "sha256": SHA}]}))
    made = _run(capsys, last_result, "new", "s1", "--mode", mode, "--issues", "3959", "--pace", "normal")
    tid = made["metrics"]["machine_id"]
    out_file = tmp_path / "worktree.json"
    out_file.write_text(json.dumps({"steps": [], "summary": "作業場所を用意した", "outputs": WORKTREE_OUT}))
    _run(capsys, last_result, "finish", tid, "作業場所の用意", "--output", str(out_file), "--reason", "手で")
    return tid


def _context(tid: str) -> dict:
    root = command.store_root_of(Path.cwd())
    with store.open_store(store.default_url(root), root) as st, store.transaction(st) as conn:
        return command.machine_context(command.read_current(conn, tid))


def test_the_design_inputs_are_what_the_plan_would_pass(repo, capsys, last_result, tmp_path):
    tid = _machine(repo, capsys, last_result, tmp_path)
    got = stage_inputs.stage_inputs_of(repo, tid, "design-mvv")
    design = definition.load_definition("standard").stage("設計")
    assert got == invoke.resolve_inputs(design, _context(tid))
    assert got == {"issues": [3959], "worktree": WORKTREE_OUT["worktree"], "mode": "standard", "pace": "normal", "mvv_ref": SHA}


def test_a_recorded_design_pr_is_not_added_to_the_inputs(repo, capsys, last_result, tmp_path):
    tid = _machine(repo, capsys, last_result, tmp_path)
    out_file = tmp_path / "design.json"
    out_file.write_text(
        json.dumps(
            {
                "steps": [],
                "summary": "設計した",
                "outputs": {"design_pr": 4, "mvv_gate": {"gate": "design", "skip": False, "verdict": None, "why": []}},
            }
        )
    )
    _run(capsys, last_result, "finish", tid, "設計", "--output", str(out_file), "--reason", "手で")
    assert _context(tid)["design_pr"] == 4
    assert "design_pr" not in stage_inputs.stage_inputs_of(repo, tid, "design-mvv")


def test_an_unknown_machine_is_refused(repo):
    with pytest.raises(model.Refused, match="無い"):
        stage_inputs.stage_inputs_of(repo, "sprint:none", "design-mvv")


def test_a_machine_of_another_repo_is_refused_before_reading_it(repo, capsys, last_result, tmp_path, monkeypatch):
    tid = _machine(repo, capsys, last_result, tmp_path)
    other = "https://example.com/secret-owner/other.git"
    root = command.store_root_of(Path.cwd())
    with store.open_store(store.default_url(root), root) as st, store.transaction(st) as conn:
        conn.execute(store.MACHINES.update().where(store.MACHINES.c.id == tid).values(repo=other))

    def refuse(*_a, **_k):
        raise AssertionError("別のリポジトリのステートマシンの定義・事象・文脈を読んだ")

    for name in ("read_definition", "read_events"):
        monkeypatch.setattr(store, name, refuse)
    monkeypatch.setattr(invoke, "resolve_inputs", refuse)
    with pytest.raises(model.Refused) as caught:
        stage_inputs.stage_inputs_of(repo, tid, "design-mvv")
    assert "このリポジトリのものでない" in str(caught.value)
    assert "secret-owner" not in str(caught.value) and str(repo) not in str(caught.value)


def test_a_step_the_definition_does_not_have_is_refused(repo, capsys, last_result, tmp_path):
    # どのモードも判定のステップ（design-mvv・release-mvv）を持つ。定義に無いステップの id は断る
    tid = _machine(repo, capsys, last_result, tmp_path, mode="light")
    assert stage_inputs.stage_inputs_of(repo, tid, "design-mvv")["mode"] == "light"
    with pytest.raises(model.Refused, match="no-such-mvv"):
        stage_inputs.stage_inputs_of(repo, tid, "no-such-mvv")


def test_a_context_without_an_input_key_is_missing_it(repo, capsys, last_result):
    # 作業場所の用意を終えていなければ worktree が文脈に無い。プランと同じく判定の入力を組まない
    made = _run(capsys, last_result, "new", "s1", "--mode", "standard", "--issues", "3959")
    with pytest.raises(invoke.StageInputMissing) as caught:
        stage_inputs.stage_inputs_of(repo, made["metrics"]["machine_id"], "design-mvv")
    assert caught.value.keys == ("worktree",)
