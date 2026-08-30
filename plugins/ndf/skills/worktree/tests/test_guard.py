"""tool 実行前の hook の入口を検証する（受け入れ条件 6〜8）。

入口は入力の受け取りと出力の整形だけを行う（詳細設計 06 の決定 8）。ここでは
3 ランタイムの入力の差を吸収できていること、拒否の判定を返さないこと、案内を
出す例と出さない例が対になっていることを確かめる。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from worktree_helpers import GUARD, write_declaration


def run_guard(payload: dict, cwd: Path, session: str | None = None, tmpdir: Path | None = None) -> dict:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    if tmpdir is not None:
        env["TMPDIR"] = str(tmpdir)
    if session is not None:
        env["KIRO_SESSION_ID"] = session
    proc = subprocess.run(
        ["bash", str(GUARD)],
        input=json.dumps(payload),
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    return {"rc": proc.returncode, "out": proc.stdout, "err": proc.stderr}


def context_of(result: dict) -> str:
    if not result["out"].strip():
        return ""
    payload = json.loads(result["out"])
    return payload.get("hookSpecificOutput", {}).get("additionalContext", "")


def declared(main_repo: Path) -> Path:
    write_declaration(main_repo, json.dumps({"version": 1}))
    return main_repo


def claude_edit(path: Path) -> dict:
    return {
        "session_id": "s1",
        "cwd": "",
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(path)},
    }


def test_protected_path_gets_guidance(main_repo: Path) -> None:
    declared(main_repo)
    target = main_repo / "plugins" / "ndf" / "README.md"
    result = run_guard(claude_edit(target), cwd=main_repo)
    assert result["rc"] == 0
    assert "plugins/ndf/README.md" in context_of(result)


def test_guidance_never_denies(main_repo: Path) -> None:
    """拒否の判定を返さない（詳細設計 06 の決定 1）。"""
    declared(main_repo)
    target = main_repo / "plugins" / "ndf" / "README.md"
    result = run_guard(claude_edit(target), cwd=main_repo)
    payload = json.loads(result["out"])
    assert "permissionDecision" not in json.dumps(payload)


def test_allowed_path_is_silent(main_repo: Path) -> None:
    declared(main_repo)
    target = main_repo / "issues" / "note.md"
    result = run_guard(claude_edit(target), cwd=main_repo)
    assert result["out"].strip() == "", result["out"]


def test_inside_worktree_is_silent(main_repo: Path, worktree: Path) -> None:
    declared(main_repo)
    target = worktree / "plugins" / "ndf" / "README.md"
    result = run_guard(claude_edit(target), cwd=worktree)
    assert result["out"].strip() == "", result["out"]


def test_no_declaration_is_silent(main_repo: Path) -> None:
    target = main_repo / "plugins" / "ndf" / "README.md"
    result = run_guard(claude_edit(target), cwd=main_repo)
    assert result["out"].strip() == "", result["out"]


def test_path_outside_main_dir_is_silent(main_repo: Path, tmp_path: Path) -> None:
    declared(main_repo)
    outside = tmp_path / "elsewhere" / "plugins" / "x.md"
    result = run_guard(claude_edit(outside), cwd=main_repo)
    assert result["out"].strip() == "", result["out"]


def test_kiro_fs_write_is_normalized(main_repo: Path) -> None:
    """Kiro CLI は `fs_write` を名乗り、パスは `tool_input.path` に入る。"""
    declared(main_repo)
    payload = {
        "hook_event_name": "preToolUse",
        "cwd": str(main_repo),
        "tool_name": "fs_write",
        "tool_input": {
            "command": "create",
            "path": str(main_repo / "plugins" / "ndf" / "README.md"),
            "file_text": "x",
        },
    }
    result = run_guard(payload, cwd=main_repo)
    assert "plugins/ndf/README.md" in context_of(result)


def test_bash_write_is_detected(main_repo: Path) -> None:
    declared(main_repo)
    payload = {
        "session_id": "s2",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "echo x > plugins/ndf/README.md"},
    }
    result = run_guard(payload, cwd=main_repo)
    assert "plugins/ndf/README.md" in context_of(result)


def test_bash_read_only_is_silent(main_repo: Path) -> None:
    declared(main_repo)
    payload = {
        "session_id": "s3",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "cat plugins/ndf/README.md"},
    }
    result = run_guard(payload, cwd=main_repo)
    assert result["out"].strip() == "", result["out"]


def test_same_path_is_not_repeated(main_repo: Path, tmp_path: Path) -> None:
    """同じパスへの案内をひとつのセッションで繰り返さない。"""
    declared(main_repo)
    state = tmp_path / "state"
    state.mkdir()
    payload = claude_edit(main_repo / "plugins" / "ndf" / "README.md")
    first = run_guard(payload, cwd=main_repo, tmpdir=state)
    second = run_guard(payload, cwd=main_repo, tmpdir=state)
    assert context_of(first) != ""
    assert second["out"].strip() == "", second["out"]


def test_unrelated_tool_is_silent(main_repo: Path) -> None:
    declared(main_repo)
    payload = {
        "session_id": "s4",
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(main_repo / "plugins" / "ndf" / "README.md")},
    }
    result = run_guard(payload, cwd=main_repo)
    assert result["out"].strip() == "", result["out"]


def test_prompt_submit_writes_plain_stdout(main_repo: Path) -> None:
    """Kiro CLI 向けの経路。パスを見ない案内を標準出力へ書く。"""
    declared(main_repo)
    payload = {"hook_event_name": "userPromptSubmit", "cwd": str(main_repo)}
    result = run_guard(payload, cwd=main_repo)
    assert result["rc"] == 0
    assert ".worktrees/" in result["out"]


def test_prompt_submit_is_silent_inside_worktree(main_repo: Path, worktree: Path) -> None:
    declared(main_repo)
    payload = {"hook_event_name": "userPromptSubmit", "cwd": str(worktree)}
    result = run_guard(payload, cwd=worktree)
    assert result["out"].strip() == "", result["out"]


def test_broken_stdin_does_not_fail(main_repo: Path) -> None:
    """入力が壊れていても作業を止めない。"""
    proc = subprocess.run(
        ["bash", str(GUARD)],
        input="not json",
        cwd=str(main_repo),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_gemini_replace_is_normalized(main_repo: Path) -> None:
    """Gemini CLI の部分書き換えツールは `replace` を名乗る。"""
    declared(main_repo)
    payload = {
        "session_id": "g1",
        "hook_event_name": "PreToolUse",
        "tool_name": "replace",
        "tool_input": {"file_path": str(main_repo / "plugins" / "ndf" / "README.md")},
    }
    result = run_guard(payload, cwd=main_repo)
    assert "plugins/ndf/README.md" in context_of(result)


def test_gemini_run_shell_command_is_normalized(main_repo: Path) -> None:
    """Gemini CLI のシェル実行ツールは `run_shell_command` を名乗る。"""
    declared(main_repo)
    payload = {
        "session_id": "g2",
        "hook_event_name": "PreToolUse",
        "tool_name": "run_shell_command",
        "tool_input": {"command": "echo x > plugins/ndf/README.md"},
    }
    result = run_guard(payload, cwd=main_repo)
    assert "plugins/ndf/README.md" in context_of(result)


def test_codex_apply_patch_is_normalized(main_repo: Path) -> None:
    """Codex CLI は `apply_patch` の本文でパスを渡す（実機で確認した形）。"""
    declared(main_repo)
    payload = {
        "session_id": "c1",
        "cwd": str(main_repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch",
        "tool_input": {
            "command": (
                "*** Begin Patch\n"
                "*** Update File: plugins/ndf/README.md\n"
                "@@\n-# sample\n+# sample edited\n"
                "*** End Patch\n"
            )
        },
    }
    result = run_guard(payload, cwd=main_repo)
    assert "plugins/ndf/README.md" in context_of(result)


def test_codex_apply_patch_on_allowed_path_is_silent(main_repo: Path) -> None:
    declared(main_repo)
    payload = {
        "session_id": "c2",
        "cwd": str(main_repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch",
        "tool_input": {
            "command": "*** Begin Patch\n*** Update File: issues/note.md\n@@\n+x\n*** End Patch\n"
        },
    }
    result = run_guard(payload, cwd=main_repo)
    assert result["out"].strip() == "", result["out"]
