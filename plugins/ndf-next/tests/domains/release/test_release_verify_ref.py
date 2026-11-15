"""検証の ref の組み立て（本物の git と、偽の GitHub。#3572 の決定 9・I9・I12）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import git_sandbox
import pytest

from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import verify_decide, verify_ref

from ._fakes import FakeHub, clone, init_repo, write_json

DECL = decl_mod.VerifyDecl("verify/next", "kit-next")
NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)


def _origin(tmp_path: Path, files: dict[int, tuple[str, str]]) -> Path:
    """main（kit 1.2.3）から切った PR ごとのブランチを `refs/pull/<番号>/head` に置いた origin と、main の clone。"""
    seed = init_repo(tmp_path / "seed")
    write_json(seed / ".claude-plugin" / "marketplace.json", {"name": "kit", "plugins": [{"name": "kit", "source": "./tools/kit"}]})
    write_json(seed / "tools" / "kit" / ".claude-plugin" / "plugin.json", {"name": "kit", "version": "1.2.3"})
    write_json(seed / ".ndf" / "worktree.json", {"base_branch": "main", "production_branch": "main"})
    (seed / "shared.txt").write_text("base\n", encoding="utf-8")
    git_sandbox.sh(seed, "add", "-A")
    git_sandbox.sh(seed, "commit", "-q", "-m", "init")
    git_sandbox.sh(seed, "branch", "-M", "main")
    for number, (rel, text) in files.items():
        git_sandbox.sh(seed, "switch", "-q", "-c", f"feat/{number}", "main")
        (seed / rel).write_text(text, encoding="utf-8")
        git_sandbox.sh(seed, "add", "-A")
        git_sandbox.sh(seed, "commit", "-q", "-m", f"pr {number}")
    git_sandbox.sh(seed, "switch", "-q", "main")
    origin = tmp_path / "origin.git"
    git_sandbox.sh(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    for number in files:
        git_sandbox.sh(origin, "update-ref", f"refs/pull/{number}/head", f"refs/heads/feat/{number}")
    return clone(tmp_path, origin, "work", branch="main")


def _row(number: int, ref: str | None = None, draft: bool = False, repo: str = "o/r") -> dict:
    return {"number": number, "draft": draft, "head": {"sha": "a" * 40, "ref": ref or f"feat/{number}", "repo": {"full_name": repo}}}


def _hub(*rows: dict) -> FakeHub:
    hub = FakeHub()
    hub.responses[("GET", "/repos/o/r/pulls")] = list(rows)
    return hub


def _show(work: Path, path: str) -> str:
    git_sandbox.sh(work, "fetch", "-q", "origin", "+refs/heads/verify/next:refs/remotes/origin/verify/next")
    return git_sandbox.sh(work, "show", f"origin/verify/next:{path}")


def test_build_merges_the_open_prs_renames_the_marketplace_and_sets_the_verify_version(tmp_path: Path) -> None:
    """受け入れ条件 12: main の先端に P1・P2 を入れ、2 つ目のマーケットプレイスの名前と検証の版を持つ。"""
    work = _origin(tmp_path, {1: ("a.txt", "a\n"), 2: ("b.txt", "b\n")})
    built = verify_ref.build(work, _hub(_row(2), _row(1)), DECL, NOW)
    assert built.ref == "verify/next" and built.version == "1.2.3-next.20261115120000"
    assert [n for n, _h in built.included] == [1, 2]
    assert _show(work, "a.txt") == "a\n" and _show(work, "b.txt") == "b\n"
    assert json.loads(_show(work, ".claude-plugin/marketplace.json"))["name"] == "kit-next"
    assert json.loads(_show(work, "tools/kit/.claude-plugin/plugin.json"))["version"] == built.version
    assert json.loads(git_sandbox.sh(work, "show", "origin/main:.claude-plugin/marketplace.json"))["name"] == "kit"
    assert len(git_sandbox.sh(work, "worktree", "list").splitlines()) == 1


def test_rebuilding_is_a_child_of_the_previous_verify_ref(tmp_path: Path) -> None:
    """I12: 組み直しは前の検証の ref の先端を親に持ち、fast-forward でだけ進む。"""
    work = _origin(tmp_path, {1: ("a.txt", "a\n"), 2: ("b.txt", "b\n")})
    first = verify_ref.build(work, _hub(_row(1)), DECL, NOW)
    second = verify_ref.build(work, _hub(_row(1), _row(2)), DECL, NOW.replace(minute=5))
    assert git_sandbox.sh(work, "merge-base", "--is-ancestor", first.head, second.head) == ""
    assert second.version.endswith("120500")


def test_a_conflict_stops_without_moving_the_ref(tmp_path: Path) -> None:
    """受け入れ条件 12: P1 と P2 が衝突すれば組まずに止め、衝突した PR を返し、ref を動かさない。"""
    work = _origin(tmp_path, {1: ("shared.txt", "one\n"), 2: ("shared.txt", "two\n")})
    with pytest.raises(verify_ref.VerifyConflict) as caught:
        verify_ref.build(work, _hub(_row(1), _row(2)), DECL, NOW)
    assert caught.value.number == 2
    assert git_sandbox.sh(work, "ls-remote", "--heads", "origin", "verify/next") == ""
    assert len(git_sandbox.sh(work, "worktree", "list").splitlines()) == 1


def test_included_skips_drafts_the_verify_ref_bump_prs_and_forks() -> None:
    pulls = [
        verify_decide.OpenPull(3, "a", "feat/3", "o/r", False),
        verify_decide.OpenPull(1, "a", "feat/1", "o/r", False),
        verify_decide.OpenPull(4, "a", "feat/4", "o/r", True),
        verify_decide.OpenPull(5, "a", "verify/next", "o/r", False),
        verify_decide.OpenPull(6, "a", "release/v1.3.0", "o/r", False),
        verify_decide.OpenPull(7, "a", "feat/7", "fork/r", False),
    ]
    assert [p.number for p in verify_decide.included(pulls, "verify/next", "o/r")] == [1, 3]


def test_version_of_and_marketplace_of() -> None:
    assert verify_decide.version_of("11.0.0", NOW) == "11.0.0-next.20261115120000"
    assert verify_decide.marketplace_of({"name": "a", "plugins": []}, "a-next") == {"name": "a-next", "plugins": []}
    assert verify_decide.marketplace_of({"plugins": []}, "a-next")["name"] == "a-next"
