"""旧い宣言の検査（`ndf.core.branch_strategy_legacy`。#3672 の決定 10・I4・I8）の単体テスト。"""

from __future__ import annotations

import json

import pytest

from ndf import contracts
from ndf.core import branch_strategy_legacy as legacy


@pytest.fixture
def other_contracts(tmp_path):
    """契約の置き場を一時のディレクトリへ替え、終わったらプラグインの置き場へ戻す。"""
    contracts.use_dir(tmp_path)
    yield tmp_path
    contracts.use_dir(None)


def test_load_moves_only_section_rows():
    moves = legacy.load_moves()
    olds = {(m.file, m.old) for m in moves}
    assert ("worktree", "/branch_flow/promotion") in olds and ("worktree", "/branch_flow/release_strategy") in olds
    assert all(m.new is None or m.new.startswith("/branch_strategy") for m in moves)
    assert all(m.file in legacy.FILES for m in moves)


@pytest.mark.parametrize("text", ["{}", '{"rows": {}}', '{"rows": [{"file": "worktree", "new": null}]}', "{"])
def test_load_moves_rejects_broken_table(other_contracts, text):
    (other_contracts / "data").mkdir()
    (other_contracts / legacy.MOVES).write_text(text, encoding="utf-8")
    with pytest.raises(legacy.LegacyTableError):
        legacy.load_moves()


def test_load_moves_reads_a_given_table(other_contracts):
    rows = [
        {"id": "a", "file": "worktree", "old": "/x", "new_file": "project", "new": "/branch_strategy/branches/base"},
        {"id": "b", "file": "worktree", "old": "/y", "new_file": "worktree", "new": "/z"},
        {"id": "c", "file": "supervise", "old": "/release", "new_file": None, "new": None},
    ]
    (other_contracts / "t.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
    assert [m.id for m in legacy.load_moves("t.json")] == ["a", "c"]


def test_find_legacy_reports_the_child_once():
    files = {
        "worktree": {"base_branch": "develop", "branch_flow": {"promotion": "whole", "start": "base"}},
        "project": {"production_merge": {"admin": True}, "delivery": []},
        "supervise": {"release": {"form": "package-plugin", "dev_workflow": "d.yml"}},
    }
    found = {(f.file, f.pointer) for f in legacy.find_legacy(files, legacy.load_moves())}
    assert {("worktree.json", "/branch_flow/promotion"), ("worktree.json", "/base_branch"), ("project.json", "/delivery")} <= found
    assert ("worktree.json", "/branch_flow") not in found and ("supervise.json", "/release") not in found
    assert legacy.find_legacy({"project": {"branch_strategy": {}}}, legacy.load_moves()) == []


def test_legacy_reason_names_file_pointer_and_command():
    found = [legacy.Found("worktree.json", "/branch_flow/promotion"), legacy.Found("release.json", "/version_files")]
    assert legacy.legacy_reason(found, "11") == (
        "`.ndf/worktree.json` の `/branch_flow/promotion`、`.ndf/release.json` の `/version_files` は旧い宣言のキーです。"
        "`ndf project branch-strategy migrate` で移してください"
    )
    assert 'project-decl.py" branch-strategy migrate' in legacy.legacy_reason(found[:1], "10")
