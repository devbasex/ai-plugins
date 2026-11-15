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


# --- 現状固定（I-003）: main 宛ての開いた PR の読み（ページ送り・行の形）---


class _PagedHub(FakeHub):
    """`pulls` の GET をページの番号で返し、送った params を残す偽物。"""

    def __init__(self, pages: dict[int, object]) -> None:
        super().__init__()
        self.pages = pages
        self.params: list[dict] = []

    def rest(self, method: str, path: str, payload=None, params=None):
        if method == "GET" and path == "/repos/o/r/pulls":
            self.params.append(dict(params))
            return self.pages.get(params["page"], [])
        return super().rest(method, path, payload, params)


def _drafts(start: int, count: int) -> list[dict]:
    return [_row(n, draft=True) for n in range(start, start + count)]


def test_characterize_open_pulls_reads_the_next_page_while_a_page_is_full(tmp_path: Path) -> None:
    """100 行のページの次も読み、draft の鍵が無い行は draft でない・head の repo が無い行は fork と同じに外れる。"""
    work = _origin(tmp_path, {1: ("a.txt", "a\n")})
    one = _row(1)
    del one["draft"]
    no_repo = {"number": 3, "head": {"sha": "b" * 40, "ref": "feat/3", "repo": None}}
    hub = _PagedHub({1: _drafts(100, 100), 2: [one, "not a row", no_repo]})
    built = verify_ref.build(work, hub, DECL, NOW)
    assert [n for n, _h in built.included] == [1]
    assert built.included[0][1] == "a" * 40
    assert hub.params == [
        {"state": "open", "base": "main", "per_page": 100, "page": 1},
        {"state": "open", "base": "main", "per_page": 100, "page": 2},
    ]


def test_characterize_open_pulls_does_not_count_non_object_rows_toward_a_full_page(tmp_path: Path) -> None:
    """オブジェクトでない行はページの行数に数えない（99 行＋1 つの文字列なら次のページを読まない）。"""
    work = _origin(tmp_path, {1: ("a.txt", "a\n")})
    hub = _PagedHub({1: [*_drafts(100, 99), "x"], 2: [_row(1)]})
    built = verify_ref.build(work, hub, DECL, NOW)
    assert built.included == ()
    assert [p["page"] for p in hub.params] == [1]


def test_characterize_open_pulls_reads_a_non_list_response_as_no_pulls(tmp_path: Path) -> None:
    work = _origin(tmp_path, {1: ("a.txt", "a\n")})
    hub = _PagedHub({1: {"message": "not a list"}})
    built = verify_ref.build(work, hub, DECL, NOW)
    assert built.included == () and built.version == "1.2.3-next.20261115120000"
    assert [p["page"] for p in hub.params] == [1]


def test_rewrite_targets_outside_the_temporary_worktree_are_refused(tmp_path: Path) -> None:
    """PR 側の宣言の絶対パス・`..`・外向きのシンボリックリンクを追って一時の worktree の外を書き換えない。"""
    work = tmp_path / "work"
    victim = tmp_path / "victim.json"
    victim.write_text('{"name": "kit", "version": "1.0.0"}\n', encoding="utf-8")
    write_json(work / ".claude-plugin" / "marketplace.json", {"plugins": [{"name": "kit", "source": "./kit"}]})
    (work / "kit" / ".claude-plugin").mkdir(parents=True)
    (work / "kit" / ".claude-plugin" / "plugin.json").symlink_to(victim)
    with pytest.raises(verify_ref.VerifyEscape, match="victim.json"):
        verify_ref._contained(work)
    (work / "kit" / ".claude-plugin" / "plugin.json").unlink()
    write_json(work / "kit" / ".claude-plugin" / "plugin.json", {"name": "kit", "version": "1.0.0"})
    verify_ref._contained(work)
    write_json(decl_mod.path_of(work), {"version": decl_mod.READABLE_VERSION, "version_files": ["../victim.json"]})
    with pytest.raises(verify_ref.VerifyEscape, match="victim.json"):
        verify_ref._contained(work)
