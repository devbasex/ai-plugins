"""#1959: 起動の承認のハッシュ・リポジトリのキー・承認の記録と、NDF が起動した AI の子のマーカー。"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from ndf import identity
from ndf.adapters import agents, claude, git, launch_approval as la

SHA = "a" * 64


def test_the_digest_ignores_key_order_and_follows_the_content() -> None:
    files = {".ndf/mcp/a.json": {"mcpServers": {"x": {"command": "x", "args": ["1"]}}}}
    same = {".ndf/mcp/a.json": {"mcpServers": {"x": {"args": ["1"], "command": "x"}}}}
    perm = {"steps": {"*": {"load": ["LSP"]}}, "unsandboxed": ["codex"]}
    assert la.target_digest(perm, files) == la.target_digest(dict(reversed(perm.items())), same)
    assert la.target_digest(perm, files) != la.target_digest(perm, {".ndf/mcp/a.json": {"mcpServers": {}}})
    assert la.target_digest(perm, files) != la.target_digest({**perm, "unsandboxed": []}, files)


def test_the_repository_key_is_the_git_common_dir_and_outside_git_the_directory(tmp_path: Path, monkeypatch) -> None:
    """リンクされた worktree は同じ共通ディレクトリを返すので同じキー。git の外（`GitError`）はディレクトリの実体。"""
    common = tmp_path / "main" / ".git"
    asked: list[Path | None] = []

    def common_dir(self):
        asked.append(self.cwd)
        if self.cwd == tmp_path / "plain":
            raise git.GitError("not a git repository")
        return common

    monkeypatch.setattr(git.Git, "common_dir", common_dir)
    assert la.repo_key(tmp_path / "main") == la.repo_key(tmp_path / "linked") == str(common)
    assert la.repo_key(tmp_path / "plain") == os.path.realpath(tmp_path / "plain")
    assert asked == [tmp_path / "main", tmp_path / "linked", tmp_path / "plain"]


def test_the_last_row_of_the_same_repository_and_hash_decides() -> None:
    assert not la.is_approved("/r", SHA)
    la.append("/r", SHA, la.Action.APPROVE, approver="人")
    assert la.is_approved("/r", SHA)
    assert not la.is_approved("/other", SHA) and not la.is_approved("/r", "b" * 64)
    la.append("/r", SHA, la.Action.REVOKE, approver="人", request_id="q-1")
    assert not la.is_approved("/r", SHA)
    la.append("/r", SHA, la.Action.APPROVE, request_id="q-2", approver="人")
    assert la.is_approved("/r", SHA)


def test_rows_carry_the_approver_only_on_approval_and_the_file_is_private() -> None:
    row = la.append("/r", SHA, la.Action.APPROVE, approver="人", request_id="q-1")
    gone = la.append("/r", SHA, la.Action.REVOKE, approver="人", request_id="q-1")
    assert {k: row[k] for k in ("repo", "sha256", "action", "approver", "request_id")} == {
        "repo": "/r", "sha256": SHA, "action": "approve", "approver": "人", "request_id": "q-1",
    }  # fmt: skip
    assert set(gone) == {"at", "repo", "sha256", "action"}
    path = la.state_file(la.APPROVALS)
    assert path.parent == identity.load().state_dir and stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        la.append("/r", SHA, "other")


def test_a_half_written_or_broken_row_is_skipped_and_an_unreadable_file_is_not_approved() -> None:
    path = la.state_file(la.APPROVALS)
    la.append("/r", SHA, la.Action.APPROVE, approver="人")
    with path.open("a", encoding="utf-8") as f:
        f.write('{壊れた\n{"repo": "/r", "sha256": "' + SHA + '", "action": "revoke"}')
    assert la.is_approved("/r", SHA)
    path.write_bytes(b"\xff\xfe\n")
    assert not la.is_approved("/r", SHA)


# ---------------------------------------------------------------- NDF が起動した AI の子のマーカー（承認を書けない）


def test_claude_children_carry_the_ai_child_flag(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(claude, "_prefaced", lambda base, *a: seen.append(base.env) or claude.ClaudeReply(True, None, "", 0.0))
    monkeypatch.setattr(claude, "relay_checks", None)
    claude.call_claude(["claude", "-p"], "質問", 1.0)
    assert seen[0][identity.env_name("ai-child")] == "1"


def test_partner_children_carry_the_ai_child_flag(tmp_path: Path, monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(agents.subprocess, "Popen", lambda *a, **k: seen.append(k["env"]))
    spec = agents.Launch("codex", ("codex", "exec"), tmp_path, agents.AgentPaths.at(tmp_path / "out"), 1.0, env={"X": "y"})
    agents._start(spec)
    assert seen[0][identity.env_name("ai-child")] == "1" and seen[0]["X"] == "y"
