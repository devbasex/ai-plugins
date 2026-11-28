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


# モデルか effort を指定する引数（#2944 の Task 1 で値を選ぶ処理を入れる前の退行を縛る）
_CHOICE_FLAGS = {"--model", "--effort", "-m", "--config"}


def _has_choice(argv) -> bool:
    """モデルか effort を指定する引数を持つか（claude の `--model`・`--effort`、codex の `-m`・`-c model_reasoning_effort=…`）。"""
    words = list(argv)
    return bool(_CHOICE_FLAGS & set(words)) or any("model_reasoning_effort" in w for w in words)


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
    assert not any(_has_choice(argv) for argv, _ in calls)  # #2944 の Task 1: モデルと effort を指定しない


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
        if stop == "old_cli":  # 照らし直しの後の CLI の下限で止まる（`claude_precheck.precheck_claude` は起動の準備 `ready` を打たない）
            return claude.ClaudeReply.failed("", "claude が古い", failure=claude.Failure.REFUSED)
        options.ready()
        launched.append(list(argv))
        return claude.ClaudeReply(True, None, "終えた", 0.0)

    with worker_paths.open_workdir(tmp_path) as wd:
        done = worker.delegate(h, "規則", root=tmp_path, wd=wd, call=fake, session="s-1")
    assert launched == [] and done.cause is not None and h.result_file.exists()


class Rows:
    """差し込む偽の使用量ログの書き込み処理。"""

    def __init__(self) -> None:
        self.rows: list[tuple[object, dict]] = []

    def record_cli(self, usage, meta):
        self.rows.append((usage, dict(meta)))


def _ran(reply: claude.ClaudeReply) -> claude.ClaudeReply:
    return reply._replace(launched=True, seconds=1.0, launched_at="2026-11-27T01:00:00+00:00")


def test_each_child_of_a_resumed_worker_writes_one_row_in_order(tmp_path: Path, monkeypatch) -> None:
    """条件 3・E4: 再開した worker は子の数だけ行が増え、会話の中の番号が 0 から進む。JSON の無い失敗の子も使用量なしの行になる。"""
    rows = Rows()
    monkeypatch.setattr(claude, "launch_recorder", rows)
    monkeypatch.setenv("NDF_PLAN_RUN_ID", "run-1")
    monkeypatch.setenv("NDF_STEP_ID", "implement")
    monkeypatch.setenv("NDF_STEP_ATTEMPT", "2")
    h = worker.Handoff("fix", "直す", tmp_path / "result.json", tmp_path, tmp_path / "state")
    logs: list[claude.LaunchLog] = []

    def fake(argv, prompt, timeout, cwd, options):
        """`call_claude` と同じく、起動した子が終わったその場で行を `options.log` へ渡す。"""
        logs.append(options.log)
        if len(logs) == 1:
            reply = _ran(claude.ClaudeReply.failed("出力なし", "claude が終了コード 1 で終えた"))
        else:
            h.result_file.write_text('{"status": "ok"}', encoding="utf-8")
            reply = _ran(claude.parse_reply('{"result": "{}", "usage": {"output_tokens": 3}, "total_cost_usd": 0.2}'))
        options.log.report(reply)
        return reply

    with worker_paths.open_workdir(tmp_path) as wd:
        done = worker.delegate(h, "規則", root=tmp_path, wd=wd, call=fake, session="s-1")

    assert done.cause is None and len(rows.rows) == 2 and logs[0] is logs[1]  # 再開しても同じ記録先が番号を進める
    (u0, r0), (u1, r1) = rows.rows
    assert u0 is None and u1 == {"output_tokens": 3}
    assert [r0["session_seq"], r1["session_seq"]] == [0, 1] and r0["session_id"] == r1["session_id"] == "s-1"
    assert r1["name"] == "fix" and (r1["run_id"], r1["step"], r1["attempt"]) == ("run-1", "implement", 2)
    assert "repo" not in r1  # リポジトリ名を得るためだけに git を起動しない
    assert r1["session_cost_usd"] == 0.2 and "直す" not in str(r1)  # 行に質問の本文を書かない


def test_a_worker_child_that_did_not_launch_writes_no_row(tmp_path: Path, monkeypatch) -> None:
    """起動しなかった子（スロット・下限・照らし直しで止まった）は行を書かない。"""
    rows = Rows()
    monkeypatch.setattr(claude, "launch_recorder", rows)
    h = worker.Handoff("fix", "直す", tmp_path / "result.json", tmp_path, tmp_path / "state")

    def fake(argv, prompt, timeout, cwd, options):
        reply = claude.ClaudeReply.failed("", "空かない", failure=claude.Failure.NO_SLOT)
        options.log.report(reply)
        return reply

    with worker_paths.open_workdir(tmp_path) as wd:
        done = worker.delegate(h, "規則", root=tmp_path, wd=wd, call=fake, session="s-1")
    assert done.cause is not None and rows.rows == []


@pytest.mark.parametrize(
    ("agent", "namespaces", "expected"),
    [
        ("codex", True, ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "-C", "{wt}", "質問"]),
        ("kiro", False, ["kiro-cli", "chat", "--no-interactive", "--trust-tools=fs_read", "質問"]),
    ],
)
def test_read_only_partner_arguments_are_unchanged(tmp_path: Path, monkeypatch, agent, namespaces, expected) -> None:
    """読むだけの相手役の引数。kiro は名前空間の有無に関わらず読むだけの指定（安全装置を外す codex は test_converge が見る）。"""
    monkeypatch.setattr(agents, "user_namespaces", lambda: namespaces)
    argv = participants.review_launch(agent, tmp_path, "質問", tmp_path / "round1").argv
    assert list(argv) == [a.replace("{wt}", str(tmp_path)) for a in expected]


def test_read_only_claude_partner_is_a_minimal_launch_with_read_tools(tmp_path: Path) -> None:
    """claude の読むだけの担当は最小構成（利用者の設定と MCP を読まない）で、読む Tool だけを許す。"""
    argv = participants.read_only_launch("sys", tmp_path, "読む", tmp_path / "s", 30.0).argv
    assert list(argv[1:8]) == ["-p", "--output-format", "json", "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]
    assert argv[argv.index("--tools") + 1] == participants.READ_TOOLS == argv[argv.index("--allowed-tools") + 1]
    assert argv[-1] == "読む"


# 退行を縛る（#2944 の Task 1）: 値を選ぶ処理を入れる前の、worker と cross-refactoring の起動の引数


@pytest.mark.parametrize("kind", ["design", "impl", "worktree", "release"])
def test_worker_launches_and_resumes_without_model_or_effort(tmp_path: Path, kind: str) -> None:
    """fix 以外の作業の worker も、最初の起動と再開にモデルと effort を指定しない（fix は再開のテストが見る）。"""
    h = worker.Handoff(kind, "作業", tmp_path / "result.json", tmp_path, tmp_path / "state")
    calls: list[list[str]] = []

    def fake(argv, prompt, timeout, cwd, options):
        calls.append(list(argv))
        if len(calls) == 2:
            h.result_file.write_text('{"status": "ok"}', encoding="utf-8")
        return claude.ClaudeReply(True, None, "終えた", 0.0)

    with worker_paths.open_workdir(tmp_path) as wd:
        worker.delegate(h, "規則", root=tmp_path, wd=wd, call=fake, session="s-1")

    assert len(calls) == 2 and not any(_has_choice(argv) for argv in calls)


def test_cross_refactoring_launches_are_unchanged(tmp_path: Path, monkeypatch) -> None:
    """cross-refactoring の担当の起動（読むだけの codex・claude と、コードを書く claude）の引数を固定する。"""
    from ndf.domains.crossrefactor import launch, launch_decide

    monkeypatch.setattr(agents, "user_namespaces", lambda: True)
    codex = launch._read_only(launch.Request("propose", "codex", "質問", "pr1", "proposals", 30.0), tmp_path, tmp_path / "s")
    reader = launch._read_only(launch.Request("propose", participants.FIXER, "質問", "pr1", "proposals", 30.0), tmp_path, tmp_path / "s")
    where = launch_decide.result_location("implement", participants.FIXER, "i1", (tmp_path / "read", tmp_path, tmp_path / "logs"))
    coder = launch._coding(launch.Request("implement", participants.FIXER, "直す", "i1", timeout=30.0), tmp_path, tmp_path / "s", where)

    assert list(codex.argv) == ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "-C", str(tmp_path), "質問"]
    system = f"{launch_decide.SKILL} の propose の担当"
    want = participants.read_only_launch(system, tmp_path, "質問", tmp_path / "s", 30.0).argv
    assert reader.argv == want
    assert not any(_has_choice(x.argv) for x in (codex, reader, coder))
