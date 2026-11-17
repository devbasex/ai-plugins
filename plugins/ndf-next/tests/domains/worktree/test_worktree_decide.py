"""worktree の判断（純粋な関数）と、設定・一覧の読み取り。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import strategy_fixture

from ndf.adapters.git import parse_worktree_list
from ndf.domains.worktree import cleanup_decide as cd
from ndf.domains.worktree import ndf_settings, guard_decide, repo, session_decide

MAIN = "/r/main"
LOCATION = guard_decide.Location(MAIN, False, ndf_settings.DEFAULT_ALLOW_PATHS, ".worktrees")


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
    assert guard_decide.flagged([target], MAIN, LOCATION) == flagged


def test_guard_skips_inside_worktree_and_dedupes() -> None:
    inside = guard_decide.Location(MAIN, True, (), ".worktrees")
    assert guard_decide.flagged(["src/a.py"], MAIN, inside) == []
    assert guard_decide.flagged(["src/a.py", "./src/a.py"], MAIN, LOCATION) == ["src/a.py"]


def test_guard_empty_allow_list_allows_nothing() -> None:
    strict = guard_decide.Location(MAIN, False, (), ".worktrees")
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


def test_ndf_settings_absent_broken_and_local_overrides(tmp_path: Path) -> None:
    assert ndf_settings.load(tmp_path) is None
    _write(tmp_path, "worktree.json", {"version": 2})
    with pytest.raises(ndf_settings.NdfSettingsError):
        ndf_settings.load(tmp_path)
    _write(tmp_path, "worktree.json", {"version": 1, "guard": {"allow_paths": ["x/"]}})
    _write(tmp_path, "worktree.local.json", {"version": 1, "follow_branch": True, "remote": "other", "testenv": 3})
    got = ndf_settings.load(tmp_path)
    assert got is not None
    assert (got.follow_branch, got.allow_paths) == (True, ("x/",))
    assert got.local_ignored == ("remote", "testenv")
    _write(tmp_path, "worktree.local.json", "{broken")
    broken = ndf_settings.load(tmp_path)
    assert broken is not None and broken.follow_branch is False and len(broken.local_ignored) == 1


@pytest.mark.parametrize("kind", strategy_fixture.KINDS)
def test_ndf_settings_reads_start_and_target_from_the_section(tmp_path: Path, kind: str) -> None:
    """5 種の設定で、スプリントの起点と PR の種類ごとのマージ先が 10 系と同じ値（#3681 の受け入れ条件 1）。"""
    strategy_fixture.write(tmp_path, kind)
    got = ndf_settings.strategy(tmp_path)
    targets = (got.pr_target("sprint"), got.pr_target("doc", strategy_fixture.SPRINT_BRANCH), got.pr_target("release"))
    assert (ndf_settings.sprint_start(tmp_path), *targets) == strategy_fixture.EXPECTED[kind]
    assert ndf_settings.sprint_pr_target(tmp_path) == strategy_fixture.EXPECTED[kind][1]


def test_ndf_settings_strategy_stops_without_section_or_with_legacy_keys(tmp_path: Path) -> None:
    with pytest.raises(ndf_settings.NdfSettingsError, match="ndf project branch-strategy init"):
        ndf_settings.strategy(tmp_path)
    strategy_fixture.write(tmp_path, "docs")
    _write(tmp_path, "worktree.json", {"version": 1, "production_branch": "main"})
    for read in (ndf_settings.load, ndf_settings.strategy):
        with pytest.raises(ndf_settings.NdfSettingsError, match="`/production_branch` は旧い"):
            read(tmp_path)
