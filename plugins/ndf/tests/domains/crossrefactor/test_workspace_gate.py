"""作業場所の保存先・PR ごとの排他・手元の最終ゲートのログの期限（`workspace`。#4566）。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import git_sandbox
import pytest

from ndf.adapters import git
from ndf.domains.crossrefactor import local_gate, store, workspace

DAY = 86_400.0
KEEP = 30 * DAY


def _repo(tmp_path: Path, name: str, monkeypatch: pytest.MonkeyPatch) -> git.Git:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    path = tmp_path / name
    git_sandbox.sh(tmp_path, "init", "-q", "-b", "main", str(path))
    git_sandbox.sh(path, "commit", "-q", "--allow-empty", "-m", "初め")
    return git.Git(path)


def test_without_the_shared_base_the_root_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("NDF_WORKTREE_BASE", raising=False)
    repo = _repo(tmp_path, "a", monkeypatch)
    assert workspace.root_of(repo, 7) == (tmp_path / "a").resolve() / ".worktrees" / ".ndf" / "rf7"


def test_two_repositories_sharing_the_base_get_different_roots(tmp_path, monkeypatch):
    a, b = _repo(tmp_path, "a", monkeypatch), _repo(tmp_path, "b", monkeypatch)
    monkeypatch.setenv("NDF_WORKTREE_BASE", str(tmp_path / "shared"))
    ra, rb = workspace.root_of(a, 7), workspace.root_of(b, 7)
    assert ra != rb
    assert ra.parent.name.startswith("repo-") and ra.parent.parent == tmp_path / "shared"


def test_an_owned_legacy_root_is_kept_and_an_unknown_one_is_not_used(tmp_path, monkeypatch):
    a = _repo(tmp_path, "a", monkeypatch)
    shared = tmp_path / "shared"
    monkeypatch.setenv("NDF_WORKTREE_BASE", str(shared))
    legacy = shared / "rf7"
    store.save(store.path_of(legacy), {"items": [], "repo": "/somewhere/else"})
    assert workspace.root_of(a, 7) != legacy
    store.save(store.path_of(legacy), {"items": [], "repo": str(a.common_dir())})
    assert workspace.root_of(a, 7) == legacy


def test_hold_is_exclusive_and_the_lock_file_is_outside_the_root(tmp_path):
    first = workspace.hold_pr(tmp_path, 7)
    assert first is not None
    assert workspace.hold_pr(tmp_path, 7) is None
    assert (tmp_path / "locks" / "rf7.lock").exists()
    first.release()
    again = workspace.hold_pr(tmp_path, 7)
    assert again is not None
    again.release()


def _log(root: Path, name: str, age: float, now: float, state: dict[str, Any]) -> Path:
    path = workspace.write_private_log(root / "logs", name, "x", state, lambda: store.save(store.path_of(root), state))
    os.utime(path, (now - age, now - age))
    return path


def test_sweep_removes_only_recorded_expired_logs(tmp_path):
    now = 10 * KEEP
    own = tmp_path / "rf7"
    state: dict[str, Any] = {"items": []}
    old = _log(own, "local-gate-0-head-1.log", KEEP + DAY, now, state)
    fresh = _log(own, "local-gate-1-head-1.log", DAY, now, state)
    stray = own / "logs" / "local-gate-9-head-1.log"
    stray.write_text("user", encoding="utf-8")
    os.utime(stray, (0, 0))
    other = own / "logs" / "agent.out"
    other.write_text("a", encoding="utf-8")
    assert workspace.sweep_gate_logs(tmp_path, None, "r", own, KEEP, now) == 1
    assert not old.exists() and fresh.exists() and stray.exists() and other.exists()
    assert [e["name"] for e in store.load(store.path_of(own))["gate_logs"]] == ["local-gate-1-head-1.log"]


def test_sweep_keeps_a_file_put_later_under_the_same_name(tmp_path):
    now = 10 * KEEP
    own = tmp_path / "rf7"
    state: dict[str, Any] = {}
    path = _log(own, "local-gate-0-head-1.log", KEEP + DAY, now, state)
    # ログを移した後に同じ名前へ別のファイルを置く（移したファイルは残るので inode は再利用されない）
    path.rename(tmp_path / "moved.log")
    path.write_text("user", encoding="utf-8")
    os.utime(path, (0, 0))
    assert workspace.sweep_gate_logs(tmp_path, None, "r", own, KEEP, now) == 0
    assert path.exists()


def test_sweep_skips_a_locked_pr_and_removes_the_state_when_only_logs_remain(tmp_path):
    now = 10 * KEEP
    own, other = tmp_path / "rf7", tmp_path / "rf8"
    state8: dict[str, Any] = {}
    _log(other, "local-gate-0-head-1.log", KEEP + DAY, now, state8)
    held = workspace.hold_pr(tmp_path, 8)
    assert workspace.sweep_gate_logs(tmp_path, None, "r", own, KEEP, now) == 0
    held.release()
    assert workspace.sweep_gate_logs(tmp_path, None, "r", own, KEEP, now) == 1
    # 記録がログだけで空になったので、状態ファイルと空のディレクトリも消える。ロックファイルは残る
    assert not other.exists()
    assert (tmp_path / "locks" / "rf8.lock").exists()


def test_sweep_verify_trees_respects_the_lock_kinds(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "a", monkeypatch)
    head = repo.rev_parse("HEAD")
    paths = {k: tmp_path / "rf7" / k for k in ("none", "ndf", "user")}
    for k, p in paths.items():
        repo.run("worktree", "add", "--detach", str(p), head)
    repo.run("worktree", "lock", "--reason", "ndf cross-refactoring pr7 host=h at=t", str(paths["ndf"]))
    repo.run("worktree", "lock", "--reason", "mine", str(paths["user"]))
    stray = tmp_path / "rf7" / "stray"
    stray.mkdir()
    state: dict[str, Any] = {"verify_trees": [str(p) for p in (*paths.values(), stray)]}
    kept = local_gate.sweep_verify_trees(repo, state, lambda: None)
    assert not paths["none"].exists() and not paths["ndf"].exists()
    assert paths["user"].exists() and stray.exists()
    assert len(kept) == 2
    assert json.dumps(state["verify_trees"]) == json.dumps([str(paths["user"]), str(stray)])
