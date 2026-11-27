"""#1959: worker の再開の呼び出し・起動の設定の指定と承認・読むだけの相手役の引数。"""

from __future__ import annotations

from pathlib import Path

import json

import pytest

from ndf.adapters import agents, claude, launch_settings
from ndf.contracts import internal, worker_paths
from ndf.core import worker
from ndf.core.converge import participants


@pytest.fixture(autouse=True)
def _no_plugin_list(monkeypatch):
    monkeypatch.setattr(claude, "list_plugins", lambda **_: [])


def test_worker_resumes_with_the_same_full_arguments_and_only_the_session_changes(tmp_path: Path) -> None:
    """worker は最初の起動と再開で同じ full の引数を使い、会話の指定だけが `--session-id` から `--resume` へ変わる。"""
    h = worker.Handoff("fix", "直す", tmp_path / "result.json", tmp_path, tmp_path / "state")
    calls: list[tuple[list[str], Path | None]] = []

    def fake(argv, prompt, timeout, cwd, options):
        calls.append((list(argv), cwd))
        assert options.verify is None  # 指定の無いリポジトリは照らし直しを置かない
        if len(calls) == 2:
            h.result_file.write_text('{"status": "ok"}', encoding="utf-8")
        return claude.ClaudeReply(True, None, "終えた", 0.0)

    with worker_paths.open_workdir(tmp_path) as wd:
        done = worker.delegate(h, "規則", root=tmp_path, wd=wd, call=fake, session="s-1")

    full = claude.claude_args(claude.ClaudeCall("規則", claude.Permission.FULL), tmp_path, cwd=tmp_path)
    assert done.cause is None and [cwd for _, cwd in calls] == [tmp_path, tmp_path]
    assert calls[0][0] == [*full, "--session-id", "s-1"]
    assert calls[1][0] == [*full, "--resume", "s-1"]


def _repo_with_mcp(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".ndf" / "mcp").mkdir(parents=True)
    (root / ".ndf" / "launch.json").write_text(json.dumps({"version": 1, "permissions": {"steps": {"fix": {"mcp": [".ndf/mcp/a.json"]}}}}))
    (root / ".ndf" / "mcp" / "a.json").write_text(json.dumps({"mcpServers": {"search": {"command": "search-mcp"}}}))
    monkeypatch.setattr(launch_settings.launch_approval, "repo_key", lambda r: str(r))
    return root


def test_an_unapproved_spec_stops_the_worker_before_any_launch(tmp_path: Path, monkeypatch) -> None:
    """条件 11: 作業種別の指定が未承認なら、worker を 1 度も起動せずに precondition で止まる。前の結果ファイルは消さない。"""
    root = _repo_with_mcp(tmp_path, monkeypatch)
    h = worker.Handoff("fix", "直す", tmp_path / "result.json", root, tmp_path / "state")
    h.result_file.write_text('{"status": "ok"}', encoding="utf-8")
    calls: list[list[str]] = []
    with worker_paths.open_workdir(tmp_path) as wd, pytest.raises(internal.InternalError) as got:
        worker.delegate(h, "規則", root=tmp_path, wd=wd, call=lambda argv, *a: calls.append(argv))
    assert got.value.kind == "precondition" and calls == [] and h.result_file.exists()


def test_an_approved_spec_launches_with_the_copy_and_checks_again_before_each_resume(tmp_path: Path, monkeypatch) -> None:
    """条件 5・11: 承認した指定の MCP の定義はコピーを渡し、再開を含む子ごとに照らし直しを渡す。確かめた後の書き換えは次の子で見つかる。"""
    root = _repo_with_mcp(tmp_path, monkeypatch)
    sha = launch_settings.approval_target(launch_settings.load(root)).sha256
    launch_settings.launch_approval.append(str(root), sha, launch_settings.launch_approval.Action.APPROVE, approver="人")
    h = worker.Handoff("fix", "直す", tmp_path / "result.json", root, tmp_path / "state")
    seen: list[tuple[list[str], str | None]] = []

    def fake(argv, prompt, timeout, cwd, options):
        seen.append((list(argv), options.verify()))
        copy = Path(argv[argv.index("--mcp-config") + 1])
        assert copy.exists() and copy != root / ".ndf" / "mcp" / "a.json"
        (root / ".ndf" / "mcp" / "a.json").write_text(json.dumps({"mcpServers": {}}))  # 1 回目の子の後に書き換える
        return claude.ClaudeReply(True, None, "終えた", 0.0)

    with worker_paths.open_workdir(tmp_path) as wd:
        worker.delegate(h, "規則", root=tmp_path, wd=wd, call=fake, session="s-1")
    assert seen[0][1] is None and seen[1][1] is not None and "--strict-mcp-config" in seen[0][0]
    assert not Path(seen[0][0][seen[0][0].index("--mcp-config") + 1]).exists()


@pytest.mark.parametrize("stop", ["revoke", "old_cli"])
def test_a_launch_stopped_by_a_check_before_it_keeps_the_previous_result(tmp_path: Path, monkeypatch, stop: str) -> None:
    """起動の前の確かめで止まる（待つ間の承認の取り消し・古い CLI）と、子は起動せず、前の結果ファイルを残す。今回の結果とは見なさない。"""
    root = _repo_with_mcp(tmp_path, monkeypatch)
    sha = launch_settings.approval_target(launch_settings.load(root)).sha256
    launch_settings.launch_approval.append(str(root), sha, launch_settings.launch_approval.Action.APPROVE, approver="人")
    h = worker.Handoff("fix", "直す", tmp_path / "result.json", root, tmp_path / "state")
    h.result_file.write_text('{"status": "ok"}', encoding="utf-8")
    launched: list[list[str]] = []

    def fake(argv, prompt, timeout, cwd, options):
        if stop == "revoke":
            launch_settings.launch_approval.append(str(root), sha, launch_settings.launch_approval.Action.REVOKE)  # 起動のスロットを待つ間
        reason = options.verify()
        if reason is not None:
            return claude.ClaudeReply.failed("", reason, failure=claude.Failure.LAUNCH)
        if stop == "old_cli":  # 照らし直しの後の CLI の下限で止まる（`claude._precheck` は起動の準備 `ready` を打たない）
            return claude.ClaudeReply.failed("", "claude が古い", failure=claude.Failure.REFUSED)
        options.ready()
        launched.append(list(argv))
        return claude.ClaudeReply(True, None, "終えた", 0.0)

    with worker_paths.open_workdir(tmp_path) as wd:
        done = worker.delegate(h, "規則", root=tmp_path, wd=wd, call=fake, session="s-1")
    assert launched == [] and done.cause is not None and h.result_file.exists()


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
