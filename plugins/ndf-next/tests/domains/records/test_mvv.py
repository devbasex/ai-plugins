"""MVV の記録: worktree の鍵（#2603）・照合の再利用（#2614）・覆しの数え方（#2777）・数える版（#2970）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import git_sandbox
import pytest

from ndf import identity
from ndf.domains.records import jsonl, mvv
from ndf.domains.records import mvv_decide as md


def _decl(version: int, limits: dict | None = None) -> dict:
    out: dict[str, Any] = {"body": "mvv.md", "versions": [{"version": version, "sha256": "s"}]}
    if limits is not None:
        out["settings"] = {md.REVISE_AFTER: limits}
    return out


def test_override_kind_does_not_count_a_passed_unknown_as_an_override() -> None:
    assert md.override_kind(md.FOLLOW, approved=False) == md.OVERRIDE_REJECT
    assert md.override_kind(md.NOT_FOLLOW, approved=True) == md.OVERRIDE_PASS
    assert md.override_kind(md.UNKNOWN, approved=True) == md.OVERRIDE_UNKNOWN_PASS
    assert md.override_kind(md.FOLLOW, approved=True) is None


def test_signal_kinds_count_every_unknown_whatever_the_answer() -> None:
    assert md.signal_kinds(md.UNKNOWN, approved=True) == [md.UNKNOWN, md.OVERRIDE_UNKNOWN_PASS]
    assert md.signal_kinds(md.UNKNOWN, approved=False) == [md.UNKNOWN]
    assert md.signal_kinds(md.FOLLOW, approved=True) == []


def test_count_signals_dedupes_pairs_and_reads_only_the_current_version_and_repo() -> None:
    row = {"repo": "/m", "version": 2, "kind": md.OVERRIDE_PASS, "state": "s", "gate": "g", "pr": 1}
    rows = [row, dict(row), {**row, "pr": 2}, {**row, "version": 1, "pr": 3}, {**row, "repo": "/other", "pr": 4},
            {**row, "kind": md.OVERRIDE_UNKNOWN_PASS, "pr": 5}]  # fmt: skip
    got = md.count_signals(rows, _decl(2, {"overrides": 2}), "/m")
    assert (got.version, got.overrides, got.unknown_pass, got.unknown, got.revise) == (2, 2, 1, 0, True)
    assert md.count_signals(rows, _decl(2), "/m").revise is False


def test_status_compares_the_body_with_the_approved_record() -> None:
    assert md.status(None, None) == md.State.ABSENT
    assert md.status({"versions": []}, "s") == md.State.UNAPPROVED
    assert md.status(_decl(1), "s") == md.State.APPROVED
    assert md.status(_decl(1), "t") == md.State.MISMATCH
    assert md.status(_decl(1), None) == md.State.UNREADABLE


def test_reusable_takes_only_the_latest_follow_for_the_same_key() -> None:
    key = md.vet_key({"sprint": "a", "project": "b"})
    assert key == md.vet_key({"project": "b", "sprint": "a"}) and key != md.vet_key({"sprint": "a", "project": "c"})
    first = md.judged_row(key, md.FOLLOW, "t1", "/m", {"usd": 0.1})
    assert md.reusable([first], key) == first
    assert md.reusable([first, md.judged_row(key, md.UNKNOWN, "t2", "/m", {})], key) is None
    again = md.reuse_row(first, "t3", "/w")
    assert again["reused_from"] == "t1" and "usd" not in again
    assert md.reuse_row(again, "t4", "/w")["reused_from"] == "t1"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
    origin, main = tmp_path / "origin.git", tmp_path / "main"
    git_sandbox.sh(tmp_path, "init", "-q", "--bare", str(origin))
    git_sandbox.sh(tmp_path, "init", "-q", "-b", "main", str(main))
    (main / ".ndf").mkdir()
    for branch, version in (("main", 1), ("develop", 2)):
        if branch == "develop":
            git_sandbox.sh(main, "checkout", "-q", "-b", "develop")
        (main / ".ndf" / "mvv.json").write_text(json.dumps(_decl(version)), encoding="utf-8")
        git_sandbox.sh(main, "add", "-A")
        git_sandbox.sh(main, "commit", "-q", "-m", branch)
    git_sandbox.sh(main, "remote", "add", "origin", str(origin))
    git_sandbox.sh(main, "push", "-q", "origin", "main", "develop")
    git_sandbox.sh(main, "fetch", "-q", "origin")
    git_sandbox.sh(main, "worktree", "add", "-q", "-b", "w", str(main / ".worktrees" / "w"))
    return main


def test_repo_key_is_the_main_directory_from_a_worktree(repo: Path) -> None:
    assert mvv.repo_key(repo / ".worktrees" / "w") == mvv.repo_key(repo) == str(repo.resolve())


def test_repo_key_outside_git_raises(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(mvv.MvvError):
        mvv.repo_key(plain)


def test_signals_count_with_the_base_branch_version_from_a_worktree(repo: Path) -> None:
    wt = repo / ".worktrees" / "w"
    for pr in (1, 2):
        mvv.record_answer(wt, {"verdict": md.NOT_FOLLOW, "approved": True, "state": "s", "gate": "g", "pr": pr}, 2)
    assert mvv.record_answer(wt, {"verdict": md.FOLLOW, "approved": True}, 2) == []
    mvv.record_answer(repo, {"verdict": md.UNKNOWN, "approved": True, "pr": 9}, 2)
    mvv.record_answer(repo, {"verdict": md.UNKNOWN, "approved": False, "pr": 10}, 2)
    got = mvv.signals(wt, "develop")
    assert (got.version, got.overrides, got.unknown_pass, got.unknown) == (2, 2, 1, 2)
    assert mvv.signals(repo, "main").overrides == 0


def test_record_answer_without_a_version_takes_the_worktree_declaration(repo: Path) -> None:
    wt = repo / ".worktrees" / "w"
    rows = mvv.record_answer(wt, {"verdict": md.NOT_FOLLOW, "approved": True, "pr": 1}, None)
    assert [r["version"] for r in rows] == [2]
    assert mvv.signals(wt, "develop").overrides == 1
    (wt / ".ndf" / "mvv.json").unlink()
    with pytest.raises(mvv.MvvError):
        mvv.record_answer(wt, {"verdict": md.NOT_FOLLOW, "approved": True, "pr": 2}, None)


def test_vet_sprint_reuses_the_latest_follow_and_survives_broken_rows(repo: Path) -> None:
    calls: list[int] = []

    def judge(inputs: Any) -> tuple[str, dict]:
        calls.append(1)
        return md.FOLLOW, {"usd": 0.2, "seconds": 3}

    path = mvv.record_path("mvv-vet")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{壊れた\n", encoding="utf-8")
    inputs = {"sprint": "本文", "project": "MVV", "principles": "原則", "prompt": "指示"}
    first = mvv.vet_sprint(repo, inputs, judge)
    second = mvv.vet_sprint(repo / ".worktrees" / "w", inputs, judge)
    assert len(calls) == 1 and second["reused_from"] == first["at"]
    mvv.vet_sprint(repo, {**inputs, "sprint": "変えた"}, judge)
    mvv.vet_sprint(repo, inputs, judge, reuse=False)
    assert len(calls) == 3
    rows, broken = jsonl.read_rows(path)
    assert len(rows) == 4 and broken == 1


# 現状固定（I-011 の前）: 記録の置き場は NDF_MVV_STATE_DIR → 状態の置き場の順。状態の置き場を解けなければ IdentityError のまま
def test_record_path_prefers_the_env_dir_and_does_not_load_the_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "m"))
    monkeypatch.setattr(identity, "load", _unloadable)
    assert mvv.record_path("mvv-vet") == tmp_path / "m" / "mvv-vet.jsonl"
    assert mvv.record_path("mvv-signals") == tmp_path / "m" / "mvv-signals.jsonl"


@pytest.mark.parametrize("env", [None, ""])
def test_record_path_falls_back_to_the_state_dir_when_the_env_is_unset_or_empty(env: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    if env is None:
        monkeypatch.delenv("NDF_MVV_STATE_DIR", raising=False)
    else:
        monkeypatch.setenv("NDF_MVV_STATE_DIR", env)
    state = identity.load().state_dir
    assert mvv.record_path("mvv-vet") == state / "mvv-vet.jsonl"
    assert mvv.record_path("mvv-signals") == state / "mvv-signals.jsonl"


def _unloadable() -> identity.PluginIdentity:
    raise identity.IdentityError("plugin.json を読めない")


def test_record_path_raises_identity_error_without_the_env_and_the_state_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NDF_MVV_STATE_DIR", raising=False)
    monkeypatch.setattr(identity, "load", _unloadable)
    with pytest.raises(identity.IdentityError):
        mvv.record_path("mvv-vet")
