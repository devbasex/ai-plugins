"""#1142 の C4: cross-refactoring の定義が、設計の決めたモジュールに 1 つずつある。

- `refactor_lib/gitfacts.py` はコミットの事実だけを定義し、分けた先（6 本）の名前を再エクスポートする
- `drive.py` の `call`・`parse_vars`・`review_status` は `lib/loop_drive.py` のもの
- `refactor_lib/clock.py` の時刻の関数は `lib/clock.py` のもの
- worktree の置き場の slug は `lib/repo.py` のもの
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
LIB = SKILL.parents[1] / "scripts" / "lib"

# 分けた先のモジュール → そこで定義する名前（issue-1142-design-modules.md の表）
OWNERS = {
    "pathkinds": ("is_test_path", "_has_shebang", "_is_code_path", "production_code_changes",
                  "TEST_PATH_MARKERS", "TEST_NAME_MARKERS", "CODE_EXTENSIONS"),
    "process": ("run_with_timeout", "_process_group_alive", "_kill_process_group", "run_test_at"),
    "github": ("resolved_threads_on_github", "_fetch_review_threads_page", "_REVIEW_THREADS_QUERY",
               "check_run_result", "_gh_api_get"),
    "worktree": ("revert_item_commits", "reset_hard", "revert_range", "replay_commits",
                 "_order_newest_first", "_worktree_changes", "_control_prefix", "_dirty_paths",
                 "_discard_worktree_changes", "discard_impl_leftovers", "_require_clean_worktree"),
    "publish": ("_sync_generated", "_run_sync_command", "_write_plan_file", "_publish_commit_message",
                "_commit_sync_changes", "push_head", "push_with_retry_marker", "flush_pending_push",
                "_push_with_credential_fallback", "credential_fallback_args", "gh_available",
                "_CREDENTIAL_LIB"),
    "results": ("read_result", "note_stopped", "STOPPED_REASONS", "record_observed_model"),
}

GITFACTS_OWN = ("safe_int", "reported_shas", "commits_in_range", "commit_trailers",
                "_parse_trailer_paragraph", "commit_diff_lines", "commit_files", "commit_test_changes",
                "tracked_markdown", "commit_touches_tests", "commit_time", "collect_commit_facts")


@pytest.mark.parametrize("module, name", [(m, n) for m, names in OWNERS.items() for n in names])
def test_a_moved_name_is_defined_in_its_owner_and_re_exported(refactor, module, name):
    owner = sys.modules[f"refactor_lib.{module}"]
    gitfacts = sys.modules["refactor_lib.gitfacts"]
    assert name in vars(owner)
    assert getattr(gitfacts, name) is getattr(owner, name)
    value = getattr(owner, name)
    if callable(value) and hasattr(value, "__module__"):
        assert value.__module__ == f"refactor_lib.{module}"


@pytest.mark.parametrize("name", GITFACTS_OWN)
def test_gitfacts_keeps_the_commit_facts(refactor, name):
    gitfacts = sys.modules["refactor_lib.gitfacts"]
    assert getattr(gitfacts, name).__module__ == "refactor_lib.gitfacts"


def test_no_owner_imports_gitfacts(refactor):
    """移した先は gitfacts を import しない（循環しない）。"""
    for module in OWNERS:
        owner = sys.modules[f"refactor_lib.{module}"]
        assert "gitfacts" not in vars(owner), module


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_drive_uses_the_loop_drive_library():
    rf = _load("rf_drive_owners", SKILL / "scripts" / "drive.py")
    for name in ("call", "parse_vars", "review_status"):
        assert getattr(rf, name).__module__ == "loop_drive", name


@pytest.mark.parametrize("name", ("now", "iso", "parse", "seconds_between"))
def test_the_clock_is_the_library_clock(refactor, name):
    clock = sys.modules["refactor_lib.clock"]
    assert getattr(clock, name).__module__ == "clock"


def test_the_clock_keeps_the_offset_of_the_state_file(refactor):
    """状態ファイルへ書く形はタイムゾーン付きで秒まで。読むと同じ時刻に戻る。"""
    clock = sys.modules["refactor_lib.clock"]
    now = clock.now()
    text = clock.iso(now)
    assert clock.parse(text) == now.replace(microsecond=0)
    assert clock.parse("2026-09-26T07:00:00Z").utcoffset().total_seconds() == 0


def test_the_repo_slug_is_the_library_slug(refactor):
    setup = sys.modules["refactor_lib.commands.setup"]
    assert "repo_slug" not in vars(sys.modules["refactor_lib.paths"])
    assert setup.repo_lib.slug("o/r") == "o--r"
