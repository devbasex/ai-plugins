"""worktree の判断（純粋な関数）と、宣言・一覧の読み取り。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.adapters.git import parse_worktree_list
from ndf.domains.worktree import cleanup_decide as cd
from ndf.domains.worktree import declaration, guard_decide, repo, session_decide

MAIN = "/r/main"
PLACE = guard_decide.Place(MAIN, False, declaration.DEFAULT_ALLOW_PATHS, ".worktrees")


@pytest.mark.parametrize(
    ("target", "flagged"),
    [
        ("src/a.py", ["src/a.py"]),
        ("issues/x.md", []),
        ("docs", []),
        (".gitignore", []),
        (".gitignore.bak", [".gitignore.bak"]),
        (".worktrees/feat/a.py", []),
        ("/elsewhere/a.py", []),
        (".", []),
        # 字面で畳まないと <メイン>/ で始まって見える
        ("a/../../outside.py", []),
        ("issues/../src/b.py", ["src/b.py"]),
    ],
)
def test_guard_flags_main_dir_writes_outside_allowed(target: str, flagged: list[str]) -> None:
    assert guard_decide.flagged([target], MAIN, PLACE) == flagged


def test_guard_skips_inside_worktree_and_dedupes() -> None:
    inside = guard_decide.Place(MAIN, True, (), ".worktrees")
    assert guard_decide.flagged(["src/a.py"], MAIN, inside) == []
    assert guard_decide.flagged(["src/a.py", "./src/a.py"], MAIN, PLACE) == ["src/a.py"]


def test_guard_empty_allow_list_allows_nothing() -> None:
    strict = guard_decide.Place(MAIN, False, (), ".worktrees")
    assert guard_decide.flagged(["issues/x.md"], MAIN, strict) == ["issues/x.md"]


def test_guard_notice_lists_paths_and_rounds_the_rest() -> None:
    paths = [f"src/{i}.py" for i in range(guard_decide.MAX_LISTED + 2)]
    got = guard_decide.notice(paths, ".worktrees")
    assert got is not None
    assert "ほか 2 件" in got.context
    assert guard_decide.notice([], ".worktrees") is None
    unread = guard_decide.notice(["src/a.py"], ".worktrees", unread=True)
    assert unread is not None and "src/a.py" not in unread.context


def test_session_deviation() -> None:
    dirty = [f" M f{i}" for i in range(session_decide.MAX_LISTED + 1)]
    text = session_decide.deviation(dirty, in_worktree=False, declared=True, follow_branch=True)
    assert text is not None
    assert f"{len(dirty)} 件" in text and "ほか 1 件" in text and session_decide.FOLLOW_NOTE in text
    assert session_decide.deviation(dirty, in_worktree=True, declared=True) is None
    assert session_decide.deviation(dirty, in_worktree=False, declared=False) is None
    assert session_decide.deviation([], in_worktree=False, declared=True) is None


MERGED, OPEN, CLOSED = cd.PullState("closed", True), cd.PullState("open", False), cd.PullState("closed", False)


@pytest.mark.parametrize(
    ("facts", "verdict"),
    [
        (cd.Facts(None, False, False, False, (MERGED,)), cd.KEEP_DETACHED),
        (cd.Facts("b", True, False, False, (MERGED,)), cd.KEEP_LOCKED),
        (cd.Facts("b", False, True, False, (MERGED,)), cd.KEEP_CURRENT),
        (cd.Facts("b", False, False, True, (MERGED,)), cd.KEEP_DIRTY),
        (cd.Facts("b", False, False, False, ()), cd.KEEP_NO_PR),
        (cd.Facts("b", False, False, False, (OPEN, MERGED)), cd.KEEP_OPEN),
        (cd.Facts("b", False, False, False, (CLOSED,)), cd.KEEP_UNMERGED),
        (cd.Facts("b", False, False, False, (CLOSED, MERGED)), cd.REMOVE),
    ],
)
def test_cleanup_decide(facts: cd.Facts, verdict: str) -> None:
    assert cd.decide(facts) == verdict
    assert verdict in cd.REASONS


def test_parse_porcelain() -> None:
    text = (
        "worktree /r/main\0HEAD aaa\0branch refs/heads/develop\0\0"
        "worktree /r/main/.worktrees/x\0HEAD bbb\0detached\0locked\0\0"
        "worktree /r/main/.worktrees/y\0HEAD ccc\0branch refs/heads/feat/y\0prunable gone\0\0"
    )
    got = parse_worktree_list(text)
    assert [(w.path, w.branch, w.locked, w.prunable) for w in got] == [
        (Path("/r/main"), "develop", False, False),
        (Path("/r/main/.worktrees/x"), None, True, False),
        (Path("/r/main/.worktrees/y"), "feat/y", False, True),
    ]
    dev = repo.dev_worktrees(Path("/r/main"), [*got, repo.Worktree(Path("/r/main/.worktrees/.ndf/s"), "c", "s")])
    assert [w.path.name for w in dev] == ["x", "y"]


def _write(main: Path, name: str, data: object) -> None:
    (main / ".ndf").mkdir(exist_ok=True)
    (main / ".ndf" / name).write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")


def test_declaration_absent_broken_and_local_overrides(tmp_path: Path) -> None:
    assert declaration.load(tmp_path) is None
    _write(tmp_path, "worktree.json", {"version": 2})
    with pytest.raises(declaration.DeclarationError):
        declaration.load(tmp_path)
    _write(tmp_path, "worktree.json", {"version": 1, "base_branch": "dev", "guard": {"allow_paths": ["x/"]}})
    _write(tmp_path, "worktree.local.json", {"version": 1, "follow_branch": True, "base_branch": "other", "testenv": 3})
    got = declaration.load(tmp_path)
    assert got is not None
    assert (got.base_branch, got.follow_branch, got.allow_paths) == ("dev", True, ("x/",))
    assert got.local_ignored == ("base_branch", "testenv")
    _write(tmp_path, "worktree.local.json", "{broken")
    broken = declaration.load(tmp_path)
    assert broken is not None and broken.follow_branch is False and len(broken.local_ignored) == 1


def test_declaration_start_channel() -> None:
    assert declaration.Declaration({"version": 1}).start_channel == "base"
    assert declaration.Declaration({"branch_flow": {"start": "production"}}).start_channel == "production"
    with pytest.raises(declaration.DeclarationError):
        _ = declaration.Declaration({"branch_flow": {"start": "main"}}).start_channel
