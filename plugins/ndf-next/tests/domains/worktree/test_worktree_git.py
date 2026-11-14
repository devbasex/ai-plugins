"""作業場所の用意と片付けを、一時の git のリポジトリで確かめる。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Iterable

import pytest

from ndf import identity
from ndf.adapters.git import Completed, Git
from ndf.adapters.github import Pull
from ndf.domains.worktree import cleanup, cleanup_decide, create, repo


def _pull(branch: str, state: str, merged: bool) -> Pull:
    return Pull(1, state, merged, "0" * 40, branch, "develop", "", "clean", "u", "MEMBER")


class FakeHub:
    """ブランチごとの PR を返す差し替え。読んだブランチを覚える。"""

    def __init__(self, pulls: dict[str, list[Pull]]) -> None:
        self.pulls = pulls
        self.asked: list[list[str]] = []

    def pulls_by_head(self, branches: Iterable[str]) -> dict[str, list[Pull]]:
        wanted = list(branches)
        self.asked.append(wanted)
        return {b: self.pulls[b] if b in self.pulls else [] for b in wanted}


def test_create_new_branch_from_start(main_repo: Path) -> None:
    got = create.create(main_repo, "feat/a")
    assert got.path == main_repo / ".worktrees" / "feat" / "a"
    assert (got.base, got.start_ref, got.new_branch, got.warnings) == ("develop", "origin/develop", True, [])
    where = repo.locate(Git(got.path))
    assert where.main_dir == main_repo.resolve() and where.in_worktree
    assert repo.current_branch(Git(got.path)) == "feat/a"


def test_create_uses_production_channel_and_explicit_start(make_repo: Callable, run_git: Callable) -> None:
    main = make_repo({"version": 1, "base_branch": "develop", "production_branch": "main", "branch_flow": {"start": "production"}})
    assert create.create(main, "sprint/s").base == "main"
    assert create.create(main, "feat/b", start="sprint/s").start_ref == "sprint/s"


def test_create_reuses_existing_branch(main_repo: Path, run_git: Callable) -> None:
    run_git(main_repo, "branch", "feat/old")
    got = create.create(main_repo, "feat/old")
    assert (got.new_branch, got.start_ref) == (False, "feat/old")


def test_create_without_declaration_uses_origin_default(make_repo: Callable) -> None:
    main = make_repo(None)
    assert create.create(main, "feat/c").base == "develop"


@pytest.mark.parametrize(
    ("setup", "branch", "kind"),
    [
        ("none", "bad..name", "invalid"),
        ("none", "-x", "invalid"),
        ("exists", "feat/a", "refused"),
        ("unignored", "feat/a", "precondition"),
        ("none", "feat/a@missing", "precondition"),
    ],
)
def test_create_refuses(main_repo: Path, setup: str, branch: str, kind: str) -> None:
    start = None
    if setup == "exists":
        (main_repo / ".worktrees" / "feat" / "a").mkdir(parents=True)
    if setup == "unignored":
        (main_repo / ".gitignore").write_text("", encoding="utf-8")
    if branch.endswith("@missing"):
        branch, start = branch.split("@")[0], "no-such-branch"
    with pytest.raises(create.CreateError) as err:
        create.create(main_repo, branch, start=start)
    assert err.value.kind == kind


def test_create_warns_when_fetch_fails(main_repo: Path, run_git: Callable) -> None:
    run_git(main_repo, "remote", "set-url", "origin", str(main_repo.parent / "gone.git"))
    got = create.create(main_repo, "feat/offline")
    assert got.start_ref == "origin/develop" and len(got.warnings) == 1


def test_list_only_dev_worktrees(main_repo: Path) -> None:
    create.create(main_repo, "feat/a")
    items = repo.dev_worktrees(main_repo.resolve(), Git(main_repo).worktrees())
    assert [w.branch for w in items] == ["feat/a"]


def test_cleanup_removes_only_merged_clean_worktrees(main_repo: Path, run_git: Callable) -> None:
    merged = create.create(main_repo, "feat/merged").path
    squashed = create.create(main_repo, "feat/squashed").path
    (squashed / "s.txt").write_text("s", encoding="utf-8")
    run_git(squashed, "add", "s.txt")
    run_git(squashed, "commit", "-q", "-m", "s")
    dirty = create.create(main_repo, "feat/dirty").path
    (dirty / "new.txt").write_text("x", encoding="utf-8")
    opened = create.create(main_repo, "feat/open").path
    create.create(main_repo, "feat/nopr")
    hub = FakeHub({
        "feat/merged": [_pull("feat/merged", "closed", True)],
        "feat/squashed": [_pull("feat/squashed", "closed", True)],
        "feat/dirty": [_pull("feat/dirty", "closed", True)],
        "feat/open": [_pull("feat/open", "open", False)],
    })  # fmt: skip

    dry = {o.branch: o for o in cleanup.cleanup(main_repo, opened, hub, dry_run=True)}
    assert dry["feat/merged"].verdict == cleanup_decide.REMOVE and not dry["feat/merged"].removed
    assert merged.exists()
    # 変更の残る worktree と今の位置の worktree は PR を読まない
    assert hub.asked == [["feat/merged", "feat/nopr", "feat/squashed"]]

    got = {o.branch: o for o in cleanup.cleanup(main_repo, opened, hub)}
    assert got["feat/merged"].removed and got["feat/merged"].branch_deleted and not merged.exists()
    # squash マージは git がマージ済みと認めないので、ブランチを残して理由を返す（-D は人が決める）
    assert got["feat/squashed"].removed and not got["feat/squashed"].branch_deleted and got["feat/squashed"].note
    assert run_git(main_repo, "branch", "--list", "feat/squashed").strip()
    assert (got["feat/dirty"].verdict, got["feat/open"].verdict, got["feat/nopr"].verdict) == (
        cleanup_decide.KEEP_DIRTY, cleanup_decide.KEEP_CURRENT, cleanup_decide.KEEP_NO_PR,
    )  # fmt: skip
    assert dirty.exists() and opened.exists()
    item = got["feat/squashed"].item()
    assert json.loads(json.dumps(item))["reason"] == cleanup_decide.REASONS[cleanup_decide.REMOVE]


def test_cleanup_evacuates_ignored_files_before_removing(main_repo: Path, run_git: Callable) -> None:
    """`git worktree remove` は無視したファイルを黙って消すため、cleanup でも先に退避する。"""
    from ndf.domains.worktree import trash

    wt = create.create(main_repo, "feat/ign").path
    (wt / ".gitignore").write_text(".env\n", encoding="utf-8")
    run_git(wt, "add", ".gitignore")
    run_git(wt, "commit", "-q", "-m", "ignore")
    run_git(main_repo, "merge", "-q", "--no-ff", "-m", "merge", "feat/ign")
    (wt / ".env").write_text("SECRET_NAME=1", encoding="utf-8")
    hub = FakeHub({"feat/ign": [_pull("feat/ign", "closed", True)]})
    [got] = cleanup.cleanup(main_repo, main_repo, hub)
    assert got.removed and not wt.exists() and got.note.startswith("退避先 ")
    dest = Path(got.note.split(" ", 1)[1].split("。", 1)[0])
    assert dest.parent == trash.trash_root(Git(main_repo)) and (dest / ".env").read_text(encoding="utf-8") == "SECRET_NAME=1"


def test_cleanup_restores_evacuated_files_when_git_refuses(main_repo: Path, run_git: Callable, monkeypatch: pytest.MonkeyPatch) -> None:
    from ndf.domains.worktree import trash

    wt = create.create(main_repo, "feat/back").path
    (wt / ".gitignore").write_text("notes.txt\n", encoding="utf-8")
    run_git(wt, "add", ".gitignore")
    run_git(wt, "commit", "-q", "-m", "back")
    run_git(main_repo, "merge", "-q", "--no-ff", "-m", "merge", "feat/back")
    (wt / "notes.txt").write_text("keep me", encoding="utf-8")
    real = Git.attempt

    def refuse(self: Git, *args: str) -> Completed:
        return Completed(1, "", "refused") if args[:2] == ("worktree", "remove") else real(self, *args)

    monkeypatch.setattr(Git, "attempt", refuse)
    [got] = cleanup.cleanup(main_repo, main_repo, FakeHub({"feat/back": [_pull("feat/back", "closed", True)]}))
    assert not got.removed and "worktree へ戻した" in got.note
    assert (wt / "notes.txt").read_text(encoding="utf-8") == "keep me" and trash.candidates(Git(main_repo), "develop") == []


def test_cleanup_keeps_selected_sprint_until_production(main_repo: Path, run_git: Callable) -> None:
    """選んで集める取り込み方のスプリントは、merged と同じく本番チャネルに含まれるまで cleanup でも消さない。"""
    decl = {"version": 1, "base_branch": "develop", "production_branch": "main", "branch_flow": {"promotion": "selected"}}
    (main_repo / ".ndf" / "worktree.json").write_text(json.dumps(decl), encoding="utf-8")
    run_git(main_repo, "add", "-A")
    run_git(main_repo, "commit", "-q", "-m", "declare")
    wt = create.create(main_repo, "sprint/s9").path
    (wt / "s.txt").write_text("s", encoding="utf-8")
    run_git(wt, "add", "s.txt")
    run_git(wt, "commit", "-q", "-m", "s")
    run_git(main_repo, "merge", "-q", "--no-ff", "-m", "merge", "sprint/s9")
    hub = FakeHub({"sprint/s9": [_pull("sprint/s9", "closed", True)]})
    [got] = cleanup.cleanup(main_repo, main_repo, hub)
    assert got.verdict == cleanup_decide.KEEP_SPRINT and not got.removed and wt.exists() and got.note
    assert run_git(main_repo, "branch", "--list", "sprint/s9").strip()


def test_cleanup_records_merge_commit_for_evacuated_files(main_repo: Path, run_git: Callable) -> None:
    """squash のマージでは先端が本番に入らないため、退避の台帳へ PR のマージコミットも書く（回収の候補になる）。"""
    from ndf.domains.worktree import trash

    wt = create.create(main_repo, "feat/sq").path
    (wt / ".gitignore").write_text(".env\n", encoding="utf-8")
    run_git(wt, "add", ".gitignore")
    run_git(wt, "commit", "-q", "-m", "ignore")
    run_git(main_repo, "merge", "-q", "--no-ff", "-m", "merge", "feat/sq")
    merge = run_git(main_repo, "rev-parse", "HEAD").strip()
    (wt / ".env").write_text("A=1", encoding="utf-8")
    pull = _pull("feat/sq", "closed", True)._replace(merge_sha=merge)
    [got] = cleanup.cleanup(main_repo, main_repo, FakeHub({"feat/sq": [pull]}))
    dest = Path(got.note.split(" ", 1)[1].split("。", 1)[0])
    assert json.loads(trash.ledger_of(dest).read_text(encoding="utf-8"))["merge_commit"] == merge


# 現状固定（I-022）: 状態ファイル（排他・台帳・退避先）の置き場は、どこから呼んでも共通の git ディレクトリの実体の下。
def test_state_files_live_under_common_git_dir_from_main_and_worktree(main_repo: Path, tmp_path: Path) -> None:
    from ndf.domains.worktree import ledger, mainlock, trash

    wt = create.create(main_repo, "feat/state").path
    link = tmp_path / "link"
    link.symlink_to(main_repo)
    common = Path(os.path.realpath(main_repo / ".git"))
    for where in (main_repo, wt, link):
        g = Git(where)
        assert mainlock.lock_file(g) == common / "ndf-main.lock"
        assert ledger.ledger_path(g) == common / identity.load().name / "worktree-registry.json"
        assert trash.trash_root(g) == common / identity.load().name / "worktree-trash"


def test_state_files_propagate_git_failure(tmp_path: Path) -> None:
    from ndf.adapters.git import GitError
    from ndf.domains.worktree import ledger, mainlock, trash

    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    for find in (mainlock.lock_file, ledger.ledger_path, trash.trash_root):
        with pytest.raises(GitError):
            find(Git(outside))
