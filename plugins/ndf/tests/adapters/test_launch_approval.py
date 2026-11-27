"""#1959: 起動の承認のハッシュ・リポジトリのキー・承認の記録と、NDF が起動した AI の子のマーカー。"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from ndf import identity
from ndf.adapters import agents, claude, claude_precheck, git, launch_approval as la

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


def test_a_revoke_retried_after_a_torn_write_takes_effect() -> None:
    """中断で改行の無い書きかけが残っても、打ち直した取り消しは次の行として分かれて反映される。"""
    path = la.state_file(la.APPROVALS)
    la.append("/r", SHA, la.Action.APPROVE, approver="人")
    with path.open("a", encoding="utf-8") as f:
        f.write('{"repo": "/r", "sha256": "' + SHA + '", "act')
    la.append("/r", SHA, la.Action.REVOKE)
    assert not la.is_approved("/r", SHA)


def test_a_row_torn_inside_a_multibyte_character_does_not_hide_the_other_rows() -> None:
    """日本語の承認者名の途中で切れた行があっても、前後の行とほかのリポジトリの承認は読める。"""
    path = la.state_file(la.APPROVALS)
    la.append("/a", SHA, la.Action.APPROVE, approver="人")
    with path.open("ab") as f:
        f.write('{"repo": "/c", "approver": "承'.encode("utf-8")[:-1])
    la.append("/b", SHA, la.Action.APPROVE, approver="承認者")
    assert la.is_approved("/a", SHA) and la.is_approved("/b", SHA)


def test_a_complete_row_left_without_its_newline_stays_ineffective_after_another_append() -> None:
    """JSON を書き終えて改行の前で中断した取り消しは、別のリポジトリの追記の後も反映されない（打ち直すまで承認は有効）。"""
    path = la.state_file(la.APPROVALS)
    la.append("/a", SHA, la.Action.APPROVE, approver="人")
    with path.open("a", encoding="utf-8") as f:
        f.write('{"repo": "/a", "sha256": "' + SHA + '", "action": "revoke"}')
    la.append("/b", SHA, la.Action.APPROVE, approver="人")
    assert la.is_approved("/a", SHA) and la.is_approved("/b", SHA)


def test_a_record_file_readable_by_others_is_neither_written_nor_trusted() -> None:
    """既存の記録の権限が広ければ追記せず（`PermissionError`）、読む側も承認済みと扱わない。"""
    path = la.state_file(la.APPROVALS)
    la.append("/r", SHA, la.Action.APPROVE, approver="人")
    before = path.read_bytes()
    path.chmod(0o644)
    assert not la.is_approved("/r", SHA)
    with pytest.raises(PermissionError):
        la.append("/r", SHA, la.Action.REVOKE)
    assert path.read_bytes() == before
    path.chmod(0o600)
    assert la.is_approved("/r", SHA)


# ---------------------------------------------------------------- NDF が起動した AI の子のマーカー（承認を書けない）


class DroppingChecks:
    """偽の中継の確かめ。`child: drop` の指定に従い、確定した環境からマーカーを除く（実プロセスを起動しない）。"""

    @staticmethod
    def _decided(argv, env):
        return claude.AccountRun({k: v for k, v in env.items() if k != identity.env_name("ai-child")}, list(argv))

    def begin(self, argv, env, cwd, keys):
        return self._decided(argv, env)

    def retry(self, argv, env, cwd, keys):
        return self._decided(argv, env)


@pytest.mark.parametrize("relayed", [False, True])
def test_claude_children_carry_the_ai_child_flag_in_the_final_environment(monkeypatch, relayed) -> None:
    """中継を通らない起動、中継経由の初回の起動、上限で替え先へ打ち直す起動のどれも、子の最終の環境にマーカーがある。"""
    from contextlib import contextmanager

    @contextmanager
    def slot(agent, *, cwd=None, env=None, wait=None, ensure=True):
        yield {}

    launched: list[dict] = []

    def run(argv, prompt, timeout, cwd, env, new_group):
        launched.append(dict(env))
        if relayed and len(launched) == 1:
            return claude.ClaudeReply.failed("", "上限", failure=claude.Failure.USAGE_LIMIT)
        return claude.ClaudeReply(True, None, "", 0.0)

    monkeypatch.setattr(claude.launch_slot, "gate", slot)
    monkeypatch.setattr(claude_precheck.cli_floor, "require", lambda *a, **k: None)
    monkeypatch.setattr(claude, "_run_claude", run)
    monkeypatch.setattr(claude, "relay_checks", DroppingChecks() if relayed else None)
    monkeypatch.setenv("NDF_CLAUDE_ACCOUNT", "work1")
    monkeypatch.setenv(identity.env_name("relay-dir"), "/tmp/relay")
    claude.call_claude(["claude", "-p"], "質問", 1.0)
    assert len(launched) == (2 if relayed else 1)
    assert all(env[identity.env_name("ai-child")] == "1" for env in launched)


def test_partner_children_carry_the_ai_child_flag(tmp_path: Path, monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(agents.subprocess, "Popen", lambda *a, **k: seen.append(k["env"]))
    spec = agents.Launch("codex", ("codex", "exec"), tmp_path, agents.AgentPaths.at(tmp_path / "out"), 1.0, env={"X": "y"})
    agents._start(spec)
    assert seen[0][identity.env_name("ai-child")] == "1" and seen[0]["X"] == "y"


def test_repo_key_asks_git_once_per_root_and_does_not_keep_a_failure(tmp_path, monkeypatch) -> None:
    """#2944: 値の選択と承認の確かめが同じ根のキーを求めても、git は 1 回だけ。解けなかった根は覚えない。"""
    asked: list[Path] = []

    def common_dir(self):
        asked.append(self.cwd)
        if len(asked) == 1:
            raise git.GitError("git の外")
        return tmp_path / ".git"

    monkeypatch.setattr(git.Git, "common_dir", common_dir)
    assert la.repo_key(tmp_path) == os.path.realpath(tmp_path)
    assert la.repo_key(tmp_path) == la.repo_key(tmp_path) == str(tmp_path / ".git") and len(asked) == 2
