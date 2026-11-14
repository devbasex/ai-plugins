"""副命令 `ndf records` の結果 JSON（表の 1 行・閉じる条件・引継ぎ文書）。"""

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
    rows.write_text(json.dumps([{"issue": 7, "result": "failed"}]), encoding="utf-8")
    code, got = _run(capsys, last_result, "--cwd", str(repo), "close-check", "7", "--verification", str(rows))
    assert code != 0 and got["status"] == "stopped" and got["items"][0]["closable"] is False
    code, got = _run(capsys, last_result, "--cwd", str(repo), "close-check", "8", "--verification", str(rows))
    assert code == 0 and got["items"][0]["closable"] is True


def test_handoff_check_reports_missing_paths(repo: Path, capsys, last_result) -> None:
    doc = repo / "handoff.md"
    doc.write_text("## 現在地\n\n`docs/none.md` を作成済み。\n", encoding="utf-8")
    code, got = _run(capsys, last_result, "--cwd", str(repo), "handoff", "check", str(doc))
    assert code != 0 and {"missing_path": "docs/none.md"} in got["items"]
    (repo / "docs").mkdir()
    (repo / "docs" / "none.md").write_text("x", encoding="utf-8")
    code, got = _run(capsys, last_result, "--cwd", str(repo), "handoff", "check", str(doc))
    assert code == 0 and got["metrics"]["lines"] == 3


def test_usage_stop_never_fails_the_hook(repo: Path, capsys, last_result, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("壊れた入力"))
    code, got = _run(capsys, last_result, "--cwd", str(repo), "usage", "stop")
    assert code == 0 and got["items"] == []
