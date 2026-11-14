"""`ndf worktree` の副命令が結果 JSON を 1 行出す。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from ndf import cli
from ndf.contracts import result


def _run(capsys: pytest.CaptureFixture[str], last_result: Callable, *argv: str) -> tuple[int, dict]:
    code = cli.main(["worktree", *argv])
    out = last_result(capsys.readouterr().out)
    assert result.errors(out) == []
    return code, out


def test_create_outputs_context_keys(main_repo: Path, capsys: pytest.CaptureFixture[str], last_result: Callable) -> None:
    code, out = _run(capsys, last_result, "--cwd", str(main_repo), "create", "feat/a")
    assert code == 0 and out["status"] == result.OK
    assert out["outputs"] == {"worktree": str(main_repo.resolve() / ".worktrees" / "feat" / "a"), "branch": "feat/a"}
    code, out = _run(capsys, last_result, "--cwd", str(main_repo), "list")
    assert out["metrics"] == {"count": 1}


def test_create_refusal_is_stopped(main_repo: Path, capsys: pytest.CaptureFixture[str], last_result: Callable) -> None:
    code, out = _run(capsys, last_result, "--cwd", str(main_repo), "create", "bad..name")
    assert code != 0 and out["cause"]["kind"] == "invalid"


def test_outside_repository_is_precondition(tmp_path: Path, capsys: pytest.CaptureFixture[str], last_result: Callable) -> None:
    code, out = _run(capsys, last_result, "--cwd", str(tmp_path), "list")
    assert out["cause"]["kind"] == "precondition"


def test_guard_and_session(main_repo: Path, capsys: pytest.CaptureFixture[str], last_result: Callable) -> None:
    code, out = _run(capsys, last_result, "--cwd", str(main_repo), "guard", "src/a.py", "issues/x.md")
    assert code == 0 and {"path": "src/a.py"} in out["items"] and {"path": "issues/x.md"} not in out["items"]
    _, out = _run(capsys, last_result, "--cwd", str(main_repo), "guard", "issues/x.md")
    assert out["items"] == []
    _, out = _run(capsys, last_result, "--cwd", str(main_repo), "session")
    assert out["items"] == [] and out["metrics"] == {"dirty": 0}
    (main_repo / ".gitignore").write_text(".worktrees/\nx\n", encoding="utf-8")
    _, out = _run(capsys, last_result, "--cwd", str(main_repo), "session")
    assert out["metrics"] == {"dirty": 1} and "1 件" in out["items"][0]["context"]


def test_guard_inside_worktree_is_silent(main_repo: Path, capsys: pytest.CaptureFixture[str], last_result: Callable) -> None:
    _run(capsys, last_result, "--cwd", str(main_repo), "create", "feat/a")
    wt = main_repo / ".worktrees" / "feat" / "a"
    _, out = _run(capsys, last_result, "--cwd", str(wt), "guard", "--unread", str(main_repo / "src" / "a.py"))
    assert out["items"] == []


def test_broken_declaration_is_precondition(main_repo: Path, capsys: pytest.CaptureFixture[str], last_result: Callable) -> None:
    (main_repo / ".ndf" / "worktree.json").write_text("{", encoding="utf-8")
    _, out = _run(capsys, last_result, "--cwd", str(main_repo), "session")
    assert out["cause"]["kind"] == "precondition"
