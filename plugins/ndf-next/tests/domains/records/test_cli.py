"""サブコマンド `ndf records` の結果 JSON（表の 1 行・閉じる条件・引継ぎ文書）。"""

from __future__ import annotations

import io
import json
from pathlib import Path

import git_sandbox
import pytest

from ndf import cli
from ndf.contracts import result
from ndf.domains.records import cli as records_cli


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    root = tmp_path / "r"
    git_sandbox.sh(tmp_path, "init", "-q", str(root))
    return root


def _run(capsys: pytest.CaptureFixture[str], last_result, *argv: str) -> tuple[int, dict]:
    code = cli.main(["records", *argv])
    got = last_result(capsys.readouterr().out)
    assert result.errors(got, code) == []
    return code, got


def test_records_is_in_the_command_table() -> None:
    assert cli.tool_of(records_cli.__name__) == "records"


def test_close_check_refuses_an_issue_with_a_failed_verification(repo: Path, capsys, last_result) -> None:
    rows = repo / "verification.json"
    record = {"version": "1.2.3", "issues": [{"number": 7, "result": "failed", "evidence": "落ちた"}]}
    rows.write_text(json.dumps(record), encoding="utf-8")
    code, got = _run(capsys, last_result, "--cwd", str(repo), "close-check", "7", "--verification", str(rows))
    assert code != 0 and got["status"] == "stopped" and got["items"][0]["closable"] is False
    code, got = _run(capsys, last_result, "--cwd", str(repo), "close-check", "8", "--verification", str(rows))
    assert code == 0 and got["items"][0]["closable"] is True
    rows.write_text(json.dumps([{"issue": 7, "result": "failed"}]), encoding="utf-8")
    code, got = _run(capsys, last_result, "--cwd", str(repo), "close-check", "7", "--verification", str(rows))
    assert code == 3 and got["cause"]["kind"] == "precondition"


def test_handoff_check_reports_missing_paths(repo: Path, capsys, last_result) -> None:
    doc = repo / "handoff.md"
    doc.write_text("## 現在地\n\n`docs/none.md` を作成済み。\n", encoding="utf-8")
    code, got = _run(capsys, last_result, "--cwd", str(repo), "handoff", "check", str(doc))
    assert code != 0 and {"missing_path": "docs/none.md"} in got["items"]
    (repo / "docs").mkdir()
    (repo / "docs" / "none.md").write_text("x", encoding="utf-8")
    code, got = _run(capsys, last_result, "--cwd", str(repo), "handoff", "check", str(doc))
    assert code == 0 and got["metrics"]["lines"] == 3


def test_handoff_check_reads_relative_paths_from_the_target_repo(
    repo: Path, capsys, last_result, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / "handoff.md").write_text("## 現在地\n\n- 対象\n\n## 古いセクション\n\n- 移す\n", encoding="utf-8")
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "handoff.md").write_text("## 現在地\n\n- 呼び出し元\n", encoding="utf-8")
    monkeypatch.chdir(caller)
    code, got = _run(capsys, last_result, "--cwd", str(repo), "handoff", "check", "handoff.md", "--trim", "--history", "h.md")
    assert code == 0 and {"moved": "## 古いセクション"} in got["items"]
    assert (repo / "h.md").is_file() and not (caller / "h.md").exists()
    assert (caller / "handoff.md").read_text(encoding="utf-8") == "## 現在地\n\n- 呼び出し元\n"


def test_usage_stop_never_fails_the_hook(repo: Path, capsys, last_result, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("壊れた入力"))
    code, got = _run(capsys, last_result, "--cwd", str(repo), "usage", "stop")
    assert code == 0 and got["items"] == []


# 現状固定（I-017 の前）: `ndf records mvv signals / answer`（cli._mvv）の結果 JSON
@pytest.fixture
def mvv_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
    origin, main = tmp_path / "origin.git", tmp_path / "main"
    git_sandbox.sh(tmp_path, "init", "-q", "--bare", str(origin))
    git_sandbox.sh(tmp_path, "init", "-q", "-b", "main", str(main))
    (main / ".ndf").mkdir()
    decl = {"body": "mvv.md", "versions": [{"version": 1, "sha256": "s"}], "settings": {"revise_after": {"overrides": 1}}}
    (main / ".ndf" / "mvv.json").write_text(json.dumps(decl), encoding="utf-8")
    git_sandbox.sh(main, "add", "-A")
    git_sandbox.sh(main, "commit", "-q", "-m", "main")
    git_sandbox.sh(main, "remote", "add", "origin", str(origin))
    git_sandbox.sh(main, "push", "-q", "origin", "main")
    git_sandbox.sh(main, "fetch", "-q", "origin")
    return main


def _answer(monkeypatch: pytest.MonkeyPatch, capsys, last_result, root: Path, stdin: str, *argv: str) -> tuple[int, dict]:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return _run(capsys, last_result, "--cwd", str(root), "mvv", "answer", *argv)


def test_mvv_answer_refuses_a_non_object_input(mvv_repo: Path, capsys, last_result, monkeypatch: pytest.MonkeyPatch) -> None:
    for stdin in ("壊れた", "[1]", ""):
        code, got = _answer(monkeypatch, capsys, last_result, mvv_repo, stdin)
        assert code != 0 and got["cause"]["kind"] == "invalid" and got["items"] == []
        assert got["summary"] == "承認ゲートの回答（標準入力の JSON object）が無い"


def test_mvv_answer_writes_only_an_override_and_signals_count_it(
    mvv_repo: Path, capsys, last_result, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, got = _answer(monkeypatch, capsys, last_result, mvv_repo, json.dumps({"verdict": "follow", "approved": True}))
    assert code == 0 and got["status"] == "ok" and got["summary"] == "覆しではないので書かなかった" and got["items"] == []
    code, got = _run(capsys, last_result, "--cwd", str(mvv_repo), "mvv", "signals", "--base", "main")
    assert code == 0 and got["summary"] == "MVV の改訂の兆候は閾値に届かない" and got["metrics"] == {"revision_due": False}
    assert got["items"] == [{"version": 1, "overrides": 0, "unknown_pass": 0, "unknown": 0}]
    answer = {"verdict": "not_follow", "approved": True, "state": "s", "gate": "g", "pr": 3, "extra": "x"}
    code, got = _answer(monkeypatch, capsys, last_result, mvv_repo, json.dumps(answer), "--version", "1")
    row = got["items"][0]
    assert code == 0 and got["summary"] == f"覆しの記録: {row['kind']}"
    assert {k: row[k] for k in ("version", "repo", "state", "gate", "pr")} == {
        "version": 1,
        "repo": str(mvv_repo.resolve()),
        "state": "s",
        "gate": "g",
        "pr": 3,
    }
    assert "extra" not in row
    code, got = _run(capsys, last_result, "--cwd", str(mvv_repo), "mvv", "signals", "--base", "main")
    assert code == 0 and got["summary"] == "MVV の改訂を提案する兆候がある" and got["metrics"] == {"revision_due": True}
    assert got["items"] == [{"version": 1, "overrides": 1, "unknown_pass": 0, "unknown": 0}]


def test_mvv_signals_stops_when_the_base_branch_has_no_ndf_settings(mvv_repo: Path, capsys, last_result) -> None:
    code, got = _run(capsys, last_result, "--cwd", str(mvv_repo), "mvv", "signals", "--base", "none")
    assert code == 3 and got["status"] == "stopped" and got["cause"]["kind"] == "precondition"
    assert got["summary"].startswith("origin/none の MVV の設定を読めない: ")
