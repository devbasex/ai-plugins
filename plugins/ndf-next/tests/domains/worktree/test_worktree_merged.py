"""マージの後の後片付け・退避・ブランチの追従・宣言の導入を、一時の git のリポジトリで確かめる。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable, Optional

import pytest

from ndf.adapters.git import Git
from ndf.adapters.github import Pull
from ndf.domains.worktree import create, declaration, follow, merged, setup, trash


def _pull(number: int, branch: str, merged_: bool) -> Pull:
    return Pull(number, "closed", merged_, "0" * 40, branch, "develop", "", "clean", "u", "MEMBER")


class FakeHub:
    def __init__(self, pulls: dict[int, Optional[Pull]]) -> None:
        self.by_number = pulls

    def pulls(self, numbers: Iterable[int]) -> dict[int, Optional[Pull]]:
        return {n: self.by_number[n] if n in self.by_number else None for n in numbers}


def _land(main_repo: Path, run_git: Callable, branch: str) -> Path:
    """worktree を作ってコミットし、origin の develop へマージした状態にする（メインディレクトリは取り込む前）。"""
    wt = create.create(main_repo, branch).path
    (wt / f"{branch.replace('/', '-')}.txt").write_text("x", encoding="utf-8")
    run_git(wt, "add", "-A")
    run_git(wt, "commit", "-q", "-m", branch)
    origin = main_repo.parent / "origin.git"
    clone = main_repo.parent / f"clone-{branch.replace('/', '-')}"
    run_git(main_repo.parent, "clone", "-q", "-b", "develop", str(origin), str(clone))
    run_git(clone, "fetch", "-q", str(main_repo), f"{branch}:{branch}")
    run_git(clone, "merge", "-q", "--no-ff", "-m", f"merge {branch}", branch)
    run_git(clone, "push", "-q", "origin", "develop")
    run_git(main_repo, "fetch", "-q", "origin")
    return wt


def test_after_merge_removes_worktree_and_branch_then_fast_forwards(main_repo: Path, run_git: Callable) -> None:
    wt = _land(main_repo, run_git, "feat/a")
    (wt / "notes.txt").write_text("keep me", encoding="utf-8")  # 追跡していないファイルは退避してから外す
    (wt / "__pycache__").mkdir()
    (wt / "__pycache__" / "m.pyc").write_text("", encoding="utf-8")
    run_git(main_repo, "update-ref", "refs/remotes/origin/develop", "develop")  # 取得済みの参照を古くし、取り込みが fetch を読むことを確かめる

    rec = merged.after_merge(main_repo, [7, 8, 9], FakeHub({7: _pull(7, "feat/a", True), 8: _pull(8, "feat/b", False)}))

    assert not wt.exists() and not run_git(main_repo, "branch", "--list", "feat/a").strip()
    got = {(i["kind"], i["name"]): i for i in rec.items}
    assert got[("branch", "feat/a")]["result"] == "deleted" and got[("branch", "feat/a")]["restore"].startswith("git branch feat/a ")
    assert got[("pr", "8")]["result"] == "kept" and got[("pr", "9")]["result"] == "kept"
    assert got[("main_dir", str(main_repo))]["result"] == "pulled" and rec.stopped is None
    assert (main_repo / "feat-a.txt").exists()
    [cand] = trash.candidates(Git(main_repo), "origin/develop")
    removed = got[("worktree", str(wt))]
    assert cand.branch == "feat/a" and cand.released and cand.name in removed["reason"] and "__pycache__" in removed["reason"]
    assert (trash.trash_root(Git(main_repo)) / cand.name / "notes.txt").read_text(encoding="utf-8") == "keep me"
    # 消すのは承認した名前だけ
    assert trash.sweep(Git(main_repo), "origin/develop", ["other"]) == []
    assert trash.sweep(Git(main_repo), "origin/develop", [cand.name]) == [cand.name]
    assert trash.candidates(Git(main_repo), "origin/develop") == []


def test_after_merge_keeps_tracked_changes_and_main_dir_off_base(main_repo: Path, run_git: Callable) -> None:
    wt = _land(main_repo, run_git, "feat/c")
    (wt / "feat-c.txt").write_text("changed", encoding="utf-8")
    run_git(main_repo, "checkout", "-q", "main")
    rec = merged.after_merge(main_repo, [1], FakeHub({1: _pull(1, "feat/c", True)}))
    results = {i["kind"]: i["result"] for i in rec.items}
    assert results == {"worktree": "kept", "main_dir": "kept"} and wt.exists()


def test_sprint_branch_uses_start_record_over_declaration(main_repo: Path, run_git: Callable) -> None:
    """宣言（whole）を変えても、開始時の記録（selected）で始めたスプリントブランチは本番に入るまで残す（#3310）。"""
    wt = _land(main_repo, run_git, "sprint/s1")
    record = {"promotion": "selected", "production_branch": "main"}
    rec = merged.after_merge(main_repo, [2], FakeHub({2: _pull(2, "sprint/s1", True)}), flow_record=record)
    assert [i["result"] for i in rec.items if i["kind"] == "branch"] == ["kept"] and wt.exists()
    rec = merged.after_merge(main_repo, [2], FakeHub({2: _pull(2, "sprint/s1", True)}))
    assert [i["result"] for i in rec.items if i["kind"] == "branch"] == ["deleted"] and not wt.exists()


def test_read_flow_record(tmp_path: Path) -> None:
    path = tmp_path / "flow.json"
    path.write_text(json.dumps({"promotion": "selected"}), encoding="utf-8")
    assert merged.read_flow_record(path) == {"promotion": "selected"}
    assert merged.read_flow_record(None) is None


def test_follow_detaches_main_dir_to_single_worktree(make_repo: Callable, run_git: Callable) -> None:
    main = make_repo({"version": 1, "base_branch": "develop", "follow_branch": True})
    wt = create.create(main, "feat/f").path
    (wt / "f.txt").write_text("f", encoding="utf-8")
    run_git(wt, "add", "-A")
    run_git(wt, "commit", "-q", "-m", "f")
    text = follow.follow(main, declaration.load(main))
    assert text is not None and "feat/f" in text
    assert Git(main).rev_parse("HEAD") == Git(wt).rev_parse("HEAD")
    assert follow.follow(main, declaration.load(main)) is None  # 合っていれば動かさない


def test_follow_does_nothing_without_flag_or_with_changes(make_repo: Callable) -> None:
    plain = make_repo({"version": 1, "base_branch": "develop"})
    create.create(plain, "feat/g")
    before = Git(plain).rev_parse("HEAD")
    assert follow.follow(plain, declaration.load(plain)) is None
    (plain / ".gitignore").write_text("changed\n", encoding="utf-8")
    (plain / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "follow_branch": True}), encoding="utf-8")
    assert follow.follow(plain, declaration.load(plain)) is None  # 未コミットの変更があれば動かさない
    assert Git(plain).rev_parse("HEAD") == before


def test_setup_init_and_state(make_repo: Callable) -> None:
    main = make_repo(None)
    assert setup.state(main).state == setup.ABSENT
    got = setup.init(main)
    assert got.written and setup.state(main).state == setup.PRESENT
    assert (main / ".ndf" / ".gitignore").read_text(encoding="utf-8").splitlines()[-1] == "worktree.local.json"
    assert not setup.init(main).written  # 既にある宣言は上書きしない
    (main / ".ndf" / "worktree.json").write_text("{", encoding="utf-8")
    assert setup.state(main).state == setup.UNREADABLE
    with pytest.raises(setup.SetupError):
        setup.init(main)  # 読めない宣言は書き直さない
    assert setup.init(main, force=True).written
