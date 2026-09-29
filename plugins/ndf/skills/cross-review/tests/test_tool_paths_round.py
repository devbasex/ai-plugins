"""ツールのパス（#1436）: レビュー worktree のラウンドの開始と、修正の push。

一時の git リポジトリ（bare の origin・メインディレクトリ・`_create_worktree` が作るレビュー worktree）で、
`start-round` の本体（`_sync_worktree(strict=True)`）と `push_fix` を通す。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import review_lib.workspace as workspace
import result_posts
import tool_paths

SERENA = [".serena/project.yml", ".serena/serena_config.yml"]
MARK = "auth_secret: MARK-1436"
HEAD = "feat/x"
# conftest は `state_mod` を使うテストで `push_fix` を送らない偽物へ差し替える。本物を収集の時点で控える
PUSH_FIX = result_posts.push_fix


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _identity(root):
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "commit.gpgsign", "false")


@pytest.fixture
def review(tmp_path, monkeypatch, state_mod):
    """PR の head（feat/x）を持つ origin と、そこからレビュー worktree を作る関数。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    main = tmp_path / "main"
    subprocess.run(["git", "clone", "-q", str(origin), str(main)], check=True, capture_output=True)
    _identity(main)
    monkeypatch.chdir(main)
    monkeypatch.delenv("CROSS_REVIEW_TMP_DIR", raising=False)

    def make(decl=None, tracked=SERENA):
        for rel in [*tracked, "src/a.txt", ".idea/workspace.xml"]:
            write(main, rel, "old\n")
        if decl is not None:
            write(main, ".ndf/worktree.json", json.dumps(decl) if isinstance(decl, dict) else decl)
        git(main, "add", "-A")
        git(main, "commit", "-q", "-m", "init")
        git(main, "push", "-q", "origin", f"HEAD:{HEAD}")
        wt = tmp_path / "wt" / "pr1"
        workspace._create_worktree(str(wt), 1, HEAD)
        _identity(wt)
        return wt

    return type("Review", (), {"make": staticmethod(make), "main": main, "origin": origin})


def head_ref(wt):
    return workspace.HeadRef(branch=HEAD, oid=git(wt, "rev-parse", f"origin/{HEAD}"), is_fork=False)


def start_round(wt):
    workspace._sync_worktree(str(wt), 1, head_ref(wt), strict=True)


def test_only_tool_paths_changed_starts_the_round(review, capsys):
    """受け入れ条件 1（I7: 中身を出さない）。"""
    wt = review.make()
    for rel in SERENA:
        write(wt, rel, f"{MARK}\n")

    start_round(wt)

    err = capsys.readouterr().err
    assert f"{tool_paths.DESCRIBE_HEAD} {' '.join(SERENA)}" in err
    assert "MARK-1436" not in err


def test_unmarked_tool_paths_are_also_left_out(review, capsys):
    """印の無いまま変わっていたツールのパスも外し、ラウンドの後は印が掛かる。"""
    wt = review.make()
    git(wt, "update-index", "--no-skip-worktree", *SERENA)
    write(wt, SERENA[0], f"{MARK}\n")

    start_round(wt)

    assert SERENA[0] in capsys.readouterr().err
    assert tool_paths.hidden(wt, SERENA) == SERENA


def test_user_change_stops_with_tool_paths_on_another_line(review, capsys):
    """受け入れ条件 2。"""
    wt = review.make()
    for rel in SERENA:
        write(wt, rel, f"{MARK}\n")
    write(wt, "src/a.txt", "new\n")

    with pytest.raises(SystemExit) as e:
        start_round(wt)

    assert e.value.code == 8
    lines = [line for line in capsys.readouterr().err.splitlines() if line.startswith(("❌", "↷"))]
    assert lines[0].startswith("❌") and "src/a.txt" in lines[0] and ".serena" not in lines[0]
    assert lines[1] == f"{tool_paths.DESCRIBE_HEAD} {' '.join(SERENA)}"


def test_other_changes_alone_still_stop(review):
    """受け入れ条件 8。"""
    wt = review.make()
    write(wt, "src/a.txt", "new\n")
    with pytest.raises(SystemExit) as e:
        start_round(wt)
    assert e.value.code == 8


def test_added_path_is_a_tool_path(review, capsys):
    """受け入れ条件 6: 足したパスも同じに扱う。"""
    wt = review.make({"version": 1, "tool_paths": {"add": [".idea/workspace.xml"]}})
    write(wt, ".idea/workspace.xml", "new\n")
    start_round(wt)
    assert ".idea/workspace.xml" in capsys.readouterr().err


def test_removed_default_is_a_user_change(review):
    """受け入れ条件 7。"""
    wt = review.make({"version": 1, "tool_paths": {"remove": [SERENA[0]]}})
    write(wt, SERENA[0], "new\n")
    with pytest.raises(SystemExit) as e:
        start_round(wt)
    assert e.value.code == 8


def test_unreadable_tool_paths_stop_init_and_start_round(review, capsys):
    """I2: init は 1、start-round は 8 で止まり、既定へ戻らない。"""
    with pytest.raises(SystemExit) as e:
        review.make("{broken")
    assert e.value.code == 1
    assert "tool_paths を読めない" in capsys.readouterr().err
    wt = review.main.parent / "wt" / "pr1"
    with pytest.raises(SystemExit) as e:
        start_round(wt)
    assert e.value.code == 8


def test_sync_to_a_head_that_changes_a_tool_path(review):
    """決定 2: 印とツールのパスの変更があっても、そのパスを変える head へ同期でき、印が掛かり直す。"""
    wt = review.make()
    write(wt, SERENA[0], f"{MARK}\n")
    write(review.main, SERENA[0], "from-head\n")
    git(review.main, "commit", "-q", "-am", "head moves")
    git(review.main, "push", "-q", "origin", f"HEAD:{HEAD}")
    git(wt, "fetch", "-q", "origin", HEAD)

    start_round(wt)

    assert git(wt, "rev-parse", "HEAD") == git(review.main, "rev-parse", "HEAD")
    assert (wt / SERENA[0]).read_text() == "from-head\n"
    assert tool_paths.hidden(wt, SERENA) == SERENA


def test_fix_commit_pushes_without_tool_paths(review):
    """受け入れ条件 4: fix 担当が `git add -A` でコミットしても、push にツールのパスが入らない。"""
    wt = review.make()
    for rel in SERENA:
        write(wt, rel, f"{MARK}\n")
    write(wt, "src/a.txt", "fixed\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "fix")
    sha = git(wt, "rev-parse", "HEAD")

    outcome = PUSH_FIX(wt, HEAD, sha)

    assert outcome.ok is True, outcome.detail
    assert git(review.origin, "rev-parse", HEAD) == sha
    assert git(review.origin, "show", "--name-only", "--format=", sha) == "src/a.txt"


def test_untracked_tool_path_in_the_commit_is_not_pushed(review):
    """決定 6: 追跡対象外のツールのパスがコミットに入ったら push しない。"""
    wt = review.make(tracked=SERENA[:1])
    before = git(review.origin, "rev-parse", HEAD)
    write(wt, SERENA[1], f"{MARK}\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "fix")

    outcome = PUSH_FIX(wt, HEAD, git(wt, "rev-parse", "HEAD"))

    assert (outcome.ok, outcome.pushed) == (False, False)
    assert SERENA[1] in outcome.detail and "MARK-1436" not in outcome.detail
    assert git(review.origin, "rev-parse", HEAD) == before


def test_default_is_the_single_definition_for_cross_review(review, monkeypatch, capsys):
    """受け入れ条件 9（cross-review の経路）: 既定を差し替えるとラウンドの開始の分類が追随する。"""
    wt = review.make()
    write(wt, "src/a.txt", "new\n")
    monkeypatch.setattr(tool_paths, "DEFAULT", ("src/a.txt",))
    start_round(wt)
    assert "src/a.txt" in capsys.readouterr().err
