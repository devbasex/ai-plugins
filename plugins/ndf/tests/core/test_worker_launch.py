"""#1959 Task 1: 起動の設定を足す前の、worker の再開の呼び出しと読むだけの相手役の引数を固定する。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.adapters import agents, claude
from ndf.contracts import worker_paths
from ndf.core import worker
from ndf.core.converge import participants


@pytest.fixture(autouse=True)
def _no_plugin_list(monkeypatch):
    monkeypatch.setattr(claude, "list_plugins", lambda **_: [])


def test_worker_resumes_with_the_same_full_arguments_and_only_the_session_changes(tmp_path: Path) -> None:
    """worker は最初の起動と再開で同じ full の引数を使い、会話の指定だけが `--session-id` から `--resume` へ変わる。"""
    h = worker.Handoff("fix", "直す", tmp_path / "result.json", tmp_path, tmp_path / "state")
    calls: list[tuple[list[str], Path | None]] = []

    def fake(argv, prompt, timeout, cwd):
        calls.append((list(argv), cwd))
        if len(calls) == 2:
            h.result_file.write_text('{"status": "ok"}', encoding="utf-8")
        return claude.ClaudeReply(True, None, "終えた", 0.0)

    with worker_paths.open_workdir(tmp_path) as wd:
        done = worker.delegate(h, "規則", root=tmp_path, wd=wd, call=fake, session="s-1")

    full = claude.claude_args(claude.ClaudeCall("規則", claude.Permission.FULL), tmp_path, cwd=tmp_path)
    assert done.cause is None and [cwd for _, cwd in calls] == [tmp_path, tmp_path]
    assert calls[0][0] == [*full, "--session-id", "s-1"]
    assert calls[1][0] == [*full, "--resume", "s-1"]


@pytest.mark.parametrize(
    ("agent", "namespaces", "expected"),
    [
        ("codex", True, ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "-C", "{wt}", "質問"]),
        (
            "codex",
            False,
            ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "--dangerously-bypass-approvals-and-sandbox", "-C", "{wt}", "質問"],
        ),
        ("kiro", True, ["kiro-cli", "chat", "--no-interactive", "--trust-tools=fs_read", "質問"]),
    ],
)
def test_read_only_partner_arguments_are_unchanged(tmp_path: Path, monkeypatch, agent, namespaces, expected) -> None:
    """読むだけの相手役の今の引数。codex は名前空間を作れなければ承認なしで安全装置を外す（Task 6 が承認つきに変える）。"""
    monkeypatch.setattr(agents, "user_namespaces", lambda: namespaces)
    argv = participants.review_launch(agent, tmp_path, "質問", tmp_path / "round1").argv
    assert list(argv) == [a.replace("{wt}", str(tmp_path)) for a in expected]


def test_read_only_claude_partner_is_a_minimal_launch_with_read_tools(tmp_path: Path) -> None:
    """claude の読むだけの担当は最小構成（利用者の設定と MCP を読まない）で、読む Tool だけを許す。"""
    argv = participants.read_only_launch("sys", tmp_path, "読む", tmp_path / "s", 30.0).argv
    assert list(argv[1:8]) == ["-p", "--output-format", "json", "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]
    assert argv[argv.index("--tools") + 1] == participants.READ_TOOLS == argv[argv.index("--allowed-tools") + 1]
    assert argv[-1] == "読む"
