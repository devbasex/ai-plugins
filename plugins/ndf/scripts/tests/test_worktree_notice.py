"""hook_lib/worktree.py の notice の現状固定テスト（I-005）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
from hook_lib import worktree as wtg  # noqa: E402
from hook_lib.payload import event_of  # noqa: E402


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("TMPDIR", str(state_dir))
    monkeypatch.setattr(wtg, "session_owner", lambda: None)
    return os.path.realpath(main)


def _declare(main: str, decl: dict | None = None) -> None:
    d = Path(main) / ".ndf"
    d.mkdir(exist_ok=True)
    (d / "worktree.json").write_text(json.dumps(decl or {"version": 1}))


def _edit(cwd: str, path: str, session: str = "s1", **extra) -> object:
    raw = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": path}, "cwd": cwd, "session_id": session}
    raw.update(extra)
    return event_of(raw)


def _expected(paths: list[str]) -> dict:
    body = "\n".join(f"  - {p}" for p in paths)
    return {
        "systemMessage": wtg.SUMMARY,
        "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": wtg.CONTEXT.format(paths=body, wdir=wtg.WORKTREE_DIR)},
    }


def test_without_declaration_nothing_is_said(repo):
    assert wtg.notice(_edit(repo, f"{repo}/src/a.py")) is None


def test_outside_a_repository_nothing_is_said(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    outside = tmp_path / "plain"
    outside.mkdir()
    assert wtg.notice(_edit(str(outside), f"{outside}/a.py")) is None


def test_editing_the_main_directory_gives_the_notice_once_per_session(repo):
    _declare(repo)
    assert wtg.notice(_edit(repo, f"{repo}/src/a.py")) == _expected(["src/a.py"])
    assert wtg.notice(_edit(repo, f"{repo}/src/a.py")) is None
    assert wtg.notice(_edit(repo, "src/b.py")) == _expected(["src/b.py"])
    state = json.loads(Path(wtg.state_file("s1")).read_text())
    assert state["notified"] == ["src/a.py", "src/b.py"]


def test_without_session_the_notice_repeats(repo):
    _declare(repo)
    assert wtg.notice(_edit(repo, f"{repo}/src/a.py", session="")) == _expected(["src/a.py"])
    assert wtg.notice(_edit(repo, f"{repo}/src/a.py", session="")) == _expected(["src/a.py"])


@pytest.mark.parametrize("rel", ["docs/x.md", "issues/1.md", ".gitignore", ".worktrees/feat/a.py", "."])
def test_allowed_and_worktree_paths_are_quiet(repo, rel):
    _declare(repo)
    assert wtg.notice(_edit(repo, f"{repo}/{rel}" if rel != "." else repo)) is None


def test_paths_outside_the_main_directory_are_quiet(repo, tmp_path):
    _declare(repo)
    assert wtg.notice(_edit(repo, str(tmp_path / "elsewhere.py"))) is None


def test_declared_allow_paths_replace_the_default(repo):
    _declare(repo, {"version": 1, "guard": {"allow_paths": ["src/"]}})
    assert wtg.notice(_edit(repo, f"{repo}/src/a.py")) is None
    assert wtg.notice(_edit(repo, f"{repo}/docs/a.md")) == _expected(["docs/a.md"])


def test_unsupported_declaration_version_is_quiet(repo):
    _declare(repo, {"version": 2})
    assert wtg.notice(_edit(repo, f"{repo}/src/a.py")) is None


def test_inside_a_worktree_nothing_is_said(repo):
    _declare(repo)
    wt = Path(repo) / ".worktrees" / "feat"
    _git(Path(repo), "worktree", "add", "-q", "-b", "feat", str(wt))
    wt_dir = os.path.realpath(wt)
    assert wtg.notice(_edit(wt_dir, f"{wt_dir}/src/a.py")) is None


@pytest.mark.parametrize("name", ["userPromptSubmit", "UserPromptSubmit"])
def test_prompt_submit_returns_the_kiro_notice(repo, name):
    _declare(repo)
    ev = event_of({"hook_event_name": name, "cwd": repo, "session_id": "k"})
    assert wtg.notice(ev) == wtg.KIRO_NOTICE


def test_untargeted_tool_is_quiet(repo):
    _declare(repo)
    ev = event_of(
        {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": f"{repo}/a.py"}, "cwd": repo, "session_id": "s"}
    )
    assert wtg.notice(ev) is None


def test_multiple_edit_paths_are_listed_and_only_new_ones_after(repo):
    _declare(repo)
    ev = event_of(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "MultiEdit",
            "tool_input": {"file_path": f"{repo}/b.py", "edits": [{"file_path": f"{repo}/a.py"}]},
            "cwd": repo,
            "session_id": "m",
        }
    )
    assert wtg.notice(ev) == _expected(["a.py", "b.py"])
    ev2 = event_of(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "MultiEdit",
            "tool_input": {"file_path": f"{repo}/b.py", "edits": [{"file_path": f"{repo}/c.py"}]},
            "cwd": repo,
            "session_id": "m",
        }
    )
    assert wtg.notice(ev2) == _expected(["c.py"])


def test_agy_queues_the_context_and_allows(repo):
    _declare(repo)
    raw = {
        "toolCall": {"name": "write_to_file", "args": {"TargetFile": f"{repo}/src/a.py"}},
        "workspacePaths": [repo],
        "conversationId": "g1",
    }
    assert wtg.notice(event_of(raw)) == {"decision": "allow"}
    state = json.loads(Path(wtg.state_file("g1")).read_text())
    assert state["pending"] == [wtg.CONTEXT.format(paths="  - src/a.py", wdir=wtg.WORKTREE_DIR)]
    assert state["notified"] == ["src/a.py"]
    assert wtg.notice(event_of(raw)) is None


def test_patch_targets_are_flagged(repo):
    _declare(repo)
    patch = "*** Begin Patch\n*** Add File: src/new.py\n+x\n*** End Patch\n"
    ev = event_of(
        {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "tool_input": {"command": patch}, "cwd": repo, "session_id": "p"}
    )
    assert wtg.notice(ev) == _expected(["src/new.py"])


def test_empty_patch_is_quiet(repo):
    _declare(repo)
    ev = event_of({"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "tool_input": {}, "cwd": repo, "session_id": "p"})
    assert wtg.notice(ev) is None


class TestShell:
    @pytest.fixture(autouse=True)
    def _need_parser(self):
        pytest.importorskip("tree_sitter_bash")

    def _bash(self, repo: str, cmd: str, session: str = "b", **ti) -> object:
        return event_of(
            {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd, **ti}, "cwd": repo, "session_id": session}
        )

    def test_redirect_into_the_main_directory_is_flagged(self, repo):
        _declare(repo)
        assert wtg.notice(self._bash(repo, "echo hi > src/out.txt")) == _expected(["src/out.txt"])

    def test_read_only_command_is_quiet(self, repo):
        _declare(repo)
        assert wtg.notice(self._bash(repo, "ls -la")) is None

    def test_empty_command_is_quiet(self, repo):
        _declare(repo)
        assert wtg.notice(self._bash(repo, "")) is None

    def test_unreadable_command_gives_the_unread_notice(self, repo):
        _declare(repo)
        out = wtg.notice(self._bash(repo, "echo $(( "))
        assert out == {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": wtg.UNREAD}}
