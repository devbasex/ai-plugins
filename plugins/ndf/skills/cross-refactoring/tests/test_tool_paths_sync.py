"""ツールのパス（#1436）: 同期の前の検査・同期コミット・作業ディレクトリの早送り・push の直前の検査。

一時の git リポジトリ（bare の origin・メインディレクトリ・書き込み用の作業ディレクトリ）で確かめる。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from crossref_helpers import make_state_v2, read_state

SERENA = [".serena/project.yml", ".serena/serena_config.yml"]
MARK = "auth_secret: MARK-1436"
HEAD = "feat/x"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def tp(refactor):
    return sys.modules["tool_paths"]


@pytest.fixture
def repo(tmp_path, monkeypatch, cmd_setup):
    """PR の head（feat/x）を持つ origin と、書き込み用の作業ディレクトリを作る関数。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    main = tmp_path / "main"
    subprocess.run(["git", "clone", "-q", str(origin), str(main)], check=True, capture_output=True)
    git(main, "config", "user.email", "t@e.st")
    git(main, "config", "user.name", "test")
    monkeypatch.chdir(main)

    def make(decl=None, tracked=SERENA):
        for rel in [*tracked, "src.py", "generated/out.py", ".idea/workspace.xml"]:
            write(main, rel, "old\n")
        if decl is not None:
            write(main, ".ndf/worktree.json", json.dumps(decl))
        git(main, "add", "-A")
        git(main, "commit", "-q", "-m", "init")
        git(main, "push", "-q", "origin", f"HEAD:{HEAD}")
        work = tmp_path / "rf1" / "work"
        cmd_setup._ensure_work_worktree(work, HEAD)
        git(work, "config", "user.email", "t@e.st")
        git(work, "config", "user.name", "test")
        return work

    return type("Repo", (), {"make": staticmethod(make), "main": main, "origin": origin})


def _state(tmp_path, work, command="printf 'x = 2\\n' > generated/out.py"):
    return read_state(make_state_v2(tmp_path, work, sync_command=command, head_branch=HEAD))


def _rewrite(work, paths=SERENA):
    for rel in paths:
        write(work, rel, f"{MARK}\n")


def test_sync_goes_on_and_leaves_tool_paths_out_of_the_commit(repo, publish, tmp_path, capsys):
    """受け入れ条件 3（I7: 中身を出さない）。"""
    work = repo.make()
    _rewrite(work)
    before = git(work, "rev-parse", "HEAD")

    publish._sync_generated(_state(tmp_path, work))

    assert git(work, "rev-parse", "HEAD^") == before
    assert git(work, "show", "--name-only", "--format=", "HEAD") == "generated/out.py"
    assert "MARK-1436" not in capsys.readouterr().err


def test_unmarked_tool_paths_are_left_out_too(repo, publish, tmp_path, capsys):
    """印の無いまま変わっていたツールのパスも、検査と同期コミットから外し、外したことを 1 行出す。"""
    work = repo.make()
    git(work, "update-index", "--no-skip-worktree", *SERENA)
    _rewrite(work)

    publish._sync_generated(_state(tmp_path, work))

    assert git(work, "show", "--name-only", "--format=", "HEAD") == "generated/out.py"
    assert f"↷ ツールのパスを検査から外した: {' '.join(SERENA)}" in capsys.readouterr().err
    assert (work / SERENA[0]).read_text() == f"{MARK}\n"


def test_other_changes_still_abort(repo, worktree, tmp_path):
    """受け入れ条件 8。"""
    work = repo.make()
    write(work, "src.py", "new\n")
    with pytest.raises(SystemExit):
        worktree._require_clean_worktree(_state(tmp_path, work), str(work))


def test_added_path_is_a_tool_path_and_removed_one_is_not(repo, worktree, tmp_path):
    """受け入れ条件 6・7（cross-refactoring の経路）。"""
    work = repo.make({"version": 1, "tool_paths": {"add": [".idea/workspace.xml"], "remove": [SERENA[0]]}})
    write(work, ".idea/workspace.xml", "new\n")
    write(work, SERENA[0], "new\n")
    assert worktree._dirty_paths(_state(tmp_path, work), str(work)) == [SERENA[0]]


def test_unreadable_tool_paths_abort(repo, worktree, tmp_path):
    """I2: 読めない設定で既定へ戻らずに中断する。"""
    work = repo.make({"version": 1})
    write(work, ".ndf/worktree.json", json.dumps({"version": 2, "tool_paths": {"add": ["x"]}}))
    with pytest.raises(SystemExit):
        worktree._dirty_paths(_state(tmp_path, work), str(work))


def test_fast_forward_to_a_head_that_changes_a_tool_path(repo, cmd_setup, tp):
    """決定 2: 印とツールのパスの変更があっても早送りでき、印が掛かり直す。"""
    work = repo.make()
    _rewrite(work, SERENA[:1])
    write(repo.main, SERENA[0], "from-head\n")
    git(repo.main, "commit", "-q", "-am", "head moves")
    git(repo.main, "push", "-q", "origin", f"HEAD:{HEAD}")

    cmd_setup._sync_work_worktree(work, HEAD)

    assert git(work, "rev-parse", "HEAD") == git(repo.main, "rev-parse", "HEAD")
    assert (work / SERENA[0]).read_text() == "from-head\n"
    assert tp.hidden(str(work), SERENA) == SERENA


def test_push_stops_when_a_tool_path_is_committed(repo, publish, patch_lib, tmp_path):
    """I4: 追跡対象外のツールのパスがコミットに入っていたら push しない。"""
    patch_lib("publish_plan_comment", lambda state: None)
    work = repo.make(tracked=SERENA[:1])
    before = git(repo.origin, "rev-parse", HEAD)
    _rewrite(work, SERENA[1:])
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "item")

    with pytest.raises(SystemExit):
        publish.push_head(_state(tmp_path, work, command=""))

    assert git(repo.origin, "rev-parse", HEAD) == before


def test_push_goes_on_without_tool_paths(repo, publish, patch_lib, tmp_path):
    patch_lib("publish_plan_comment", lambda state: None)
    work = repo.make()
    _rewrite(work)
    write(work, "src.py", "new\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "item")
    state = _state(tmp_path, work, command="")
    # 送るコミットは記録した改善項目のもの（記録の無いコミットは push の直前の照合で止まる。#817）
    state["items"] = [
        {"id": "I-001", "status": "verified", "commits": {"test": None, "implement": git(work, "rev-parse", "HEAD"), "fix": []}}
    ]

    publish.push_head(state)

    assert git(repo.origin, "rev-parse", HEAD) == git(work, "rev-parse", "HEAD")
    assert git(repo.origin, "show", "--name-only", "--format=", HEAD) == "src.py"


def test_default_is_the_single_definition_for_cross_refactoring(repo, worktree, tp, tmp_path, monkeypatch):
    """受け入れ条件 9（cross-refactoring の経路）: 既定を差し替えると同期の前の分類が追随する。"""
    work = repo.make()
    write(work, "src.py", "new\n")
    monkeypatch.setattr(tp, "DEFAULT", ("src.py",))
    assert worktree._dirty_paths(_state(tmp_path, work), str(work)) == []
